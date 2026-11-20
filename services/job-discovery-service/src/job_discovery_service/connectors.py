"""Connectors: one per permitted public job source (architecture §9).

Greenhouse, Lever and Ashby publish official, unauthenticated job-board APIs
for exactly this use. Careers pages are read only through their schema.org
JobPosting data, subject to robots.txt. Each connector returns at most
``limit`` NormalizedJob objects or raises FetchError/ConnectorError with a
short code.
"""

from __future__ import annotations

import json
from collections.abc import Awaitable, Callable
from datetime import datetime, timezone
from urllib.parse import quote

from job_discovery_service.fetcher import FetchError, SafeFetcher
from job_discovery_service.normalize import NormalizedJob, html_to_text, infer_remote, job_postings_from_html


class ConnectorError(Exception):
    def __init__(self, code: str) -> None:
        super().__init__(code)
        self.code = code


def _json(body: bytes) -> object:
    try:
        return json.loads(body)
    except ValueError as exc:
        raise ConnectorError("unexpected_response") from exc


def _jobs(raw: list, build: Callable[[dict], NormalizedJob], limit: int) -> list[NormalizedJob]:
    jobs: list[NormalizedJob] = []
    for item in raw:
        if len(jobs) >= limit:
            break
        if not isinstance(item, dict):
            continue
        try:
            jobs.append(build(item))
        except (ValueError, TypeError, KeyError):
            continue  # one malformed posting never sinks the whole source
    return jobs


# ------------------------------------------------------------- Greenhouse
# https://developers.greenhouse.io/job-board.html  (GET /v1/boards/{token}/jobs?content=true)


async def greenhouse(fetcher: SafeFetcher, config: dict, limit: int) -> list[NormalizedJob]:
    token = quote(config["board_token"], safe="")
    resp = await fetcher.get(
        f"https://boards-api.greenhouse.io/v1/boards/{token}/jobs?content=true", accept="application/json"
    )
    data = _json(resp.body)
    if not isinstance(data, dict) or not isinstance(data.get("jobs"), list):
        raise ConnectorError("unexpected_response")

    def build(item: dict) -> NormalizedJob:
        location = (item.get("location") or {}).get("name")
        departments = [d.get("name") for d in item.get("departments") or [] if isinstance(d, dict)]
        return NormalizedJob(
            external_id=str(item["id"]),
            title=item["title"],
            url=item.get("absolute_url"),
            location=location,
            remote=infer_remote(location, item["title"]),
            department=", ".join(d for d in departments if d) or None,
            description=html_to_text(item.get("content") or ""),
            posted_at=item.get("first_published") or item.get("updated_at"),
        )

    return _jobs(data["jobs"], build, limit)


# ------------------------------------------------------------------ Lever
# https://github.com/lever/postings-api  (GET /v0/postings/{company}?mode=json)


async def lever(fetcher: SafeFetcher, config: dict, limit: int) -> list[NormalizedJob]:
    company = quote(config["company"], safe="")
    resp = await fetcher.get(f"https://api.lever.co/v0/postings/{company}?mode=json", accept="application/json")
    data = _json(resp.body)
    if not isinstance(data, list):
        raise ConnectorError("unexpected_response")

    def build(item: dict) -> NormalizedJob:
        categories = item.get("categories") or {}
        created = item.get("createdAt")
        description = item.get("descriptionPlain") or html_to_text(item.get("description") or "")
        for section in item.get("lists") or []:
            if isinstance(section, dict):
                description += (
                    "\n\n" + str(section.get("text") or "") + "\n" + html_to_text(section.get("content") or "")
                )
        return NormalizedJob(
            external_id=str(item["id"]),
            title=item["text"],
            url=item.get("hostedUrl"),
            location=categories.get("location"),
            remote=True if item.get("workplaceType") == "remote" else infer_remote(categories.get("location")),
            employment_type=categories.get("commitment"),
            department=categories.get("team") or categories.get("department"),
            description=description.strip()[:20_000],
            posted_at=datetime.fromtimestamp(created / 1000, tz=timezone.utc) if isinstance(created, int) else None,
        )

    return _jobs(data, build, limit)


# ------------------------------------------------------------------ Ashby
# https://developers.ashbyhq.com/docs/public-job-posting-api  (GET /posting-api/job-board/{org})


async def ashby(fetcher: SafeFetcher, config: dict, limit: int) -> list[NormalizedJob]:
    org = quote(config["organization"], safe="")
    resp = await fetcher.get(f"https://api.ashbyhq.com/posting-api/job-board/{org}", accept="application/json")
    data = _json(resp.body)
    if not isinstance(data, dict) or not isinstance(data.get("jobs"), list):
        raise ConnectorError("unexpected_response")

    def build(item: dict) -> NormalizedJob:
        return NormalizedJob(
            external_id=str(item["id"]),
            title=item["title"],
            url=item.get("jobUrl") or item.get("applyUrl"),
            location=item.get("location"),
            remote=bool(item["isRemote"])
            if isinstance(item.get("isRemote"), bool)
            else infer_remote(item.get("location")),
            employment_type=item.get("employmentType"),
            department=item.get("department") or item.get("team"),
            description=item.get("descriptionPlain") or html_to_text(item.get("descriptionHtml") or ""),
            posted_at=item.get("publishedAt"),
        )

    return _jobs([j for j in data["jobs"] if isinstance(j, dict) and j.get("isListed", True)], build, limit)


# ----------------------------------------------------------- careers page


async def careers_page(fetcher: SafeFetcher, config: dict, limit: int) -> list[NormalizedJob]:
    resp = await fetcher.get_page(config["url"])
    if "html" not in resp.content_type and not resp.body.lstrip().startswith(b"<"):
        raise ConnectorError("not_a_web_page")
    return job_postings_from_html(resp.text(), resp.url, limit)


Connector = Callable[[SafeFetcher, dict, int], Awaitable[list[NormalizedJob]]]

CONNECTORS: dict[str, Connector] = {
    "greenhouse": greenhouse,
    "lever": lever,
    "ashby": ashby,
    "careers_page": careers_page,
}

__all__ = ["CONNECTORS", "ConnectorError", "FetchError"]
