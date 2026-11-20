"""Company workspace rules against a real, freshly migrated PostgreSQL database
(critical test #15 for company data). Run with CAREERPILOT_INTEGRATION=1."""

from __future__ import annotations

import os
import uuid

import pytest
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

from careerpilot_common.errors import ServiceError
from company_service import service
from company_service.models import Connector
from company_service.schemas import CompanyCreate, CompanyUpdate, ConnectorCreate, ConnectorUpdate
from company_service.settings import CompanySettings

pytestmark = [
    pytest.mark.integration,
    pytest.mark.skipif(os.environ.get("CAREERPILOT_INTEGRATION") != "1", reason="set CAREERPILOT_INTEGRATION=1"),
]

SETTINGS = CompanySettings(environment="test", max_companies_per_user=5, max_connectors_per_company=3)


@pytest.fixture
async def db(scratch_companies_db):
    engine = create_async_engine(scratch_companies_db.replace("?connect_timeout=5", ""))
    sm = async_sessionmaker(engine, expire_on_commit=False)
    yield sm
    await engine.dispose()


async def call(sm, fn, *args):
    async with sm() as session:
        return await fn(session, *args)


async def create(sm, user, **fields):
    return await call(sm, service.create_company, SETTINGS, user, CompanyCreate(**fields))


async def expect_error(sm, fn, *args) -> ServiceError:
    with pytest.raises(ServiceError) as err:
        await call(sm, fn, *args)
    return err.value


async def test_create_list_sorted_by_priority_then_name(db):
    user = uuid.uuid4()
    await create(db, user, name="Zeta", priority="low")
    await create(db, user, name="beta", priority="high")
    await create(db, user, name="Alpha", priority="high", careers_url="acme.com/careers", region="Riyadh")
    companies = await call(db, service.list_companies, user)
    assert [c.name for c in companies] == ["Alpha", "beta", "Zeta"]
    assert companies[0].careers_url == "https://acme.com/careers"
    assert companies[0].region == "Riyadh"
    assert companies[0].version == 1


async def test_duplicate_names_are_refused_case_insensitively(db):
    user = uuid.uuid4()
    await create(db, user, name="Acme, Inc.")
    err = await expect_error(db, service.create_company, SETTINGS, user, CompanyCreate(name="ACME inc"))
    assert (err.status_code, err.code) == (409, "company_exists")
    # Another user may track the same company.
    await create(db, uuid.uuid4(), name="Acme, Inc.")


@pytest.mark.parametrize(
    ("url", "code"),
    [
        ("http://acme.com", "https_required"),
        ("https://169.254.169.254/latest", "ip_address_not_allowed"),
        ("https://intranet.local/jobs", "private_host"),
        ("https://user:pw@acme.com", "credentials_in_url"),
    ],
)
async def test_unsafe_careers_urls_are_refused_with_a_reason(db, url, code):
    err = await expect_error(
        db, service.create_company, SETTINGS, uuid.uuid4(), CompanyCreate(name="X", careers_url=url)
    )
    assert (err.status_code, err.code) == (422, code)


async def test_partial_update_clear_field_and_version_conflict(db):
    user = uuid.uuid4()
    company = await create(db, user, name="Acme", careers_url="https://acme.com/jobs", notes="Referral from Sara")
    updated = await call(
        db, service.update_company, user, company.id, CompanyUpdate(priority="high", careers_url=None, version=1)
    )
    assert (updated.priority, updated.careers_url, updated.notes, updated.version) == (
        "high",
        None,
        "Referral from Sara",
        2,
    )

    stale = await expect_error(db, service.update_company, user, company.id, CompanyUpdate(region="Jeddah", version=1))
    assert (stale.status_code, stale.code) == (409, "version_conflict")
    unconditional = await call(db, service.update_company, user, company.id, CompanyUpdate(region="Jeddah"))
    assert (unconditional.region, unconditional.version) == ("Jeddah", 3)


async def test_renaming_into_an_existing_name_is_refused(db):
    user = uuid.uuid4()
    await create(db, user, name="Acme")
    other = await create(db, user, name="Globex")
    err = await expect_error(db, service.update_company, user, other.id, CompanyUpdate(name="acme"))
    assert err.code == "company_exists"


async def test_detected_ats_board_is_offered_until_added(db):
    user = uuid.uuid4()
    company = await create(db, user, name="Acme", careers_url="https://boards.greenhouse.io/acme")
    assert company.detected_connector is not None
    assert (company.detected_connector.connector_type, company.detected_connector.config) == (
        "greenhouse",
        {"board_token": "acme"},
    )
    with_connector = await call(
        db,
        service.add_connector,
        SETTINGS,
        user,
        company.id,
        ConnectorCreate(connector_type="greenhouse", config={"board_token": "ACME"}),
    )
    assert with_connector.detected_connector is None  # same board, case-insensitive
    assert with_connector.version == 2


async def test_connector_lifecycle_duplicates_and_limits(db):
    user = uuid.uuid4()
    company = await create(db, user, name="Acme")
    add = lambda body: call(db, service.add_connector, SETTINGS, user, company.id, body)  # noqa: E731

    result = await add(ConnectorCreate(connector_type="lever", config={"company": "acme"}))
    connector = result.connectors[0]
    assert (connector.connector_type, connector.config, connector.enabled) == ("lever", {"company": "acme"}, True)

    dup = await expect_error(
        db,
        service.add_connector,
        SETTINGS,
        user,
        company.id,
        ConnectorCreate(connector_type="lever", config={"company": "ACME"}),
    )
    assert dup.code == "connector_exists"

    toggled = await call(db, service.update_connector, user, company.id, connector.id, ConnectorUpdate(enabled=False))
    assert toggled.connectors[0].enabled is False

    await add(ConnectorCreate(connector_type="careers_page", config={"url": "acme.com/careers"}))
    await add(ConnectorCreate(connector_type="ashby", config={"organization": "acme"}))
    limit = await expect_error(
        db,
        service.add_connector,
        SETTINGS,
        user,
        company.id,
        ConnectorCreate(connector_type="greenhouse", config={"board_token": "acme"}),
    )
    assert limit.code == "connector_limit"

    after = await call(db, service.delete_connector, user, company.id, connector.id)
    assert [c.connector_type for c in after.connectors] == ["careers_page", "ashby"]


async def test_deleting_a_company_removes_its_connectors(db):
    user = uuid.uuid4()
    company = await create(db, user, name="Acme")
    await call(
        db,
        service.add_connector,
        SETTINGS,
        user,
        company.id,
        ConnectorCreate(connector_type="lever", config={"company": "acme"}),
    )
    await call(db, service.delete_company, user, company.id)
    async with db() as session:
        remaining = await session.scalar(
            select(func.count()).select_from(Connector).where(Connector.company_id == company.id)
        )
    assert remaining == 0
    assert (await expect_error(db, service.get_company, user, company.id)).status_code == 404


async def test_company_limit(db):
    user = uuid.uuid4()
    for i in range(SETTINGS.max_companies_per_user):
        await create(db, user, name=f"Company {i}")
    err = await expect_error(db, service.create_company, SETTINGS, user, CompanyCreate(name="One more"))
    assert err.code == "company_limit"


async def test_users_cannot_see_or_change_each_others_companies(db):
    alice, bob = uuid.uuid4(), uuid.uuid4()
    company = await create(db, alice, name="Acme")
    with_connector = await call(
        db,
        service.add_connector,
        SETTINGS,
        alice,
        company.id,
        ConnectorCreate(connector_type="lever", config={"company": "acme"}),
    )
    connector_id = with_connector.connectors[0].id

    attempts = [
        (service.get_company, bob, company.id),
        (service.update_company, bob, company.id, CompanyUpdate(name="Mine now")),
        (service.delete_company, bob, company.id),
        (
            service.add_connector,
            SETTINGS,
            bob,
            company.id,
            ConnectorCreate(connector_type="ashby", config={"organization": "x"}),
        ),
        (service.update_connector, bob, company.id, connector_id, ConnectorUpdate(enabled=False)),
        (service.delete_connector, bob, company.id, connector_id),
    ]
    for fn, *args in attempts:
        err = await expect_error(db, fn, *args)
        assert err.status_code == 404, fn.__name__
    assert await call(db, service.list_companies, bob) == []
    unchanged = await call(db, service.get_company, alice, company.id)
    assert (unchanged.name, unchanged.version, unchanged.connectors[0].enabled) == ("Acme", 2, True)
