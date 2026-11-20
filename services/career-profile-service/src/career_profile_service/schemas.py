"""Fact value shapes, requests, responses, and the de-duplication / conflict rules."""

from __future__ import annotations

import re
import uuid
from datetime import date, datetime
from typing import Annotated, Literal

from pydantic import BaseModel, ConfigDict, Field, ValidationError, model_validator

from career_profile_service.models import FactType, VerificationStatus

PartialDate = Annotated[str, Field(pattern=r"^(19|20)\d{2}(-(0[1-9]|1[0-2]))?$")]


class _Value(BaseModel):
    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True)


class PersonalValue(_Value):
    field: Literal["full_name", "email", "phone", "location", "link"]
    value: str = Field(min_length=1, max_length=300)


class SummaryValue(_Value):
    text: str = Field(min_length=1, max_length=2000)


class EmploymentValue(_Value):
    title: str = Field(min_length=1, max_length=200)
    organization: str | None = Field(default=None, max_length=200)
    start: PartialDate | None = None
    end: PartialDate | None = None
    current: bool = False
    description: str | None = Field(default=None, max_length=2000)


class EducationValue(_Value):
    degree: str | None = Field(default=None, max_length=200)
    institution: str | None = Field(default=None, max_length=200)
    start: PartialDate | None = None
    end: PartialDate | None = None

    @model_validator(mode="after")
    def _degree_or_institution(self) -> EducationValue:
        if not (self.degree or self.institution):
            raise ValueError("degree or institution is required")
        return self


class SkillValue(_Value):
    name: str = Field(min_length=1, max_length=80)


class CertificationValue(_Value):
    name: str = Field(min_length=1, max_length=200)
    year: int | None = Field(default=None, ge=1950, le=2100)


class LanguageValue(_Value):
    name: str = Field(min_length=1, max_length=60)
    level: str | None = Field(default=None, max_length=60)


VALUE_MODELS: dict[FactType, type[_Value]] = {
    FactType.PERSONAL: PersonalValue,
    FactType.SUMMARY: SummaryValue,
    FactType.EMPLOYMENT: EmploymentValue,
    FactType.EDUCATION: EducationValue,
    FactType.SKILL: SkillValue,
    FactType.CERTIFICATION: CertificationValue,
    FactType.LANGUAGE: LanguageValue,
}


class InvalidFactValue(ValueError):
    pass


def validate_value(fact_type: FactType, value: object) -> dict:
    try:
        return VALUE_MODELS[fact_type].model_validate(value).model_dump(mode="json")
    except ValidationError as exc:
        raise InvalidFactValue(str(exc.errors(include_url=False, include_input=False))) from exc


_NON_WORD = re.compile(r"[\W_]+", re.UNICODE)


def _norm(text: object) -> str:
    return _NON_WORD.sub(" ", str(text or "")).strip().casefold()


def dedupe_key(fact_type: FactType, value: dict) -> str:
    """Two facts with the same key describe the same thing (maybe with different details)."""
    if fact_type == FactType.PERSONAL:
        key = f"{value['field']}:{_norm(value['value'])}"
    elif fact_type == FactType.SUMMARY:
        key = f"summary:{_norm(value['text'])[:200]}"
    elif fact_type == FactType.EMPLOYMENT:
        key = f"employment:{_norm(value['title'])}|{_norm(value.get('organization'))}"
    elif fact_type == FactType.EDUCATION:
        key = f"education:{_norm(value.get('degree'))}|{_norm(value.get('institution'))}"
    else:
        key = f"{fact_type.value}:{_norm(value['name'])}"
    return key[:512]


def detail_signature(fact_type: FactType, value: dict) -> tuple:
    """The details that make two facts about the same thing disagree."""
    if fact_type in (FactType.EMPLOYMENT, FactType.EDUCATION):
        return (value.get("start"), value.get("end"), bool(value.get("current")))
    if fact_type == FactType.LANGUAGE:
        return ((value.get("level") or "").casefold(),)
    return ()


def _iso_to_date(text: str | None, *, end: bool = False) -> date | None:
    if not text:
        return None
    if len(text) == 4:
        return date(int(text), 12 if end else 1, 1)
    year, month = text.split("-")
    return date(int(year), int(month), 1)


def validity(fact_type: FactType, value: dict) -> tuple[date | None, date | None]:
    if fact_type in (FactType.EMPLOYMENT, FactType.EDUCATION):
        end = None if value.get("current") else _iso_to_date(value.get("end"), end=True)
        return _iso_to_date(value.get("start")), end
    return None, None


# ------------------------------------------------------------- requests


class FactCreate(BaseModel):
    model_config = ConfigDict(extra="forbid")

    fact_type: FactType
    value: dict


class FactUpdate(BaseModel):
    model_config = ConfigDict(extra="forbid")

    value: dict | None = None
    verification_status: Literal["user_verified", "rejected"] | None = None

    @model_validator(mode="after")
    def _something(self) -> FactUpdate:
        if self.value is None and self.verification_status is None:
            raise ValueError("value or verification_status is required")
        return self


class BulkStatusUpdate(BaseModel):
    model_config = ConfigDict(extra="forbid")

    fact_ids: list[uuid.UUID] = Field(min_length=1, max_length=500)
    verification_status: Literal["user_verified", "rejected"]


# ------------------------------------------------------------ responses


class FactSource(BaseModel):
    model_config = ConfigDict(extra="forbid")

    document_id: uuid.UUID | None
    name: str | None
    excerpt: str | None


class FactOut(BaseModel):
    model_config = ConfigDict(extra="forbid")

    id: uuid.UUID
    fact_type: FactType
    value: dict
    source: FactSource | None
    confidence: float
    verification_status: VerificationStatus
    valid_from: date | None
    valid_to: date | None
    created_at: datetime
    updated_at: datetime


class Conflict(BaseModel):
    model_config = ConfigDict(extra="forbid")

    kind: Literal["multiple_values", "different_dates", "different_details"]
    fact_type: FactType
    fact_ids: list[uuid.UUID]


class ProfileOut(BaseModel):
    model_config = ConfigDict(extra="forbid")

    id: uuid.UUID
    version: int
    updated_at: datetime
    facts: list[FactOut]
    conflicts: list[Conflict]
