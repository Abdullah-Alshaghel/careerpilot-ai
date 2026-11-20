"""Requests, responses and the job.discovered event payload."""

from __future__ import annotations

import uuid
from datetime import datetime

from pydantic import BaseModel, ConfigDict, Field

from job_discovery_service.models import ListingStatus, RunStatus, SourceStatus


class SearchRunSourceOut(BaseModel):
    model_config = ConfigDict(extra="forbid", from_attributes=True)

    company_id: uuid.UUID
    company_name: str
    connector_type: str
    status: SourceStatus
    error_code: str | None
    jobs_found: int
    jobs_new: int


class SearchRunOut(BaseModel):
    model_config = ConfigDict(extra="forbid", from_attributes=True)

    id: uuid.UUID
    status: RunStatus
    jobs_found: int
    jobs_new: int
    created_at: datetime
    started_at: datetime | None
    finished_at: datetime | None
    sources: list[SearchRunSourceOut]


class SearchRunList(BaseModel):
    model_config = ConfigDict(extra="forbid")

    runs: list[SearchRunOut]


class JobSummary(BaseModel):
    model_config = ConfigDict(extra="forbid")

    id: uuid.UUID
    company_id: uuid.UUID | None
    company_name: str
    source_type: str
    title: str
    url: str | None
    location: str | None
    remote: bool | None
    employment_type: str | None
    department: str | None
    posted_at: datetime | None
    first_seen_at: datetime
    listing_status: ListingStatus
    duplicate_of_id: uuid.UUID | None
    # First found by the user's most recent completed search.
    is_new: bool


class JobOut(JobSummary):
    description: str


class JobList(BaseModel):
    model_config = ConfigDict(extra="forbid")

    jobs: list[JobSummary]
    total: int


class ImportRequest(BaseModel):
    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True)

    url: str = Field(min_length=8, max_length=2048)


class JobDiscoveredV1(BaseModel):
    """Payload of ``job.discovered`` v1 (consumed by Job Analysis, Phase 6).
    Job text is public but untrusted: consumers treat it as data, never as instructions."""

    model_config = ConfigDict(extra="forbid")

    job_id: uuid.UUID
    company_id: uuid.UUID | None
    company_name: str = Field(max_length=200)
    title: str = Field(max_length=300)
    url: str | None
    location: str | None
    remote: bool | None
    source_type: str = Field(max_length=32)
    description: str = Field(max_length=20_000)
