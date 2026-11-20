"""Event messaging over RabbitMQ (topology in docs/EVENTS.md).

- Producers never publish inside a request. They add the event to their own
  ``outbox_events`` table in the same transaction as the state change; a relay
  publishes pending rows with publisher confirms and marks them sent. A crash
  between commit and publish therefore delays an event but never loses it.
- Consumers get one durable queue each, dead-lettered to ``careerpilot.dlx``.
  A failing message is retried once (redelivery), then parked in
  ``<queue>.dlq`` for inspection. Consumers must be idempotent on ``event_id``.
"""

from __future__ import annotations

import asyncio
import logging
import uuid
from collections.abc import Awaitable, Callable, Iterable
from datetime import datetime, timezone
from pathlib import Path

from pydantic import ValidationError
from sqlalchemy import Column, DateTime, Integer, MetaData, String, Table, insert, select, update
from sqlalchemy.dialects.postgresql import JSONB, UUID
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from careerpilot_common.correlation import correlation_id_var
from careerpilot_common.events import EventEnvelope

log = logging.getLogger("careerpilot.messaging")

EXCHANGE = "careerpilot.events"
DEAD_LETTER_EXCHANGE = "careerpilot.dlx"

# ------------------------------------------------------------------ outbox


def outbox_table(metadata: MetaData) -> Table:
    """The outbox, declared on the owning service's metadata (so its Alembic
    migrations manage it like any other table)."""
    return Table(
        "outbox_events",
        metadata,
        Column("event_id", UUID(as_uuid=True), primary_key=True),
        Column("routing_key", String(128), nullable=False),
        Column("envelope", JSONB, nullable=False),
        Column("created_at", DateTime(timezone=True), nullable=False),
        Column("published_at", DateTime(timezone=True), index=True),
        Column("attempts", Integer, nullable=False, server_default="0"),
    )


async def enqueue_event(session: AsyncSession, table: Table, envelope: EventEnvelope) -> None:
    """Add an event to the outbox inside the caller's transaction."""
    await session.execute(
        insert(table).values(
            event_id=envelope.event_id,
            routing_key=envelope.routing_key,
            envelope=envelope.model_dump(mode="json"),
            created_at=datetime.now(timezone.utc),
        )
    )


class EventPublisher:
    """Persistent messages to the topic exchange, with publisher confirms."""

    def __init__(self, url: str, connect: Callable[..., Awaitable[object]] | None = None) -> None:
        self._url = url
        self._connect = connect
        self._connection: object | None = None
        self._exchange: object | None = None

    async def _ensure(self) -> object:
        if self._exchange is not None and not self._connection.is_closed:  # type: ignore[attr-defined]
            return self._exchange
        import aio_pika

        connect = self._connect or aio_pika.connect_robust
        self._connection = await connect(self._url, timeout=10)
        channel = await self._connection.channel(publisher_confirms=True)  # type: ignore[attr-defined]
        self._exchange = await channel.declare_exchange(EXCHANGE, aio_pika.ExchangeType.TOPIC, durable=True)
        return self._exchange

    async def publish(self, routing_key: str, envelope: dict) -> None:
        import json

        import aio_pika

        exchange = await self._ensure()
        message = aio_pika.Message(
            body=json.dumps(envelope).encode(),
            content_type="application/json",
            delivery_mode=aio_pika.DeliveryMode.PERSISTENT,
            message_id=str(envelope["event_id"]),
            correlation_id=envelope.get("correlation_id"),
        )
        # Raises unless the broker confirms the message was routed and stored.
        await exchange.publish(message, routing_key=routing_key, mandatory=False)  # type: ignore[attr-defined]

    async def close(self) -> None:
        if self._connection is not None:
            await self._connection.close()  # type: ignore[attr-defined]
            self._connection = None
            self._exchange = None


async def relay_outbox(
    sessionmaker: async_sessionmaker[AsyncSession],
    table: Table,
    publish: Callable[[str, dict], Awaitable[None]],
    batch_size: int = 50,
) -> int:
    """Publish pending outbox rows in order. Returns how many were published.

    ``FOR UPDATE SKIP LOCKED`` lets several relay replicas run without
    publishing the same row twice concurrently. If publishing fails the row
    stays pending (its attempt count increases) and is retried next round.
    """
    published = 0
    async with sessionmaker() as session, session.begin():
        rows = (
            await session.execute(
                select(table.c.event_id, table.c.routing_key, table.c.envelope)
                .where(table.c.published_at.is_(None))
                .order_by(table.c.created_at)
                .limit(batch_size)
                .with_for_update(skip_locked=True)
            )
        ).all()
        for row in rows:
            try:
                await publish(row.routing_key, row.envelope)
            except Exception as exc:
                log.warning(
                    "outbox publish failed; will retry",
                    extra={"event_id": str(row.event_id), "error_type": type(exc).__name__},
                )
                await session.execute(
                    update(table).where(table.c.event_id == row.event_id).values(attempts=table.c.attempts + 1)
                )
                break  # keep order: don't publish later events before this one
            await session.execute(
                update(table)
                .where(table.c.event_id == row.event_id)
                .values(published_at=datetime.now(timezone.utc), attempts=table.c.attempts + 1)
            )
            published += 1
    return published


# --------------------------------------------------------------- consumers

Handler = Callable[[EventEnvelope], Awaitable[None]]


async def declare_consumer_queue(channel: object, queue_name: str, routing_keys: Iterable[str]) -> object:
    import aio_pika

    exchange = await channel.declare_exchange(EXCHANGE, aio_pika.ExchangeType.TOPIC, durable=True)  # type: ignore[attr-defined]
    dlx = await channel.declare_exchange(DEAD_LETTER_EXCHANGE, aio_pika.ExchangeType.DIRECT, durable=True)  # type: ignore[attr-defined]
    dlq = await channel.declare_queue(f"{queue_name}.dlq", durable=True)  # type: ignore[attr-defined]
    await dlq.bind(dlx, routing_key=queue_name)
    queue = await channel.declare_queue(  # type: ignore[attr-defined]
        queue_name,
        durable=True,
        arguments={"x-dead-letter-exchange": DEAD_LETTER_EXCHANGE, "x-dead-letter-routing-key": queue_name},
    )
    for key in routing_keys:
        await queue.bind(exchange, routing_key=key)
    return queue


async def handle_delivery(message: object, handler: Handler) -> str:
    """Process one delivery; returns the outcome (``ack``/``retry``/``dead-letter``)."""
    try:
        envelope = EventEnvelope.model_validate_json(message.body)  # type: ignore[attr-defined]
    except ValidationError:
        log.error("malformed event; dead-lettered")
        await message.reject(requeue=False)  # type: ignore[attr-defined]
        return "dead-letter"
    token = correlation_id_var.set(envelope.correlation_id)
    try:
        await handler(envelope)
    except Exception:
        retry = not message.redelivered  # type: ignore[attr-defined]
        log.exception(
            "event handler failed",
            extra={"event_id": str(envelope.event_id), "event_type": envelope.event_type, "retry": retry},
        )
        await message.reject(requeue=retry)  # type: ignore[attr-defined]
        return "retry" if retry else "dead-letter"
    finally:
        correlation_id_var.reset(token)
    await message.ack()  # type: ignore[attr-defined]
    return "ack"


async def run_consumer(
    url: str,
    queue_name: str,
    routing_keys: Iterable[str],
    handler: Handler,
    stop: asyncio.Event,
    prefetch: int = 4,
    heartbeat_file: str | None = None,
    heartbeat_interval: float = 10.0,
) -> None:
    """Consume until ``stop`` is set. If ``heartbeat_file`` is given, it is
    touched while the broker connection is open (container health check)."""
    import aio_pika

    connection = await aio_pika.connect_robust(url, timeout=10)
    try:
        channel = await connection.channel()
        await channel.set_qos(prefetch_count=prefetch)
        queue = await declare_consumer_queue(channel, queue_name, list(routing_keys))
        log.info("consumer started", extra={"queue": queue_name})

        async def on_message(message: object) -> None:
            await handle_delivery(message, handler)

        tag = await queue.consume(on_message)  # type: ignore[attr-defined]
        while not stop.is_set():
            if heartbeat_file and not connection.is_closed:
                await asyncio.to_thread(Path(heartbeat_file).touch)
            try:
                await asyncio.wait_for(stop.wait(), timeout=heartbeat_interval)
            except asyncio.TimeoutError:
                pass
        await queue.cancel(tag)  # type: ignore[attr-defined]
    finally:
        await connection.close()


def new_event(
    event_type: str,
    *,
    aggregate_id: str,
    user_id: uuid.UUID | None,
    producer: str,
    payload: dict,
    schema_version: int = 1,
) -> EventEnvelope:
    from careerpilot_common.correlation import get_correlation_id

    return EventEnvelope(
        event_type=event_type,
        schema_version=schema_version,
        correlation_id=get_correlation_id() or str(uuid.uuid4()),
        aggregate_id=aggregate_id,
        user_id=user_id,
        producer=producer,
        payload=payload,
    )
