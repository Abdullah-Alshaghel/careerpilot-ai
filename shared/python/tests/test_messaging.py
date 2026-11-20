from __future__ import annotations

import uuid

from careerpilot_common.correlation import get_correlation_id
from careerpilot_common.messaging import handle_delivery, new_event


class FakeMessage:
    def __init__(self, body: bytes, redelivered: bool = False) -> None:
        self.body = body
        self.redelivered = redelivered
        self.outcome: str | None = None

    async def ack(self) -> None:
        self.outcome = "ack"

    async def reject(self, requeue: bool = False) -> None:
        self.outcome = "requeue" if requeue else "dead-letter"


def event_bytes() -> bytes:
    event = new_event(
        "document.deleted",
        aggregate_id="doc-1",
        user_id=uuid.uuid4(),
        producer="document-service",
        payload={"document_id": str(uuid.uuid4())},
    )
    return event.model_dump_json().encode()


async def test_successful_handler_acks_with_the_events_correlation_id():
    seen = []

    async def handler(event):
        seen.append((event.event_type, get_correlation_id()))

    message = FakeMessage(event_bytes())
    assert await handle_delivery(message, handler) == "ack"
    assert message.outcome == "ack"
    assert seen[0][0] == "document.deleted"
    assert seen[0][1]  # logs from the handler carry the originating request's id


async def test_failure_is_retried_once_then_dead_lettered():
    async def handler(event):
        raise RuntimeError("database down")

    first = FakeMessage(event_bytes())
    assert await handle_delivery(first, handler) == "retry"
    assert first.outcome == "requeue"

    second = FakeMessage(first.body, redelivered=True)
    assert await handle_delivery(second, handler) == "dead-letter"
    assert second.outcome == "dead-letter"


async def test_malformed_or_unknown_events_go_straight_to_the_dead_letter_queue():
    called = []

    async def handler(event):
        called.append(event)

    for body in (b"not json", b'{"event_type": "unknown.thing"}'):
        message = FakeMessage(body)
        assert await handle_delivery(message, handler) == "dead-letter"
    assert called == []
