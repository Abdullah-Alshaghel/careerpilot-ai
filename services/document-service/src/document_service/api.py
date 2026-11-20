"""Document routes. Every query is scoped by the caller's verified user id;
another user's document is indistinguishable from a missing one (404)."""

from __future__ import annotations

import hashlib
import logging
import time
import uuid
from collections.abc import AsyncIterator
from typing import Annotated

from fastapi import APIRouter, Depends, File, Request, Response, UploadFile, status
from sqlalchemy import delete, func, select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from careerpilot_common.auth import AccessTokenClaims, require_user
from careerpilot_common.errors import ServiceError
from careerpilot_common.messaging import enqueue_event, new_event
from document_service.filetypes import RejectedFile, detect_type, safe_filename
from document_service.models import Document, outbox
from document_service.schemas import DocumentDeletedV1, DocumentList, DocumentOut
from document_service.settings import DocumentSettings
from document_service.storage import ObjectStore, original_key

log = logging.getLogger("document_service.api")

_UPLOAD_CHUNK = 64 * 1024


async def get_db(request: Request) -> AsyncIterator[AsyncSession]:
    async with request.app.state.sessionmaker() as session:
        yield session


def get_store(request: Request) -> ObjectStore:
    return request.app.state.store


def get_settings(request: Request) -> DocumentSettings:
    return request.app.state.settings


current_user = require_user(lambda request: request.app.state.jwks)

Db = Annotated[AsyncSession, Depends(get_db)]
Store = Annotated[ObjectStore, Depends(get_store)]
Settings = Annotated[DocumentSettings, Depends(get_settings)]
User = Annotated[AccessTokenClaims, Depends(current_user)]


async def _read_upload(file: UploadFile, limit: int) -> bytes:
    chunks, size = [], 0
    while chunk := await file.read(_UPLOAD_CHUNK):
        size += len(chunk)
        if size > limit:
            raise ServiceError(413, "file_too_large", f"Files must be {limit // (1024 * 1024)} MB or smaller")
        chunks.append(chunk)
    return b"".join(chunks)


async def _owned(db: AsyncSession, user: AccessTokenClaims, document_id: uuid.UUID) -> Document:
    document = await db.scalar(select(Document).where(Document.id == document_id, Document.user_id == user.user_id))
    if document is None:
        raise ServiceError(404, "not_found", "Document not found")
    return document


def build_router() -> APIRouter:
    router = APIRouter(prefix="/v1/documents", tags=["documents"])

    @router.post("", status_code=status.HTTP_201_CREATED)
    async def upload(
        user: User,
        db: Db,
        store: Store,
        settings: Settings,
        response: Response,
        file: Annotated[UploadFile, File(description="PDF, DOCX or UTF-8 text")],
    ) -> DocumentOut:
        started = time.perf_counter()
        data = await _read_upload(file, settings.max_upload_bytes)
        try:
            detected = detect_type(data)
        except RejectedFile as exc:
            status_code = 422 if exc.code == "empty_file" else 415
            raise ServiceError(status_code, exc.code, exc.message) from exc

        checksum = hashlib.sha256(data).hexdigest()
        existing = await db.scalar(
            select(Document).where(Document.user_id == user.user_id, Document.checksum == checksum)
        )
        if existing is not None:
            response.status_code = status.HTTP_200_OK  # same file uploaded again
            return DocumentOut.model_validate(existing)

        count = await db.scalar(select(func.count()).select_from(Document).where(Document.user_id == user.user_id))
        if (count or 0) >= settings.max_documents_per_user:
            raise ServiceError(409, "document_limit", "Document limit reached. Delete a document first")

        document_id = uuid.uuid4()
        key = original_key(user.user_id, document_id)
        stored_at = time.perf_counter()
        try:
            await store.put(key, data, detected.mime_type, checksum)
        except Exception as exc:
            log.error("object storage write failed", extra={"error_type": type(exc).__name__})
            raise ServiceError(503, "storage_unavailable", "Upload failed. Try again shortly") from exc

        document = Document(
            id=document_id,
            user_id=user.user_id,
            filename=safe_filename(file.filename, detected),
            mime_type=detected.mime_type,
            size_bytes=len(data),
            checksum=checksum,
            object_key=key,
        )
        db.add(document)
        try:
            await db.commit()
        except IntegrityError:
            # Lost a race with an identical concurrent upload: keep theirs.
            await db.rollback()
            await store.delete_prefix(key)
            existing = await db.scalar(
                select(Document).where(Document.user_id == user.user_id, Document.checksum == checksum)
            )
            if existing is None:
                raise
            response.status_code = status.HTTP_200_OK
            return DocumentOut.model_validate(existing)
        await db.refresh(document)
        # Step timings make a slow upload diagnosable from the logs alone.
        log.info(
            "document uploaded",
            extra={
                "document_id": str(document.id),
                "mime_type": document.mime_type,
                "size_bytes": document.size_bytes,
                "prepare_ms": round((stored_at - started) * 1000),
                "total_ms": round((time.perf_counter() - started) * 1000),
            },
        )
        return DocumentOut.model_validate(document)

    @router.get("")
    async def list_documents(user: User, db: Db) -> DocumentList:
        rows = await db.scalars(
            select(Document).where(Document.user_id == user.user_id).order_by(Document.created_at.desc())
        )
        return DocumentList(documents=[DocumentOut.model_validate(d) for d in rows])

    @router.get("/{document_id}")
    async def get_document(document_id: uuid.UUID, user: User, db: Db) -> DocumentOut:
        return DocumentOut.model_validate(await _owned(db, user, document_id))

    @router.delete("/{document_id}", status_code=status.HTTP_204_NO_CONTENT)
    async def delete_document(document_id: uuid.UUID, user: User, db: Db, store: Store) -> Response:
        document = await _owned(db, user, document_id)
        prefix = document.object_key.rsplit("/", 1)[0] + "/"
        await db.execute(delete(Document).where(Document.id == document.id))
        # Same transaction: the profile service learns about it exactly when it happens.
        await enqueue_event(
            db,
            outbox,
            new_event(
                "document.deleted",
                aggregate_id=str(document.id),
                user_id=user.user_id,
                producer="document-service",
                payload=DocumentDeletedV1(document_id=document.id).model_dump(mode="json"),
            ),
        )
        await db.commit()
        try:
            await store.delete_prefix(prefix)
        except Exception as exc:
            # Metadata is gone, so the file is unreachable; log for cleanup.
            log.error(
                "object cleanup failed", extra={"document_id": str(document.id), "error_type": type(exc).__name__}
            )
        return Response(status_code=status.HTTP_204_NO_CONTENT)

    return router
