"""Requests, responses and per-connector configuration rules."""

from __future__ import annotations

import re
import uuid
from datetime import datetime
from typing import Annotated

from pydantic import AfterValidator, BaseModel, ConfigDict, Field, model_validator

from company_service.models import ConnectorType, Priority
from company_service.urls import InvalidUrl, is_valid_slug, normalize_public_https_url

_NON_WORD = re.compile(r"[\W_]+", re.UNICODE)


def name_key(name: str) -> str:
    """ "Acme, Inc." and "ACME Inc" are the same company for duplicate checks."""
    return _NON_WORD.sub(" ", name).strip().casefold()[:200]


def _blank_to_none(value: str | None) -> str | None:
    return value if value else None


CompanyName = Annotated[str, Field(min_length=1, max_length=200)]
# Normalized and safety-checked in the service layer, which returns the specific
# reason code (https_required, private_host, ...) for the UI to explain.
CareersUrl = Annotated[str | None, Field(default=None, max_length=2100), AfterValidator(_blank_to_none)]
Region = Annotated[str | None, Field(default=None, max_length=100), AfterValidator(_blank_to_none)]
Notes = Annotated[str | None, Field(default=None, max_length=2000), AfterValidator(_blank_to_none)]


class _Request(BaseModel):
    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True)


class CompanyCreate(_Request):
    name: CompanyName
    careers_url: CareersUrl = None
    region: Region = None
    priority: Priority = Priority.MEDIUM
    notes: Notes = None


class CompanyUpdate(_Request):
    name: CompanyName | None = None
    careers_url: CareersUrl = None
    region: Region = None
    priority: Priority | None = None
    notes: Notes = None
    # If sent, the update applies only to this version (else 409 version_conflict).
    version: int | None = Field(default=None, ge=1)


# ------------------------------------------------------------- connectors

_SLUG_KEYS = {
    ConnectorType.GREENHOUSE: "board_token",
    ConnectorType.LEVER: "company",
    ConnectorType.ASHBY: "organization",
}


class InvalidConnector(ValueError):
    def __init__(self, code: str) -> None:
        super().__init__(code)
        self.code = code


def validate_connector_config(connector_type: ConnectorType, config: object) -> tuple[dict, str]:
    """Return ``(clean_config, identity_key)``. Only public identifiers are accepted."""
    if not isinstance(config, dict):
        raise InvalidConnector("invalid_connector_config")
    if connector_type == ConnectorType.CAREERS_PAGE:
        if set(config) != {"url"} or not isinstance(config["url"], str):
            raise InvalidConnector("invalid_connector_config")
        try:
            url = normalize_public_https_url(config["url"])
        except InvalidUrl as exc:
            raise InvalidConnector(exc.code) from exc
        return {"url": url}, url
    key = _SLUG_KEYS[connector_type]
    if set(config) != {key} or not isinstance(config[key], str) or not is_valid_slug(config[key].strip()):
        raise InvalidConnector("invalid_connector_config")
    slug = config[key].strip()
    return {key: slug}, slug.casefold()


class ConnectorCreate(_Request):
    connector_type: ConnectorType
    config: dict
    enabled: bool = True


class ConnectorUpdate(_Request):
    config: dict | None = None
    enabled: bool | None = None

    @model_validator(mode="after")
    def _something(self) -> ConnectorUpdate:
        if self.config is None and self.enabled is None:
            raise ValueError("config or enabled is required")
        return self


# -------------------------------------------------------------- responses


class ConnectorOut(BaseModel):
    model_config = ConfigDict(extra="forbid", from_attributes=True)

    id: uuid.UUID
    connector_type: ConnectorType
    config: dict
    enabled: bool
    created_at: datetime
    updated_at: datetime


class DetectedConnectorOut(BaseModel):
    model_config = ConfigDict(extra="forbid")

    connector_type: ConnectorType
    config: dict


class CompanyOut(BaseModel):
    model_config = ConfigDict(extra="forbid")

    id: uuid.UUID
    name: str
    careers_url: str | None
    region: str | None
    priority: Priority
    notes: str | None
    version: int
    connectors: list[ConnectorOut]
    # A public ATS board recognised from careers_url that isn't a connector yet.
    detected_connector: DetectedConnectorOut | None
    created_at: datetime
    updated_at: datetime


class CompanyList(BaseModel):
    model_config = ConfigDict(extra="forbid")

    companies: list[CompanyOut]
