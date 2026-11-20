"""Job discovery against a real, freshly migrated PostgreSQL database with a
scripted fetcher (no internet). Critical tests #5 (discovery) and #6
(duplicate detection), plus isolation. Run with CAREERPILOT_INTEGRATION=1."""

from __future__ import annotations

import json
import os
import uuid

import pytest
from sqlalchemy import select
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

from careerpilot_common.errors import ServiceError
from job_discovery_service import service
from job_discovery_service.companies import CompanySources
from job_discovery_service.fetcher import FetchError, Response
from job_discovery_service.models import RunStatus, outbox
from job_discovery_service.settings import DiscoverySettings

pytestmark = [
    pytest.mark.integration,
    pytest.mark.skipif(os.environ.get("CAREERPILOT_INTEGRATION") != "1", reason="set CAREERPILOT_INTEGRATION=1"),
]

SETTINGS = DiscoverySettings(environment="test", search_cooldown_seconds=0)
GH = "https://boards-api.greenhouse.io/v1/boards/acme/jobs?content=true"
LEVER = "https://api.lever.co/v0/postings/globex?mode=json"
CAREERS = "https://acme.example/careers"
DESCRIPTION = "<p>Build payment APIs in Python.</p>"


def greenhouse(*jobs: tuple[int, str]) -> bytes:
    return json.dumps(
        {
            "jobs": [
                {
                    "id": jid,
                    "title": title,
                    "absolute_url": f"https://job-boards.greenhouse.io/acme/jobs/{jid}",
                    "location": {"name": "Riyadh"},
                    "content": DESCRIPTION,
                }
                for jid, title in jobs
            ]
        }
    ).encode()


def careers_page(title: str) -> bytes:
    posting = {"@type": "JobPosting", "title": title, "identifier": "CP-1", "description": DESCRIPTION}
    return f'<html><script type="application/ld+json">{json.dumps(posting)}</script></html>'.encode()


class ScriptedFetcher:
    def __init__(self, pages: dict[str, bytes | FetchError]) -> None:
        self.pages = pages

    async def get(self, url: str, *, accept: str = "*/*") -> Response:
        page = self.pages.get(url, FetchError("not_found"))
        if isinstance(page, FetchError):
            raise page
        return Response(url, 200, "text/html" if b"<html" in page else "application/json", page)

    get_page = get


def companies(*specs: tuple[str, str, dict]) -> list[CompanySources]:
    """(company name, connector type, config) -> what the Company Service returns."""
    by_name: dict[str, dict] = {}
    for name, kind, config in specs:
        entry = by_name.setdefault(name, {"id": str(uuid.uuid4()), "name": name, "connectors": []})
        entry["connectors"].append({"id": str(uuid.uuid4()), "connector_type": kind, "config": config, "enabled": True})
    return [CompanySources.model_validate(c) for c in by_name.values()]


@pytest.fixture
async def sm(scratch_jobs_db):
    engine = create_async_engine(scratch_jobs_db.replace("?connect_timeout=5", ""))
    yield async_sessionmaker(engine, expire_on_commit=False)
    await engine.dispose()


async def search(sm, user, sources, fetcher, settings=SETTINGS):
    async with sm() as db:
        run = await service.start_search(db, settings, user, sources)
    claimed = await service.claim_run(sm, settings)
    assert claimed == run.id
    await service.process_run(claimed, sm, fetcher, settings)
    async with sm() as db:
        return await service.get_run(db, user, run.id)


async def jobs_of(sm, user, **filters):
    async with sm() as db:
        return await service.list_jobs(db, user, **filters)


async def test_search_collects_jobs_and_reports_each_source(sm):
    user = uuid.uuid4()
    sources = companies(("Acme", "greenhouse", {"board_token": "acme"}), ("Globex", "lever", {"company": "globex"}))
    fetcher = ScriptedFetcher({GH: greenhouse((1, "Backend Engineer"), (2, "Data Analyst"))})  # Lever: 404
    run = await search(sm, user, sources, fetcher)

    assert run.status == RunStatus.COMPLETED  # one source succeeded
    assert [(s.company_name, s.status, s.error_code, s.jobs_new) for s in run.sources] == [
        ("Acme", "succeeded", None, 2),
        ("Globex", "failed", "not_found", 0),
    ]
    listing = await jobs_of(sm, user)
    assert listing.total == 2
    assert {j.title for j in listing.jobs} == {"Backend Engineer", "Data Analyst"}
    assert all(j.is_new and j.company_name == "Acme" and j.location == "Riyadh" for j in listing.jobs)
    async with sm() as db:
        detail = await service.get_job(db, user, listing.jobs[0].id)
    assert detail.description == "Build payment APIs in Python."  # plain text, never HTML

    async with sm() as db:
        events = (await db.execute(select(outbox.c.routing_key, outbox.c.envelope))).all()
    discovered = [e for key, e in events if key == "job.discovered.v1" and e["user_id"] == str(user)]
    assert len(discovered) == 2
    assert discovered[0]["payload"]["description"] == "Build payment APIs in Python."


async def test_rerun_updates_instead_of_duplicating_and_tracks_removed_jobs(sm):
    user = uuid.uuid4()
    sources = companies(("Acme", "greenhouse", {"board_token": "acme"}))
    await search(sm, user, sources, ScriptedFetcher({GH: greenhouse((1, "Engineer"), (2, "Analyst"))}))
    second = await search(sm, user, sources, ScriptedFetcher({GH: greenhouse((1, "Senior Engineer"), (3, "Designer"))}))

    assert (second.jobs_found, second.jobs_new) == (2, 1)
    listing = await jobs_of(sm, user)
    assert {(j.title, j.is_new) for j in listing.jobs} == {("Senior Engineer", False), ("Designer", True)}
    gone = await jobs_of(sm, user, include_unlisted=True)
    assert {j.title: j.listing_status for j in gone.jobs}["Analyst"] == "no_longer_listed"

    # Listed again later: back to active, same job (no duplicate).
    await search(sm, user, sources, ScriptedFetcher({GH: greenhouse((1, "Senior Engineer"), (2, "Analyst"))}))
    assert (await jobs_of(sm, user, include_unlisted=True)).total == 3


async def test_same_posting_on_two_sources_is_detected_as_a_duplicate(sm):
    user = uuid.uuid4()
    sources = companies(("Acme", "greenhouse", {"board_token": "acme"}), ("Acme", "careers_page", {"url": CAREERS}))
    fetcher = ScriptedFetcher({GH: greenhouse((1, "Backend Engineer")), CAREERS: careers_page("Backend Engineer")})
    run = await search(sm, user, sources, fetcher)
    assert run.jobs_found == 2

    visible = await jobs_of(sm, user)
    assert [j.source_type for j in visible.jobs] == ["greenhouse"]
    everything = await jobs_of(sm, user, include_duplicates=True)
    duplicate = next(j for j in everything.jobs if j.source_type == "careers_page")
    assert duplicate.duplicate_of_id == visible.jobs[0].id

    async with sm() as db:
        events = (await db.execute(select(outbox.c.envelope).where(outbox.c.routing_key == "job.discovered.v1"))).all()
    mine = [e for (e,) in events if e["user_id"] == str(user)]
    assert len(mine) == 1  # a duplicate is not announced again


async def test_run_fails_only_when_every_source_fails(sm):
    user = uuid.uuid4()
    sources = companies(("Acme", "greenhouse", {"board_token": "acme"}))
    run = await search(sm, user, sources, ScriptedFetcher({GH: FetchError("blocked_address")}))
    assert run.status == RunStatus.FAILED
    assert run.sources[0].error_code == "blocked_address"


async def test_search_rules_one_at_a_time_cooldown_and_sources_required(sm):
    user = uuid.uuid4()
    async with sm() as db:
        with pytest.raises(ServiceError) as err:
            await service.start_search(db, SETTINGS, user, [])
    assert err.value.code == "no_sources"

    sources = companies(("Acme", "greenhouse", {"board_token": "acme"}))
    async with sm() as db:
        await service.start_search(db, SETTINGS, user, sources)
    async with sm() as db:
        with pytest.raises(ServiceError) as err:
            await service.start_search(db, SETTINGS, user, sources)
    assert err.value.code == "search_in_progress"

    await service.process_run(await service.claim_run(sm, SETTINGS), sm, ScriptedFetcher({}), SETTINGS)
    slow = DiscoverySettings(environment="test", search_cooldown_seconds=600)
    async with sm() as db:
        with pytest.raises(ServiceError) as err:
            await service.start_search(db, slow, user, sources)
    assert (err.value.status_code, err.value.code) == (429, "search_too_soon")
    assert int(err.value.headers["Retry-After"]) > 0


async def test_disabled_connectors_are_skipped(sm):
    user = uuid.uuid4()
    sources = companies(("Acme", "greenhouse", {"board_token": "acme"}), ("Acme", "lever", {"company": "acme"}))
    sources[0].connectors[1].enabled = False
    async with sm() as db:
        run = await service.start_search(db, SETTINGS, user, sources)
    assert [s.connector_type for s in run.sources] == ["greenhouse"]
    await service.process_run(await service.claim_run(sm, SETTINGS), sm, ScriptedFetcher({}), SETTINGS)


async def test_import_a_job_from_a_link(sm):
    user = uuid.uuid4()
    url = "https://careers.initech.example/jobs/42"
    page = (
        b'<html><script type="application/ld+json">{"@type": "JobPosting", "title": "Platform Engineer", '
        b'"hiringOrganization": {"name": "Initech"}, "description": "<p>Run Kubernetes</p>"}</script></html>'
    )
    fetcher = ScriptedFetcher({url: page, "https://careers.initech.example/empty": b"<html><h1>Hi</h1></html>"})
    async with sm() as db:
        job, created = await service.import_url(db, fetcher, user, url)
    assert created and (job.title, job.company_name, job.source_type, job.url) == (
        "Platform Engineer",
        "Initech",
        "manual",
        url,
    )
    async with sm() as db:
        again, created = await service.import_url(db, fetcher, user, url)
    assert not created and again.id == job.id

    for bad_url, code in (
        ("https://careers.initech.example/empty", "no_job_found"),
        ("http://careers.initech.example/jobs/42", "https_required"),
    ):
        async with sm() as db:
            with pytest.raises(ServiceError) as err:
                await service.import_url(db, fetcher, user, bad_url)
        assert err.value.code == code


async def test_users_never_see_each_others_searches_or_jobs(sm):
    alice, bob = uuid.uuid4(), uuid.uuid4()
    sources = companies(("Acme", "greenhouse", {"board_token": "acme"}))
    run = await search(sm, alice, sources, ScriptedFetcher({GH: greenhouse((1, "Engineer"))}))
    job = (await jobs_of(sm, alice)).jobs[0]

    assert (await jobs_of(sm, bob)).total == 0
    async with sm() as db:
        assert await service.list_runs(db, bob) == []
        for call in (service.get_run(db, bob, run.id), service.get_job(db, bob, job.id)):
            with pytest.raises(ServiceError) as err:
                await call
            assert err.value.status_code == 404
