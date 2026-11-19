from __future__ import annotations

import asyncio

import pytest

from careerpilot_common.health import RabbitMQProbe


class FakeConnection:
    def __init__(self) -> None:
        self.is_closed = False

    async def close(self) -> None:
        self.is_closed = True


class Broker:
    def __init__(self) -> None:
        self.up = True
        self.opened: list[FakeConnection] = []

    async def connect(self, url: str, timeout: float) -> FakeConnection:
        await asyncio.sleep(0.01)
        if not self.up:
            raise ConnectionError("broker down")
        self.opened.append(FakeConnection())
        return self.opened[-1]


async def test_reuses_one_connection_across_checks():
    broker = Broker()
    probe = RabbitMQProbe("amqp://x", 1.0, connect=broker.connect)
    for _ in range(20):
        await probe.check()
    assert len(broker.opened) == 1


async def test_concurrent_first_checks_open_a_single_connection():
    broker = Broker()
    probe = RabbitMQProbe("amqp://x", 1.0, connect=broker.connect)
    await asyncio.gather(*(probe.check() for _ in range(10)))
    assert len(broker.opened) == 1


async def test_reports_down_and_reconnects_after_the_connection_drops():
    broker = Broker()
    probe = RabbitMQProbe("amqp://x", 1.0, connect=broker.connect)
    await probe.check()

    broker.opened[0].is_closed = True  # broker restarted
    broker.up = False
    with pytest.raises(ConnectionError):
        await probe.check()

    broker.up = True
    await probe.check()
    assert len(broker.opened) == 2


async def test_close_releases_the_connection():
    broker = Broker()
    probe = RabbitMQProbe("amqp://x", 1.0, connect=broker.connect)
    await probe.check()
    await probe.close()
    assert broker.opened[0].is_closed
