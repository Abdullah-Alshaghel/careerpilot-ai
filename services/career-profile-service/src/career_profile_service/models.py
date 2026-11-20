"""Tables owned by the Career Profile Service. No other service reads them directly."""

from __future__ import annotations

import enum
import uuid
from datetime import date, datetime

from sqlalchemy import (
    BigInteger,
    Date,
    DateTime,
    Enum,
    Float,
    ForeignKey,
    Identity,
    Index,
    Integer,
    MetaData,
    String,
    Text,
    func,
)
from sqlalchemy.dialects.postgresql import JSONB, UUID
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column, relationship

NAMING_CONVENTION = {
    "ix": "ix_%(table_name)s_%(column_0_N_name)s",
    "uq": "uq_%(table_name)s_%(column_0_N_name)s",
    "ck": "ck_%(table_name)s_%(constraint_name)s",
    "fk": "fk_%(table_name)s_%(column_0_name)s_%(referred_table_name)s",
    "pk": "pk_%(table_name)s",
}


class Base(DeclarativeBase):
    metadata = MetaData(naming_convention=NAMING_CONVENTION)


class FactType(str, enum.Enum):
    PERSONAL = "personal"
    SUMMARY = "summary"
    EMPLOYMENT = "employment"
    EDUCATION = "education"
    SKILL = "skill"
    CERTIFICATION = "certification"
    LANGUAGE = "language"


class VerificationStatus(str, enum.Enum):
    EXTRACTED = "extracted"  # suggested by a parser; NOT a fact until the user approves
    USER_VERIFIED = "user_verified"
    REJECTED = "rejected"


def _enum(cls: type[enum.Enum], name: str) -> Enum:
    return Enum(cls, name=name, values_callable=lambda e: [m.value for m in e])


class CareerProfile(Base):
    __tablename__ = "career_profiles"

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    user_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), unique=True)
    # Incremented on every change, so later phases can record which profile
    # version an application was built from.
    version: Mapped[int] = mapped_column(Integer, server_default="1")
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), onupdate=func.now()
    )

    facts: Mapped[list[CareerFact]] = relationship(back_populates="profile", passive_deletes=True)


class CareerFact(Base):
    __tablename__ = "career_facts"
    __table_args__ = (
        Index("ix_career_facts_profile_id_fact_type", "profile_id", "fact_type"),
        Index("ix_career_facts_profile_id_dedupe_key", "profile_id", "dedupe_key"),
    )

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    # Insertion order: facts from one CV keep the order they appear in it.
    seq: Mapped[int] = mapped_column(BigInteger, Identity(always=True))
    profile_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("career_profiles.id", ondelete="CASCADE"))
    fact_type: Mapped[FactType] = mapped_column(_enum(FactType, "fact_type"))
    # Structured value; its shape depends on fact_type (see schemas.py).
    value: Mapped[dict] = mapped_column(JSONB)
    # Normalized identity of the value, for de-duplication and conflict checks.
    dedupe_key: Mapped[str] = mapped_column(String(512))
    # Provenance. Null source_document_id = entered by the user.
    source_document_id: Mapped[uuid.UUID | None] = mapped_column(UUID(as_uuid=True), index=True)
    source_name: Mapped[str | None] = mapped_column(String(255))
    source_excerpt: Mapped[str | None] = mapped_column(Text)
    extractor_version: Mapped[str | None] = mapped_column(String(32))
    confidence: Mapped[float] = mapped_column(Float)
    verification_status: Mapped[VerificationStatus] = mapped_column(_enum(VerificationStatus, "verification_status"))
    valid_from: Mapped[date | None] = mapped_column(Date)
    valid_to: Mapped[date | None] = mapped_column(Date)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), onupdate=func.now()
    )

    profile: Mapped[CareerProfile] = relationship(back_populates="facts")


class ProcessedEvent(Base):
    """Consumer idempotency: an event id is handled at most once."""

    __tablename__ = "processed_events"

    event_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True)
    event_type: Mapped[str] = mapped_column(String(64))
    processed_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
