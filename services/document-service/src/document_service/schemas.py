"""Public response shapes and event payloads (mirrored in shared/ contracts)."""

from __future__ import annotations

import uuid
from datetime import datetime
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field

from document_service.models import DocumentStatus


class DocumentOut(BaseModel):
    model_config = ConfigDict(extra="forbid", from_attributes=True)

    id: uuid.UUID
    filename: str
    mime_type: Literal[
        "application/pdf",
        "application/vnd.openxmlformats-officedocument.wordprocessingml.document",
        "text/plain",
    ]
    size_bytes: int
    status: DocumentStatus
    failure_reason: str | None
    created_at: datetime
    updated_at: datetime


class DocumentList(BaseModel):
    model_config = ConfigDict(extra="forbid")

    documents: list[DocumentOut]


class DocumentTextExtractedV1(BaseModel):
    """Payload of ``document.text_extracted`` v1. Carries CV text: consumers
    must never log it (``document_text`` is a redacted log key)."""

    model_config = ConfigDict(extra="forbid")

    document_id: uuid.UUID
    filename: str = Field(max_length=255)
    mime_type: str = Field(max_length=127)
    parser_version: str = Field(max_length=32)
    document_text: str
    truncated: bool


class DocumentDeletedV1(BaseModel):
    """Payload of ``document.deleted`` v1."""

    model_config = ConfigDict(extra="forbid")

    document_id: uuid.UUID
