"""Search worker: claims queued runs, runs each source, relays the outbox.

Runs inside the API process by default (``run_worker``); can also run alone
with ``python -m job_discovery_service.worker``. Runs are claimed with
``FOR UPDATE SKIP LOCKED``, so any number of copies is safe.
"""

from __future__ import annotations

import asyncio
import logging
import signal

from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

from careerpilot_common.logging import configure_logging
from careerpilot_common.messaging import EventPublisher, relay_outbox
from job_discovery_service.fetcher import SafeFetcher
from job_discovery_service.models import outbox
from job_discovery_service.service import claim_run, process_run
from job_discovery_service.settings import DiscoverySettings

log = logging.getLogger("job_discovery_service.worker")


def make_fetcher(settings: DiscoverySettings) -> SafeFetcher:
    return SafeFetcher(
        timeout=settings.fetch_timeout_seconds,
        max_bytes=settings.fetch_max_bytes,
        host_interval=settings.fetch_host_interval_seconds,
    )


async def run_loop(
    settings: DiscoverySettings,
    sm: async_sessionmaker[AsyncSession],
    fetcher: SafeFetcher,
    publisher: EventPublisher,
    stop: asyncio.Event,
    wake: asyncio.Event,
) -> None:
    log.info("search worker started")
    while not stop.is_set():
        try:
            await relay_outbox(sm, outbox, publisher.publish)
            run_id = await claim_run(sm, settings)
            if run_id is not None:
                await process_run(run_id, sm, fetcher, settings)
                continue
        except Exception:
            log.exception("search worker iteration failed")
        wake.clear()
        stop_wait = asyncio.ensure_future(stop.wait())
        wake_wait = asyncio.ensure_future(wake.wait())
        await asyncio.wait(
            {stop_wait, wake_wait}, timeout=settings.worker_poll_seconds, return_when=asyncio.FIRST_COMPLETED
        )
        for task in (stop_wait, wake_wait):
            task.cancel()


def main() -> None:
    settings = DiscoverySettings()
    configure_logging(f"{settings.service_name}-worker", settings.environment, settings.log_level)

    async def runner() -> None:
        stop, wake = asyncio.Event(), asyncio.Event()
        loop = asyncio.get_running_loop()
        for sig in (signal.SIGTERM, signal.SIGINT):
            try:
                loop.add_signal_handler(sig, stop.set)
            except NotImplementedError:  # Windows
                pass
        engine = create_async_engine(settings.database_url.get_secret_value(), pool_size=2, pool_pre_ping=True)
        fetcher = make_fetcher(settings)
        publisher = EventPublisher(settings.rabbitmq_url.get_secret_value())
        try:
            await run_loop(settings, async_sessionmaker(engine, expire_on_commit=False), fetcher, publisher, stop, wake)
        finally:
            await publisher.close()
            await fetcher.aclose()
            await engine.dispose()

    asyncio.run(runner())


if __name__ == "__main__":
    main()
