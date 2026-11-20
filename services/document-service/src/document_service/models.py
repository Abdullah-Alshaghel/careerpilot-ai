"""Tables owned by the Document Service. No other service reads them directly."""

from __future__ import annotations

import enum
import uuid
from datetime import datetime

from sqlalchemy import (
    BigInteger,
    DateTime,
    Enum,
    ForeignKey,
    Integer,
    MetaData,
    String,
    UniqueConstraint,
    func,
    text,
)
from sqlalchemy.dialects.postgresql import UUID
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column, relationship

from careerpilot_common.messaging import outbox_table

NAMING_CONVENTION = {
    "ix": "ix_%(table_name)s_%(column_0_N_name)s",
    "uq": "uq_%(table_name)s_%(column_0_N_name)s",
    "ck": "ck_%(table_name)s_%(constraint_name)s",
    "fk": "fk_%(table_name)s_%(column_0_name)s_%(referred_table_name)s",
    "pk": "pk_%(table_name)s",
}


class Base(DeclarativeBase):
    metadata = MetaData(naming_convention=NAMING_CONVENTION)


class DocumentStatus(str, enum.Enum):
    UPLOADED = "uploaded"  # stored, waiting for the extraction worker
    PROCESSING = "processing"
    EXTRACTED = "extracted"
    FAILED = "failed"


class ExtractionStatus(str, enum.Enum):
    SUCCEEDED = "succeeded"
    FAILED = "failed"


def _enum(cls: type[enum.Enum], name: str) -> Enum:
    return Enum(cls, name=name, values_callable=lambda e: [m.value for m in e])


class Document(Base):
    __tablename__ = "documents"
    # Uploading the same file twice returns the existing document.
    __table_args__ = (UniqueConstraint("user_id", "checksum"),)

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    # Owner, from the verified access token. Every query filters on it.
    user_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), index=True)
    filename: Mapped[str] = mapped_column(String(255))
    mime_type: Mapped[str] = mapped_column(String(127))
    size_bytes: Mapped[int] = mapped_column(BigInteger)
    checksum: Mapped[str] = mapped_column(String(64))  # SHA-256 hex
    object_key: Mapped[str] = mapped_column(String(512), unique=True)
    version: Mapped[int] = mapped_column(Integer, server_default="1")
    status: Mapped[DocumentStatus] = mapped_column(
        _enum(DocumentStatus, "document_status"), server_default=DocumentStatus.UPLOADED.value, index=True
    )
    # Short machine code (e.g. "encrypted_pdf", "no_text"); never raw error text.
    failure_reason: Mapped[str | None] = mapped_column(String(64))
    attempts: Mapped[int] = mapped_column(Integer, server_default="0")
    processing_started_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), onupdate=func.now()
    )

    extractions: Mapped[list[DocumentExtraction]] = relationship(
        back_populates="document", cascade="all, delete-orphan", passive_deletes=True
    )


class DocumentExtraction(Base):
    __tablename__ = "document_extractions"

    id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), primary_key=True, default=uuid.uuid4, server_default=text("gen_random_uuid()")
    )
    document_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("documents.id", ondelete="CASCADE"), index=True)
    # Object key of the extracted plain text (never stored in the database).
    extracted_text_ref: Mapped[str | None] = mapped_column(String(512))
    parser_version: Mapped[str] = mapped_column(String(32))
    status: Mapped[ExtractionStatus] = mapped_column(_enum(ExtractionStatus, "extraction_status"))
    char_count: Mapped[int | None] = mapped_column(Integer)
    page_count: Mapped[int | None] = mapped_column(Integer)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())

    document: Mapped[Document] = relationship(back_populates="extractions")


outbox = outbox_table(Base.metadata)
