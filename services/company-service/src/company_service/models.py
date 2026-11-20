"""Tables owned by the Company Service. No other service reads them directly."""

from __future__ import annotations

import enum
import uuid
from datetime import datetime

from sqlalchemy import (
    Boolean,
    DateTime,
    Enum,
    ForeignKey,
    Integer,
    MetaData,
    String,
    Text,
    UniqueConstraint,
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


class Priority(str, enum.Enum):
    HIGH = "high"
    MEDIUM = "medium"
    LOW = "low"


class ConnectorType(str, enum.Enum):
    CAREERS_PAGE = "careers_page"  # a public careers page (Phase 5 fetches it)
    GREENHOUSE = "greenhouse"  # public job board API
    LEVER = "lever"
    ASHBY = "ashby"


def _enum(cls: type[enum.Enum], name: str) -> Enum:
    return Enum(cls, name=name, values_callable=lambda e: [m.value for m in e])


class Company(Base):
    __tablename__ = "companies"
    # One entry per company name per user (case- and punctuation-insensitive).
    __table_args__ = (UniqueConstraint("user_id", "name_key"),)

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    user_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), index=True)
    name: Mapped[str] = mapped_column(String(200))
    name_key: Mapped[str] = mapped_column(String(200))
    careers_url: Mapped[str | None] = mapped_column(String(2048))
    region: Mapped[str | None] = mapped_column(String(100))
    priority: Mapped[Priority] = mapped_column(_enum(Priority, "company_priority"), server_default="medium")
    notes: Mapped[str | None] = mapped_column(Text)
    # Optimistic concurrency: edits from two devices can't silently overwrite each other.
    version: Mapped[int] = mapped_column(Integer, server_default="1")
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), onupdate=func.now()
    )

    connectors: Mapped[list[Connector]] = relationship(
        back_populates="company",
        cascade="all, delete-orphan",
        passive_deletes=True,
        order_by="Connector.created_at",
        lazy="selectin",
    )


class Connector(Base):
    """Where to look for this company's jobs. Holds public identifiers only,
    never credentials; nothing is fetched until Phase 5."""

    __tablename__ = "connectors"
    __table_args__ = (UniqueConstraint("company_id", "connector_type", "identity_key"),)

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    company_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("companies.id", ondelete="CASCADE"), index=True)
    connector_type: Mapped[ConnectorType] = mapped_column(_enum(ConnectorType, "connector_type"))
    config: Mapped[dict] = mapped_column(JSONB)
    # Normalized identifier (board slug or URL) for duplicate detection.
    identity_key: Mapped[str] = mapped_column(String(2048))
    enabled: Mapped[bool] = mapped_column(Boolean, server_default="true")
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), onupdate=func.now()
    )

    company: Mapped[Company] = relationship(back_populates="connectors")
