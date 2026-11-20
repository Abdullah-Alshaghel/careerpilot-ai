"""Extraction worker: ``python -m document_service.worker``.

Loop: claim one uploaded document (``FOR UPDATE SKIP LOCKED``, so replicas
never collide), extract its text in an isolated child process, store the text,
and record the result plus a ``document.text_extracted`` event in one
transaction. Between documents it relays the outbox to RabbitMQ.
"""

from __future__ import annotations

import asyncio
import logging
import signal
import time
from datetime import datetime, timedelta, timezone
from pathlib import Path

from sqlalchemy import or_, select, update
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

from careerpilot_common.logging import configure_logging
from careerpilot_common.messaging import EventPublisher, enqueue_event, new_event, relay_outbox
from document_service.extraction import PARSER_VERSION, ExtractionError, extract_isolated
from document_service.models import Document, DocumentExtraction, DocumentStatus, ExtractionStatus, outbox
from document_service.schemas import DocumentTextExtractedV1
from document_service.settings import DocumentSettings
from document_service.storage import ObjectStore, extracted_text_key

log = logging.getLogger("document_service.worker")


async def claim_next(sessionmaker: async_sessionmaker[AsyncSession], settings: DocumentSettings) -> Document | None:
    stale = datetime.now(timezone.utc) - timedelta(seconds=settings.stale_processing_seconds)
    async with sessionmaker() as session, session.begin():
        # Out of attempts: give up visibly instead of showing "processing" forever.
        await session.execute(
            update(Document)
            .where(
                Document.status == DocumentStatus.PROCESSING,
                Document.processing_started_at < stale,
                Document.attempts >= settings.max_extraction_attempts,
            )
            .values(status=DocumentStatus.FAILED, failure_reason="processing_failed")
        )
        document = await session.scalar(
            select(Document)
            .where(
                or_(
                    Document.status == DocumentStatus.UPLOADED,
                    # A worker died mid-way: retry, but not forever.
                    (Document.status == DocumentStatus.PROCESSING)
                    & (Document.processing_started_at < stale)
                    & (Document.attempts < settings.max_extraction_attempts),
                )
            )
            .order_by(Document.created_at)
            .limit(1)
            .with_for_update(skip_locked=True)
        )
        if document is None:
            return None
        document.status = DocumentStatus.PROCESSING
        document.processing_started_at = datetime.now(timezone.utc)
        document.attempts += 1
    return document


async def process(
    document: Document,
    sessionmaker: async_sessionmaker[AsyncSession],
    store: ObjectStore,
    settings: DocumentSettings,
    extract=extract_isolated,
) -> DocumentStatus:
    started = time.perf_counter()
    try:
        data = await store.get(document.object_key)
        result = await asyncio.to_thread(
            extract,
            data,
            document.mime_type,
            timeout=settings.extraction_timeout_seconds,
            memory_limit_mb=settings.extraction_memory_limit_mb,
            max_pages=settings.max_pdf_pages,
            max_chars=settings.max_extracted_chars,
        )
    except ExtractionError as exc:
        return await _record_failure(document, sessionmaker, exc.code)

    text_key = extracted_text_key(document.user_id, document.id, PARSER_VERSION)
    await store.put(text_key, result.text.encode("utf-8"), "text/plain; charset=utf-8")

    async with sessionmaker() as session, session.begin():
        current = await session.get(Document, document.id, with_for_update=True)
        if current is None:
            return DocumentStatus.FAILED  # deleted while we worked; the delete cleans up storage
        current.status = DocumentStatus.EXTRACTED
        current.failure_reason = None
        session.add(
            DocumentExtraction(
                document_id=current.id,
                extracted_text_ref=text_key,
                parser_version=PARSER_VERSION,
                status=ExtractionStatus.SUCCEEDED,
                char_count=len(result.text),
                page_count=result.page_count,
            )
        )
        await enqueue_event(
            session,
            outbox,
            new_event(
                "document.text_extracted",
                aggregate_id=str(current.id),
                user_id=current.user_id,
                producer="document-service",
                payload=DocumentTextExtractedV1(
                    document_id=current.id,
                    filename=current.filename,
                    mime_type=current.mime_type,
                    parser_version=PARSER_VERSION,
                    document_text=result.text,
                    truncated=result.truncated,
                ).model_dump(mode="json"),
            ),
        )
    log.info(
        "document extracted",
        extra={
            "document_id": str(document.id),
            "char_count": len(result.text),
            "duration_ms": round((time.perf_counter() - started) * 1000),
        },
    )
    return DocumentStatus.EXTRACTED


async def _record_failure(
    document: Document, sessionmaker: async_sessionmaker[AsyncSession], code: str
) -> DocumentStatus:
    async with sessionmaker() as session, session.begin():
        current = await session.get(Document, document.id, with_for_update=True)
        if current is None:
            return DocumentStatus.FAILED
        current.status = DocumentStatus.FAILED
        current.failure_reason = code
        session.add(
            DocumentExtraction(document_id=current.id, parser_version=PARSER_VERSION, status=ExtractionStatus.FAILED)
        )
    log.warning("document extraction failed", extra={"document_id": str(document.id), "reason": code})
    return DocumentStatus.FAILED


async def run(settings: DocumentSettings, stop: asyncio.Event) -> None:
    engine = create_async_engine(settings.database_url.get_secret_value(), pool_size=2, pool_pre_ping=True)
    sessionmaker = async_sessionmaker(engine, expire_on_commit=False)
    store = ObjectStore(settings)
    await store.ensure_bucket()
    publisher = EventPublisher(settings.rabbitmq_url.get_secret_value())
    heartbeat = Path(settings.worker_heartbeat_file)
    log.info("extraction worker started")
    try:
        while not stop.is_set():
            await asyncio.to_thread(heartbeat.touch)
            try:
                await relay_outbox(sessionmaker, outbox, publisher.publish)
                document = await claim_next(sessionmaker, settings)
                if document is not None:
                    try:
                        await process(document, sessionmaker, store, settings)
                    except Exception:
                        # Storage/DB trouble: leave it "processing"; the stale rule retries it.
                        log.exception("document processing interrupted", extra={"document_id": str(document.id)})
                    continue
            except Exception:
                log.exception("worker iteration failed")
            try:
                await asyncio.wait_for(stop.wait(), timeout=settings.worker_poll_seconds)
            except asyncio.TimeoutError:
                pass
    finally:
        await publisher.close()
        await engine.dispose()


def main() -> None:
    settings = DocumentSettings()
    configure_logging(f"{settings.service_name}-worker", settings.environment, settings.log_level)
    stop = asyncio.Event()

    async def runner() -> None:
        loop = asyncio.get_running_loop()
        for sig in (signal.SIGTERM, signal.SIGINT):
            try:
                loop.add_signal_handler(sig, stop.set)
            except NotImplementedError:  # Windows
                pass
        await run(settings, stop)

    asyncio.run(runner())


if __name__ == "__main__":
    main()
