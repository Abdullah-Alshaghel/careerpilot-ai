"""Versioned event envelope (architecture doc §12).

Mirrors ``shared/event-schemas/envelope.v1.schema.json``. Event payloads are
defined per event type by the producing service; this module only defines the
envelope and the registry of known event types.
"""

from __future__ import annotations

import uuid
from datetime import datetime, timezone
from typing import Any

from pydantic import BaseModel, ConfigDict, Field, field_validator

KNOWN_EVENT_TYPES = frozenset(
    {
        "search.started",
        "job.discovered",
        "job.analyzed",
        "job.matched",
        "approval.requested",
        "job.approved",
        "job.rejected",
        "application.package.generated",
        "application.approved",
        "application.submitted",
        "application.status_changed",
        "notification.requested",
    }
)


class EventEnvelope(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    event_id: uuid.UUID = Field(default_factory=uuid.uuid4)
    event_type: str
    schema_version: int = Field(ge=1)
    occurred_at: datetime = Field(default_factory=lambda: datetime.now(timezone.utc))
    correlation_id: str = Field(min_length=8, max_length=128)
    aggregate_id: str = Field(min_length=1, max_length=128)
    # Owner of the data; consumers must verify ownership before acting.
    user_id: uuid.UUID | None = None
    producer: str = Field(min_length=1, max_length=64)
    payload: dict[str, Any]

    @field_validator("event_type")
    @classmethod
    def _known_type(cls, value: str) -> str:
        if value not in KNOWN_EVENT_TYPES:
            raise ValueError(f"unknown event type: {value}")
        return value

    @field_validator("occurred_at")
    @classmethod
    def _utc(cls, value: datetime) -> datetime:
        if value.tzinfo is None:
            raise ValueError("occurred_at must be timezone-aware (UTC)")
        return value.astimezone(timezone.utc)

    @property
    def routing_key(self) -> str:
        return f"{self.event_type}.v{self.schema_version}"
