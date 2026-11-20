"""Connectors and normalization against recorded-shape fixtures (no network).

Fixture shapes follow the vendors' public job-board APIs, checked once against
real boards during development (Greenhouse, Lever demo, Ashby).
"""

from __future__ import annotations

import json
from datetime import datetime, timezone

import pytest

from job_discovery_service.connectors import CONNECTORS, ConnectorError
from job_discovery_service.fetcher import FetchError, Response
from job_discovery_service.normalize import (
    company_key,
    description_hash,
    html_to_text,
    infer_remote,
    job_postings_from_html,
)


class FakeFetcher:
    def __init__(self, pages: dict[str, bytes | FetchError], content_type: str = "application/json") -> None:
        self.pages = pages
        self.content_type = content_type
        self.requested: list[str] = []

    async def _respond(self, url: str) -> Response:
        self.requested.append(url)
        page = self.pages[url]
        if isinstance(page, FetchError):
            raise page
        return Response(url, 200, self.content_type, page)

    async def get(self, url: str, *, accept: str = "*/*") -> Response:
        return await self._respond(url)

    async def get_page(self, url: str) -> Response:
        return await self._respond(url)


GREENHOUSE = {
    "jobs": [
        {
            "id": 8279752,
            "title": "Enterprise Account Executive",
            "absolute_url": "https://job-boards.greenhouse.io/acme/jobs/8279752",
            "location": {"name": "London, United Kingdom"},
            "updated_at": "2026-11-01T10:00:00-04:00",
            "first_published": "2026-10-14T15:52:46-04:00",
            "departments": [{"name": "Sales"}],
            "content": "&lt;p&gt;Join &lt;strong&gt;Acme&lt;/strong&gt;.&lt;/p&gt;"
            "&lt;ul&gt;&lt;li&gt;Sell&lt;/li&gt;&lt;/ul&gt;"
            "&lt;script&gt;alert(1)&lt;/script&gt;",
        },
        {"id": 2, "title": "Remote Engineer", "location": {"name": "Anywhere"}, "content": ""},
        {"id": 3},  # malformed: skipped, not fatal
    ]
}

LEVER = [
    {
        "id": "ad208490-4052-4f91-a57e-433f2d1e484b",
        "text": "Customer Success Manager",
        "hostedUrl": "https://jobs.lever.co/acme/ad208490",
        "categories": {"location": "Atlanta, Georgia", "commitment": "Full-time", "team": "Success"},
        "workplaceType": "onsite",
        "descriptionPlain": "Help customers.",
        "lists": [{"text": "Requirements", "content": "<li>Empathy</li><li>SQL</li>"}],
        "createdAt": 1788200000000,
    },
    {"id": "b", "text": "Data Scientist", "categories": {}, "workplaceType": "remote", "descriptionPlain": "x"},
]

ASHBY = {
    "jobs": [
        {
            "id": "86a60834",
            "title": "Account Executive - EMEA",
            "location": "France",
            "isRemote": True,
            "employmentType": "FullTime",
            "department": "Sales",
            "jobUrl": "https://jobs.ashbyhq.com/acme/86a60834",
            "descriptionPlain": "Sell Ashby.",
            "publishedAt": "2026-09-08T16:27:26.694+00:00",
            "isListed": True,
        },
        {"id": "hidden", "title": "Unlisted", "isListed": False},
    ]
}

CAREERS_HTML = """<html><head>
<script type="application/ld+json">
{"@context": "https://schema.org", "@graph": [
  {"@type": "Organization", "name": "Acme"},
  {"@type": "JobPosting", "title": "Backend Engineer", "identifier": {"@type": "PropertyValue", "value": "BE-1"},
   "url": "https://acme.example/jobs/be-1", "datePosted": "2026-11-01",
   "jobLocation": {"@type": "Place", "address": {"addressLocality": "Riyadh", "addressCountry": "SA"}},
   "employmentType": "FULL_TIME", "description": "<p>Build APIs</p>"},
  {"@type": "JobPosting", "title": "Support Agent", "jobLocationType": "TELECOMMUTE", "description": "Help"}
]}
</script>
<script type="application/ld+json">{ not json </script>
</head><body><h1>Careers</h1></body></html>"""


async def run(kind: str, config: dict, pages: dict, limit: int = 500, content_type: str = "application/json"):
    fetcher = FakeFetcher(pages, content_type)
    return await CONNECTORS[kind](fetcher, config, limit), fetcher


async def test_greenhouse():
    url = "https://boards-api.greenhouse.io/v1/boards/acme/jobs?content=true"
    jobs, _ = await run("greenhouse", {"board_token": "acme"}, {url: json.dumps(GREENHOUSE).encode()})
    assert [j.external_id for j in jobs] == ["8279752", "2"]
    first = jobs[0]
    assert (first.title, first.location, first.department) == (
        "Enterprise Account Executive",
        "London, United Kingdom",
        "Sales",
    )
    assert first.description == "Join Acme.\n\n• Sell"  # escaped HTML -> text, script dropped
    assert first.posted_at == datetime(2026, 10, 14, 19, 52, 46, tzinfo=timezone.utc)
    assert jobs[1].remote is True


async def test_lever():
    url = "https://api.lever.co/v0/postings/acme?mode=json"
    jobs, _ = await run("lever", {"company": "acme"}, {url: json.dumps(LEVER).encode()})
    first, remote = jobs
    assert (first.title, first.employment_type, first.department, first.remote) == (
        "Customer Success Manager",
        "Full-time",
        "Success",
        None,
    )
    assert "Requirements" in first.description and "• SQL" in first.description
    assert first.posted_at.year == 2026
    assert remote.remote is True


async def test_ashby_skips_unlisted_jobs():
    url = "https://api.ashbyhq.com/posting-api/job-board/acme"
    jobs, _ = await run("ashby", {"organization": "acme"}, {url: json.dumps(ASHBY).encode()})
    assert [(j.external_id, j.remote, j.employment_type) for j in jobs] == [("86a60834", True, "FullTime")]


async def test_careers_page_reads_schema_org_job_postings():
    url = "https://acme.example/careers"
    jobs, _ = await run("careers_page", {"url": url}, {url: CAREERS_HTML.encode()}, content_type="text/html")
    assert [(j.external_id, j.title, j.location, j.remote) for j in jobs] == [
        ("BE-1", "Backend Engineer", "Riyadh, SA", None),
        ("https://acme.example/careers", "Support Agent", None, True),
    ]
    assert jobs[0].description == "Build APIs"


async def test_slugs_are_url_encoded_into_the_path():
    url = "https://api.lever.co/v0/postings/a%2Fb?mode=json"
    _, fetcher = await run("lever", {"company": "a/b"}, {url: b"[]"})
    assert fetcher.requested == [url]


async def test_limit_and_malformed_responses():
    url = "https://boards-api.greenhouse.io/v1/boards/acme/jobs?content=true"
    jobs, _ = await run("greenhouse", {"board_token": "acme"}, {url: json.dumps(GREENHOUSE).encode()}, limit=1)
    assert len(jobs) == 1
    for body in (b"<html>maintenance</html>", b'{"unexpected": true}'):
        with pytest.raises(ConnectorError):
            await run("greenhouse", {"board_token": "acme"}, {url: body})


async def test_fetch_errors_propagate_with_their_code():
    url = "https://api.lever.co/v0/postings/gone?mode=json"
    with pytest.raises(FetchError) as err:
        await run("lever", {"company": "gone"}, {url: FetchError("not_found")})
    assert err.value.code == "not_found"


def test_non_https_job_links_are_dropped():
    jobs = job_postings_from_html(
        '<script type="application/ld+json">{"@type":"JobPosting","title":"X","url":"javascript:alert(1)"}</script>',
        "https://acme.example/careers",
    )
    assert jobs[0].url is None


@pytest.mark.parametrize(
    ("markup", "text"),
    [
        ("<p>a</p><p>b</p>", "a\n\nb"),
        ("<style>p{}</style><p>visible</p><script>steal()</script>", "visible"),
        ("<ul><li>one</li><li>two</li></ul>", "• one\n• two"),
        ("Tom &amp; Jerry &lt;3", "Tom & Jerry <3"),
        ("<p>مهندس</p>", "مهندس"),
        ("a​\u0007b", "a​b"),  # control char dropped, zero-width space kept
        ("pay‮usd 0001‬", "payusd 0001"),  # bidi override dropped
    ],
)
def test_html_to_text(markup, text):
    assert html_to_text(markup) == text


def test_duplicate_and_company_keys():
    assert description_hash("Engineer", "Build  things") == description_hash("engineer ", "build things")
    assert description_hash("Engineer", "a") != description_hash("Engineer", "b")
    assert company_key("Acme, Inc.") == company_key("ACME inc")
    assert infer_remote("Remote - EMEA") is True
    assert infer_remote("London") is None
