"""Private S3-compatible object storage for uploaded files and extracted text."""

from __future__ import annotations

import asyncio
import uuid

import boto3
from botocore.config import Config
from botocore.exceptions import ClientError

from document_service.settings import DocumentSettings


def original_key(user_id: uuid.UUID, document_id: uuid.UUID) -> str:
    # Per-user prefix; the uploaded filename never appears in a key.
    return f"users/{user_id}/documents/{document_id}/original"


def extracted_text_key(user_id: uuid.UUID, document_id: uuid.UUID, parser_version: str) -> str:
    return f"users/{user_id}/documents/{document_id}/extracted-{parser_version}.txt"


class ObjectStore:
    """Thin async wrapper around boto3 (calls run in worker threads)."""

    def __init__(self, settings: DocumentSettings, client: object | None = None) -> None:
        self.bucket = settings.s3_bucket
        self._client = client or boto3.client(
            "s3",
            endpoint_url=settings.s3_endpoint_url,
            region_name=settings.s3_region,
            aws_access_key_id=settings.s3_access_key.get_secret_value(),
            aws_secret_access_key=settings.s3_secret_key.get_secret_value(),
            config=Config(
                signature_version="s3v4",
                s3={"addressing_style": "path"},
                retries={"max_attempts": 3, "mode": "standard"},
                connect_timeout=5,
                read_timeout=30,
            ),
        )

    async def ensure_bucket(self) -> None:
        def ensure() -> None:
            try:
                self._client.head_bucket(Bucket=self.bucket)  # type: ignore[attr-defined]
            except ClientError as exc:
                if exc.response.get("Error", {}).get("Code") not in {"404", "NoSuchBucket", "NotFound"}:
                    raise
                self._client.create_bucket(Bucket=self.bucket)  # type: ignore[attr-defined]

        await asyncio.to_thread(ensure)

    async def check(self) -> None:
        await asyncio.to_thread(self._client.head_bucket, Bucket=self.bucket)  # type: ignore[attr-defined]

    async def put(self, key: str, data: bytes, content_type: str, checksum: str | None = None) -> None:
        extra = {"Metadata": {"sha256": checksum}} if checksum else {}
        await asyncio.to_thread(
            self._client.put_object,  # type: ignore[attr-defined]
            Bucket=self.bucket,
            Key=key,
            Body=data,
            ContentType=content_type,
            **extra,
        )

    async def get(self, key: str) -> bytes:
        def get() -> bytes:
            return self._client.get_object(Bucket=self.bucket, Key=key)["Body"].read()  # type: ignore[attr-defined]

        return await asyncio.to_thread(get)

    async def delete_prefix(self, prefix: str) -> None:
        def delete() -> None:
            listing = self._client.list_objects_v2(Bucket=self.bucket, Prefix=prefix)  # type: ignore[attr-defined]
            for item in listing.get("Contents", []):
                self._client.delete_object(Bucket=self.bucket, Key=item["Key"])  # type: ignore[attr-defined]

        await asyncio.to_thread(delete)
