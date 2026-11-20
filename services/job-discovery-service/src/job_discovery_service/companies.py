"""Reads the caller's companies and job sources from the Company Service.

Uses the caller's own bearer token, so the Company Service applies its usual
ownership checks; this service never reads another service's database.
"""

from __future__ import annotations

import uuid

import httpx
from pydantic import BaseModel, ConfigDict, ValidationError

from careerpilot_common.correlation import CORRELATION_HEADER, get_correlation_id
from careerpilot_common.errors import ServiceError


class _Connector(BaseModel):
    model_config = ConfigDict(extra="ignore")

    id: uuid.UUID
    connector_type: str
    config: dict[str, str]
    enabled: bool


class CompanySources(BaseModel):
    model_config = ConfigDict(extra="ignore")

    id: uuid.UUID
    name: str
    connectors: list[_Connector]


class _CompanyList(BaseModel):
    model_config = ConfigDict(extra="ignore")

    companies: list[CompanySources]


async def fetch_companies(http: httpx.AsyncClient, base_url: str, authorization: str) -> list[CompanySources]:
    try:
        resp = await http.get(
            base_url.rstrip("/") + "/v1/companies",
            headers={"authorization": authorization, CORRELATION_HEADER: get_correlation_id() or ""},
        )
    except httpx.HTTPError as exc:
        raise ServiceError(503, "companies_unavailable", "Your companies can't be loaded right now") from exc
    if resp.status_code == 401:
        raise ServiceError(401, "unauthorized", "Sign in again")
    if resp.status_code != 200:
        raise ServiceError(503, "companies_unavailable", "Your companies can't be loaded right now")
    try:
        return _CompanyList.model_validate(resp.json()).companies
    except (ValueError, ValidationError) as exc:
        raise ServiceError(503, "companies_unavailable", "Your companies can't be loaded right now") from exc
