"""Job discovery use cases. Every query is scoped by the user id."""

from __future__ import annotations

import hashlib
import logging
import uuid
from datetime import datetime, timedelta, timezone
from urllib.parse import urlsplit

from sqlalchemy import and_, func, or_, select, update
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from careerpilot_common.errors import ServiceError
from careerpilot_common.messaging import enqueue_event, new_event
from job_discovery_service.companies import CompanySources
from job_discovery_service.connectors import CONNECTORS, ConnectorError
from job_discovery_service.fetcher import FetchError, SafeFetcher
from job_discovery_service.models import (
    Job,
    ListingStatus,
    RunStatus,
    SearchRun,
    SearchRunSource,
    SourceStatus,
    outbox,
)
from job_discovery_service.normalize import NormalizedJob, company_key, description_hash, job_postings_from_html
from job_discovery_service.schemas import JobDiscoveredV1, JobList, JobOut, JobSummary, SearchRunOut
from job_discovery_service.settings import DiscoverySettings

log = logging.getLogger("job_discovery_service.service")


def utcnow() -> datetime:
    return datetime.now(timezone.utc)


def source_key(connector_type: str, config: dict) -> str:
    value = next(iter(config.values()), "")
    return f"{connector_type}:{str(value).casefold()}"[:600]


# ---------------------------------------------------------------- searches


async def start_search(
    db: AsyncSession, settings: DiscoverySettings, user_id: uuid.UUID, companies: list[CompanySources]
) -> SearchRunOut:
    # One run at a time per user; serialize concurrent clicks on the same user.
    await db.execute(select(func.pg_advisory_xact_lock(func.hashtext(str(user_id)))))
    busy = await db.scalar(
        select(SearchRun.id).where(
            SearchRun.user_id == user_id, SearchRun.status.in_([RunStatus.QUEUED, RunStatus.RUNNING])
        )
    )
    if busy is not None:
        raise ServiceError(409, "search_in_progress", "A search is already running")
    last = await db.scalar(select(func.max(SearchRun.created_at)).where(SearchRun.user_id == user_id))
    if last is not None:
        wait = settings.search_cooldown_seconds - (utcnow() - last).total_seconds()
        if wait > 0:
            raise ServiceError(
                429,
                "search_too_soon",
                "Please wait a moment before searching again",
                headers={"Retry-After": str(int(wait) + 1)},
            )

    sources = [
        (company, connector)
        for company in companies
        for connector in company.connectors
        if connector.enabled and connector.connector_type in CONNECTORS
    ][: settings.max_sources_per_run]
    if not sources:
        raise ServiceError(422, "no_sources", "Add a company with a job source first")

    run = SearchRun(user_id=user_id, status=RunStatus.QUEUED, filters={})
    for position, (company, connector) in enumerate(sources):
        run.sources.append(
            SearchRunSource(
                position=position,
                company_id=company.id,
                company_name=company.name[:200],
                connector_id=connector.id,
                connector_type=connector.connector_type,
                config=connector.config,
                status=SourceStatus.PENDING,
            )
        )
    db.add(run)
    await db.commit()
    await db.refresh(run, attribute_names=["created_at", "sources"])
    return SearchRunOut.model_validate(run)


async def list_runs(db: AsyncSession, user_id: uuid.UUID, limit: int = 10) -> list[SearchRunOut]:
    rows = await db.scalars(
        select(SearchRun).where(SearchRun.user_id == user_id).order_by(SearchRun.created_at.desc()).limit(limit)
    )
    return [SearchRunOut.model_validate(r) for r in rows]


async def get_run(db: AsyncSession, user_id: uuid.UUID, run_id: uuid.UUID) -> SearchRunOut:
    run = await db.scalar(select(SearchRun).where(SearchRun.id == run_id, SearchRun.user_id == user_id))
    if run is None:
        raise ServiceError(404, "not_found", "Search not found")
    return SearchRunOut.model_validate(run)


# ------------------------------------------------------------------ worker


async def claim_run(sm: async_sessionmaker[AsyncSession], settings: DiscoverySettings) -> uuid.UUID | None:
    stale = utcnow() - timedelta(seconds=settings.stale_run_seconds)
    async with sm() as db, db.begin():
        await db.execute(
            update(SearchRun)
            .where(
                SearchRun.status == RunStatus.RUNNING,
                SearchRun.started_at < stale,
                SearchRun.attempts >= settings.max_run_attempts,
            )
            .values(status=RunStatus.FAILED, finished_at=utcnow())
        )
        run = await db.scalar(
            select(SearchRun)
            .where(
                or_(
                    SearchRun.status == RunStatus.QUEUED,
                    and_(
                        SearchRun.status == RunStatus.RUNNING,
                        SearchRun.started_at < stale,
                        SearchRun.attempts < settings.max_run_attempts,
                    ),
                )
            )
            .order_by(SearchRun.created_at)
            .limit(1)
            .with_for_update(skip_locked=True)
        )
        if run is None:
            return None
        run.status, run.started_at, run.attempts = RunStatus.RUNNING, utcnow(), run.attempts + 1
        return run.id


async def upsert_jobs(
    db: AsyncSession,
    *,
    user_id: uuid.UUID,
    company_id: uuid.UUID | None,
    company_name: str,
    source_type: str,
    key: str,
    jobs: list[NormalizedJob],
    run_id: uuid.UUID | None,
    mark_missing: bool,
) -> tuple[int, int, list[uuid.UUID]]:
    """Insert or refresh jobs from one source. Returns (found, new, job ids)."""
    now = utcnow()
    ckey = company_key(company_name)
    existing = {
        j.external_id: j
        for j in await db.scalars(
            select(Job).where(Job.user_id == user_id, Job.source_type == source_type, Job.source_key == key)
        )
    }
    new_count, ids, seen = 0, [], set()
    for item in jobs:
        if item.external_id in seen:
            continue
        seen.add(item.external_id)
        digest = description_hash(item.title, item.description)
        job = existing.get(item.external_id)
        if job is not None:
            job.title, job.url, job.location, job.remote = item.title, item.url, item.location, item.remote
            job.employment_type, job.department = item.employment_type, item.department
            job.description, job.description_hash = item.description, digest
            job.posted_at = item.posted_at or job.posted_at
            job.last_seen_at, job.listing_status = now, ListingStatus.ACTIVE
            ids.append(job.id)
            continue
        # Same posting already known from another source (e.g. careers page and ATS)?
        original = await db.scalar(
            select(Job.id).where(
                Job.user_id == user_id,
                Job.description_hash == digest,
                Job.company_key == ckey,
                Job.duplicate_of_id.is_(None),
                Job.source_key != key,
            )
        )
        job = Job(
            id=uuid.uuid4(),
            user_id=user_id,
            company_id=company_id,
            company_name=company_name[:200],
            company_key=ckey,
            source_type=source_type,
            source_key=key,
            external_id=item.external_id,
            url=item.url,
            title=item.title,
            location=item.location,
            remote=item.remote,
            employment_type=item.employment_type,
            department=item.department,
            description=item.description,
            description_hash=digest,
            posted_at=item.posted_at,
            first_seen_at=now,
            last_seen_at=now,
            first_run_id=run_id,
            listing_status=ListingStatus.ACTIVE,
            duplicate_of_id=original,
        )
        db.add(job)
        ids.append(job.id)
        new_count += 1
        if original is None:
            await enqueue_event(
                db,
                outbox,
                new_event(
                    "job.discovered",
                    aggregate_id=str(job.id),
                    user_id=user_id,
                    producer="job-discovery-service",
                    payload=JobDiscoveredV1(
                        job_id=job.id,
                        company_id=company_id,
                        company_name=job.company_name,
                        title=job.title,
                        url=job.url,
                        location=job.location,
                        remote=job.remote,
                        source_type=source_type,
                        description=job.description,
                    ).model_dump(mode="json"),
                ),
            )
    if mark_missing:
        missing = [j.id for ext, j in existing.items() if ext not in seen]
        if missing:
            await db.execute(
                update(Job).where(Job.id.in_(missing)).values(listing_status=ListingStatus.NO_LONGER_LISTED)
            )
    return len(seen), new_count, ids


async def process_run(
    run_id: uuid.UUID, sm: async_sessionmaker[AsyncSession], fetcher: SafeFetcher, settings: DiscoverySettings
) -> RunStatus:
    async with sm() as db:
        run = await db.get(SearchRun, run_id)
        assert run is not None  # noqa: S101 - claimed just before
        user_id = run.user_id
        pending = [(s.id, s.connector_type, dict(s.config), s.company_id, s.company_name) for s in run.sources
                   if s.status == SourceStatus.PENDING]  # fmt: skip

    for source_id, connector_type, config, company_id, company_name in pending:
        error: str | None = None
        jobs: list[NormalizedJob] = []
        try:
            jobs = await CONNECTORS[connector_type](fetcher, config, settings.max_jobs_per_source)
        except (FetchError, ConnectorError) as exc:
            error = exc.code
        except Exception:
            log.exception("connector crashed", extra={"connector_type": connector_type})
            error = "connector_error"
        async with sm() as db, db.begin():
            source = await db.get(SearchRunSource, source_id, with_for_update=True)
            assert source is not None  # noqa: S101
            source.finished_at = utcnow()
            if error is not None:
                source.status, source.error_code = SourceStatus.FAILED, error
                continue
            found, new, _ = await upsert_jobs(
                db,
                user_id=user_id,
                company_id=company_id,
                company_name=company_name,
                source_type=connector_type,
                key=source_key(connector_type, config),
                jobs=jobs,
                run_id=run_id,
                mark_missing=True,
            )
            source.status, source.jobs_found, source.jobs_new = SourceStatus.SUCCEEDED, found, new

    async with sm() as db, db.begin():
        run = await db.get(SearchRun, run_id, with_for_update=True)
        assert run is not None  # noqa: S101
        await db.refresh(run, attribute_names=["sources"])
        run.jobs_found = sum(s.jobs_found for s in run.sources)
        run.jobs_new = sum(s.jobs_new for s in run.sources)
        succeeded = any(s.status == SourceStatus.SUCCEEDED for s in run.sources)
        run.status = RunStatus.COMPLETED if succeeded else RunStatus.FAILED
        run.finished_at = utcnow()
        status = run.status
    log.info("search run finished", extra={"run_id": str(run_id), "status": status.value})
    return status


# -------------------------------------------------------------------- jobs


def _summary(job: Job, latest_run: uuid.UUID | None) -> dict:
    return {
        "id": job.id,
        "company_id": job.company_id,
        "company_name": job.company_name,
        "source_type": job.source_type,
        "title": job.title,
        "url": job.url,
        "location": job.location,
        "remote": job.remote,
        "employment_type": job.employment_type,
        "department": job.department,
        "posted_at": job.posted_at,
        "first_seen_at": job.first_seen_at,
        "listing_status": job.listing_status,
        "duplicate_of_id": job.duplicate_of_id,
        "is_new": latest_run is not None and job.first_run_id == latest_run,
    }


async def _latest_completed_run(db: AsyncSession, user_id: uuid.UUID) -> uuid.UUID | None:
    return await db.scalar(
        select(SearchRun.id)
        .where(SearchRun.user_id == user_id, SearchRun.status == RunStatus.COMPLETED)
        .order_by(SearchRun.finished_at.desc())
        .limit(1)
    )


async def list_jobs(
    db: AsyncSession,
    user_id: uuid.UUID,
    *,
    q: str | None = None,
    company_id: uuid.UUID | None = None,
    remote_only: bool = False,
    include_unlisted: bool = False,
    include_duplicates: bool = False,
    limit: int = 50,
    offset: int = 0,
) -> JobList:
    conditions = [Job.user_id == user_id]
    if q:
        pattern = "%" + q.replace("\\", "\\\\").replace("%", "\\%").replace("_", "\\_") + "%"
        conditions.append(
            or_(
                Job.title.ilike(pattern, escape="\\"),
                Job.company_name.ilike(pattern, escape="\\"),
                Job.location.ilike(pattern, escape="\\"),
            )
        )
    if company_id:
        conditions.append(Job.company_id == company_id)
    if remote_only:
        conditions.append(Job.remote.is_(True))
    if not include_unlisted:
        conditions.append(Job.listing_status == ListingStatus.ACTIVE)
    if not include_duplicates:
        conditions.append(Job.duplicate_of_id.is_(None))
    total = await db.scalar(select(func.count()).select_from(Job).where(*conditions)) or 0
    rows = await db.scalars(
        select(Job).where(*conditions).order_by(Job.first_seen_at.desc(), Job.title).limit(limit).offset(offset)
    )
    latest = await _latest_completed_run(db, user_id)
    return JobList(jobs=[JobSummary(**_summary(j, latest)) for j in rows], total=total)


async def get_job(db: AsyncSession, user_id: uuid.UUID, job_id: uuid.UUID) -> JobOut:
    job = await db.scalar(select(Job).where(Job.id == job_id, Job.user_id == user_id))
    if job is None:
        raise ServiceError(404, "not_found", "Job not found")
    return JobOut(**_summary(job, await _latest_completed_run(db, user_id)), description=job.description)


async def import_url(db: AsyncSession, fetcher: SafeFetcher, user_id: uuid.UUID, url: str) -> tuple[JobOut, bool]:
    """Add one job from a link the user pasted. Returns (job, created)."""
    parts = urlsplit(url)
    if parts.scheme != "https" or not parts.hostname:
        raise ServiceError(422, "https_required", "Use an https:// job link")
    try:
        page = await fetcher.get_page(url)
    except FetchError as exc:
        status = 422 if exc.code in {"unsafe_url", "blocked_address", "disallowed_by_robots", "not_found"} else 502
        raise ServiceError(status, exc.code, "That job page could not be read") from exc
    postings = job_postings_from_html(page.text(), page.url, limit=1)
    if not postings:
        raise ServiceError(422, "no_job_found", "No job posting was found on that page")
    posting = postings[0]
    host = (urlsplit(page.url).hostname or parts.hostname).casefold()
    if posting.external_id == page.url or not posting.external_id:
        posting = posting.model_copy(update={"external_id": hashlib.sha256(page.url.encode()).hexdigest()[:40]})
    if posting.url is None:
        posting = posting.model_copy(update={"url": page.url})
    _, new, ids = await upsert_jobs(
        db,
        user_id=user_id,
        company_id=None,
        company_name=posting.company or host,
        source_type="manual",
        key=f"manual:{host}",
        jobs=[posting],
        run_id=None,
        mark_missing=False,
    )
    await db.commit()
    return await get_job(db, user_id, ids[0]), new > 0
