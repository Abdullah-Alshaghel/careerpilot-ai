"""Tables owned by the Job Discovery Service. No other service reads them directly."""

from __future__ import annotations

import enum
import uuid
from datetime import datetime

from sqlalchemy import (
    Boolean,
    DateTime,
    Enum,
    ForeignKey,
    Index,
    Integer,
    MetaData,
    String,
    Text,
    UniqueConstraint,
    func,
)
from sqlalchemy.dialects.postgresql import JSONB, UUID
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


class RunStatus(str, enum.Enum):
    QUEUED = "queued"
    RUNNING = "running"
    COMPLETED = "completed"  # at least one source succeeded (others may have failed)
    FAILED = "failed"  # every source failed


class SourceStatus(str, enum.Enum):
    PENDING = "pending"
    SUCCEEDED = "succeeded"
    FAILED = "failed"


class ListingStatus(str, enum.Enum):
    ACTIVE = "active"
    NO_LONGER_LISTED = "no_longer_listed"  # missing from the last successful fetch of its source


def _enum(cls: type[enum.Enum], name: str) -> Enum:
    return Enum(cls, name=name, values_callable=lambda e: [m.value for m in e])


class SearchRun(Base):
    """A search the user started. Sources are a snapshot taken at start."""

    __tablename__ = "search_runs"

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    user_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), index=True)
    status: Mapped[RunStatus] = mapped_column(_enum(RunStatus, "run_status"), index=True)
    filters: Mapped[dict] = mapped_column(JSONB, server_default="{}")
    jobs_found: Mapped[int] = mapped_column(Integer, server_default="0")
    jobs_new: Mapped[int] = mapped_column(Integer, server_default="0")
    attempts: Mapped[int] = mapped_column(Integer, server_default="0")
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    started_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    finished_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))

    sources: Mapped[list[SearchRunSource]] = relationship(
        back_populates="run",
        cascade="all, delete-orphan",
        passive_deletes=True,
        order_by="SearchRunSource.position",
        lazy="selectin",
    )


class SearchRunSource(Base):
    __tablename__ = "search_run_sources"

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    run_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("search_runs.id", ondelete="CASCADE"), index=True)
    position: Mapped[int] = mapped_column(Integer)
    company_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True))
    company_name: Mapped[str] = mapped_column(String(200))
    connector_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True))
    connector_type: Mapped[str] = mapped_column(String(32))
    config: Mapped[dict] = mapped_column(JSONB)
    status: Mapped[SourceStatus] = mapped_column(_enum(SourceStatus, "source_status"))
    # Short code (not_found, timeout, blocked_address, disallowed_by_robots, ...).
    error_code: Mapped[str | None] = mapped_column(String(64))
    jobs_found: Mapped[int] = mapped_column(Integer, server_default="0")
    jobs_new: Mapped[int] = mapped_column(Integer, server_default="0")
    finished_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))

    run: Mapped[SearchRun] = relationship(back_populates="sources")


class Job(Base):
    __tablename__ = "jobs"
    __table_args__ = (
        # The same posting from the same source is one job, re-found or not.
        UniqueConstraint("user_id", "source_type", "source_key", "external_id"),
        # Cross-source duplicate detection.
        Index("ix_jobs_user_id_description_hash", "user_id", "description_hash"),
        Index("ix_jobs_user_id_first_seen_at", "user_id", "first_seen_at"),
    )

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    user_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True))
    company_id: Mapped[uuid.UUID | None] = mapped_column(UUID(as_uuid=True), index=True)
    company_name: Mapped[str] = mapped_column(String(200))
    company_key: Mapped[str] = mapped_column(String(200))
    # greenhouse | lever | ashby | careers_page | manual
    source_type: Mapped[str] = mapped_column(String(32))
    # e.g. "greenhouse:acme", "careers_page:https://acme.com/careers", "manual:acme.com"
    source_key: Mapped[str] = mapped_column(String(600))
    external_id: Mapped[str] = mapped_column(String(200))
    url: Mapped[str | None] = mapped_column(String(2048))
    title: Mapped[str] = mapped_column(String(300))
    location: Mapped[str | None] = mapped_column(String(200))
    remote: Mapped[bool | None] = mapped_column(Boolean)
    employment_type: Mapped[str | None] = mapped_column(String(60))
    department: Mapped[str | None] = mapped_column(String(200))
    # Plain text only (converted from the source's HTML); never rendered as markup.
    description: Mapped[str] = mapped_column(Text)
    description_hash: Mapped[str] = mapped_column(String(64))
    posted_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    first_seen_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    last_seen_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    first_run_id: Mapped[uuid.UUID | None] = mapped_column(UUID(as_uuid=True))
    listing_status: Mapped[ListingStatus] = mapped_column(
        _enum(ListingStatus, "listing_status"), server_default=ListingStatus.ACTIVE.value
    )
    # Same posting already known from another source: this row points at it.
    duplicate_of_id: Mapped[uuid.UUID | None] = mapped_column(ForeignKey("jobs.id", ondelete="SET NULL"))


outbox = outbox_table(Base.metadata)
