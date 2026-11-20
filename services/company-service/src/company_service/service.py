"""Company workspace use cases. Every query is scoped by the caller's user id."""

from __future__ import annotations

import uuid

from sqlalchemy import case, func, select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from careerpilot_common.errors import ServiceError
from company_service.models import Company, Connector, ConnectorType, Priority
from company_service.schemas import (
    CompanyCreate,
    CompanyOut,
    CompanyUpdate,
    ConnectorCreate,
    ConnectorOut,
    ConnectorUpdate,
    DetectedConnectorOut,
    InvalidConnector,
    name_key,
    validate_connector_config,
)
from company_service.settings import CompanySettings
from company_service.urls import InvalidUrl, detect_connector, normalize_public_https_url

_URL_MESSAGES = {
    "https_required": "Use an https:// address",
    "credentials_in_url": "The address must not contain a username or password",
    "non_default_port": "The address must not specify a port",
    "ip_address_not_allowed": "Use the site's domain name, not an IP address",
    "private_host": "Use a public website address",
    "invalid_url": "Enter a valid web address",
}


def _url_error(code: str) -> ServiceError:
    return ServiceError(422, code, _URL_MESSAGES.get(code, "Enter a valid web address"))


def _careers_url(raw: str | None) -> str | None:
    if raw is None:
        return None
    try:
        return normalize_public_https_url(raw)
    except InvalidUrl as exc:
        raise _url_error(exc.code) from exc


def company_out(company: Company) -> CompanyOut:
    detected = None
    if company.careers_url:
        found = detect_connector(company.careers_url)
        if found is not None:
            _, key = validate_connector_config(ConnectorType(found.connector_type), found.config)
            already = any(
                c.connector_type == found.connector_type and c.identity_key == key for c in company.connectors
            )
            if not already:
                detected = DetectedConnectorOut(connector_type=found.connector_type, config=found.config)
    return CompanyOut(
        id=company.id,
        name=company.name,
        careers_url=company.careers_url,
        region=company.region,
        priority=company.priority,
        notes=company.notes,
        version=company.version,
        connectors=[ConnectorOut.model_validate(c) for c in company.connectors],
        detected_connector=detected,
        created_at=company.created_at,
        updated_at=company.updated_at,
    )


async def _owned(db: AsyncSession, user_id: uuid.UUID, company_id: uuid.UUID, *, lock: bool = False) -> Company:
    stmt = select(Company).where(Company.id == company_id, Company.user_id == user_id)
    if lock:
        stmt = stmt.with_for_update()
    company = await db.scalar(stmt)
    if company is None:
        raise ServiceError(404, "not_found", "Company not found")
    return company


async def _refreshed(db: AsyncSession, company: Company) -> CompanyOut:
    await db.refresh(company, attribute_names=["connectors", "updated_at", "version"])
    return company_out(company)


def _name_taken() -> ServiceError:
    return ServiceError(409, "company_exists", "You already track a company with this name")


async def list_companies(db: AsyncSession, user_id: uuid.UUID) -> list[CompanyOut]:
    rank = case({Priority.HIGH: 0, Priority.MEDIUM: 1, Priority.LOW: 2}, value=Company.priority)
    rows = await db.scalars(select(Company).where(Company.user_id == user_id).order_by(rank, Company.name_key))
    return [company_out(c) for c in rows]


async def get_company(db: AsyncSession, user_id: uuid.UUID, company_id: uuid.UUID) -> CompanyOut:
    return company_out(await _owned(db, user_id, company_id))


async def create_company(
    db: AsyncSession, settings: CompanySettings, user_id: uuid.UUID, body: CompanyCreate
) -> CompanyOut:
    count = await db.scalar(select(func.count()).select_from(Company).where(Company.user_id == user_id))
    if (count or 0) >= settings.max_companies_per_user:
        raise ServiceError(409, "company_limit", "Company limit reached. Remove a company first")
    company = Company(
        id=uuid.uuid4(),
        user_id=user_id,
        name=body.name,
        name_key=name_key(body.name),
        careers_url=_careers_url(body.careers_url),
        region=body.region,
        priority=body.priority,
        notes=body.notes,
    )
    db.add(company)
    try:
        await db.commit()
    except IntegrityError as exc:
        await db.rollback()
        raise _name_taken() from exc
    return await _refreshed(db, company)


async def update_company(
    db: AsyncSession, user_id: uuid.UUID, company_id: uuid.UUID, body: CompanyUpdate
) -> CompanyOut:
    company = await _owned(db, user_id, company_id, lock=True)
    if body.version is not None and body.version != company.version:
        raise ServiceError(409, "version_conflict", "This company was changed elsewhere. Reload and try again")
    sent = body.model_fields_set - {"version"}
    if "name" in sent and body.name is not None:
        company.name, company.name_key = body.name, name_key(body.name)
    if "careers_url" in sent:
        company.careers_url = _careers_url(body.careers_url)
    if "region" in sent:
        company.region = body.region
    if "priority" in sent and body.priority is not None:
        company.priority = body.priority
    if "notes" in sent:
        company.notes = body.notes
    company.version += 1
    try:
        await db.commit()
    except IntegrityError as exc:
        await db.rollback()
        raise _name_taken() from exc
    return await _refreshed(db, company)


async def delete_company(db: AsyncSession, user_id: uuid.UUID, company_id: uuid.UUID) -> None:
    company = await _owned(db, user_id, company_id, lock=True)
    await db.delete(company)  # connectors cascade
    await db.commit()


# ------------------------------------------------------------- connectors


def _checked_config(connector_type: ConnectorType, config: object) -> tuple[dict, str]:
    try:
        return validate_connector_config(connector_type, config)
    except InvalidConnector as exc:
        if exc.code in _URL_MESSAGES:
            raise _url_error(exc.code) from exc
        raise ServiceError(422, exc.code, "The connector settings are not valid") from exc


def _connector_exists() -> ServiceError:
    return ServiceError(409, "connector_exists", "This connector is already set up")


async def add_connector(
    db: AsyncSession, settings: CompanySettings, user_id: uuid.UUID, company_id: uuid.UUID, body: ConnectorCreate
) -> CompanyOut:
    company = await _owned(db, user_id, company_id, lock=True)
    if len(company.connectors) >= settings.max_connectors_per_company:
        raise ServiceError(409, "connector_limit", "Connector limit reached for this company")
    config, key = _checked_config(body.connector_type, body.config)
    company.connectors.append(
        Connector(connector_type=body.connector_type, config=config, identity_key=key, enabled=body.enabled)
    )
    company.version += 1
    try:
        await db.commit()
    except IntegrityError as exc:
        await db.rollback()
        raise _connector_exists() from exc
    return await _refreshed(db, company)


async def _owned_connector(
    db: AsyncSession, user_id: uuid.UUID, company_id: uuid.UUID, connector_id: uuid.UUID
) -> tuple[Company, Connector]:
    company = await _owned(db, user_id, company_id, lock=True)
    connector = next((c for c in company.connectors if c.id == connector_id), None)
    if connector is None:
        raise ServiceError(404, "not_found", "Connector not found")
    return company, connector


async def update_connector(
    db: AsyncSession, user_id: uuid.UUID, company_id: uuid.UUID, connector_id: uuid.UUID, body: ConnectorUpdate
) -> CompanyOut:
    company, connector = await _owned_connector(db, user_id, company_id, connector_id)
    if body.config is not None:
        connector.config, connector.identity_key = _checked_config(connector.connector_type, body.config)
    if body.enabled is not None:
        connector.enabled = body.enabled
    company.version += 1
    try:
        await db.commit()
    except IntegrityError as exc:
        await db.rollback()
        raise _connector_exists() from exc
    return await _refreshed(db, company)


async def delete_connector(
    db: AsyncSession, user_id: uuid.UUID, company_id: uuid.UUID, connector_id: uuid.UUID
) -> CompanyOut:
    company, connector = await _owned_connector(db, user_id, company_id, connector_id)
    company.connectors.remove(connector)
    company.version += 1
    await db.commit()
    return await _refreshed(db, company)
