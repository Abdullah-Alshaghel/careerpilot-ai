"""Document-events consumer: ``python -m career_profile_service.worker``.

Consumes ``document.text_extracted.v1`` (suggest facts) and
``document.deleted.v1`` (drop that document's unreviewed suggestions) from its
own durable queue. Handlers are idempotent on ``event_id``.
"""

from __future__ import annotations

import asyncio
import logging
import signal
import uuid

from pydantic import BaseModel, ConfigDict
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

from career_profile_service import service
from career_profile_service.settings import ProfileSettings
from careerpilot_common.events import EventEnvelope
from careerpilot_common.logging import configure_logging
from careerpilot_common.messaging import run_consumer

log = logging.getLogger("career_profile_service.worker")

ROUTING_KEYS = ("document.text_extracted.v1", "document.deleted.v1")


# Mirrors of the producer's payloads (shared/event-schemas/payloads), so this
# service never imports another service's code. Contract tests keep them aligned.
class DocumentTextExtractedV1(BaseModel):
    model_config = ConfigDict(extra="ignore")  # tolerate additive fields

    document_id: uuid.UUID
    filename: str
    mime_type: str
    parser_version: str
    document_text: str
    truncated: bool


class DocumentDeletedV1(BaseModel):
    model_config = ConfigDict(extra="ignore")

    document_id: uuid.UUID


def make_handler(sessionmaker: async_sessionmaker[AsyncSession]):
    async def handle(event: EventEnvelope) -> None:
        if event.user_id is None:
            log.error("document event without owner; dropped", extra={"event_id": str(event.event_id)})
            return
        async with sessionmaker() as db:
            if event.event_type == "document.text_extracted":
                payload = DocumentTextExtractedV1.model_validate(event.payload)
                await service.ingest_document_text(
                    db,
                    event_id=event.event_id,
                    user_id=event.user_id,
                    document_id=payload.document_id,
                    filename=payload.filename,
                    text=payload.document_text,
                )
            elif event.event_type == "document.deleted":
                payload_deleted = DocumentDeletedV1.model_validate(event.payload)
                await service.forget_document(
                    db, event_id=event.event_id, user_id=event.user_id, document_id=payload_deleted.document_id
                )

    return handle


async def run(settings: ProfileSettings, stop: asyncio.Event) -> None:
    engine = create_async_engine(settings.database_url.get_secret_value(), pool_size=2, pool_pre_ping=True)
    try:
        await run_consumer(
            settings.rabbitmq_url.get_secret_value(),
            settings.document_events_queue,
            ROUTING_KEYS,
            make_handler(async_sessionmaker(engine, expire_on_commit=False)),
            stop,
            heartbeat_file=settings.worker_heartbeat_file,
        )
    finally:
        await engine.dispose()


def main() -> None:
    settings = ProfileSettings()
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
