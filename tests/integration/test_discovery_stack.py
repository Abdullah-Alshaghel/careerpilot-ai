"""Job discovery through the real gateway and container. No third-party
traffic: these tests only exercise paths that end before the internet (or at
the fetcher's address check). Run with CAREERPILOT_INTEGRATION=1."""

from __future__ import annotations

import os
import uuid

import httpx
import pytest
import redis

from tests.integration.conftest import GATEWAY, env

pytestmark = [
    pytest.mark.integration,
    pytest.mark.skipif(os.environ.get("CAREERPILOT_INTEGRATION") != "1", reason="set CAREERPILOT_INTEGRATION=1"),
]


def user_client() -> httpx.Client:
    r = redis.Redis(host="127.0.0.1", port=6379, password=env("REDIS_PASSWORD"))
    for key in r.scan_iter("rl:*"):
        r.delete(key)
    r.close()
    client = httpx.Client(base_url=GATEWAY, timeout=60, headers={"x-client-type": "web"})
    tokens = client.post(
        "/v1/auth/register",
        json={"email": f"jobs-{uuid.uuid4().hex[:10]}@example.com", "password": "correct horse battery staple"},
    ).json()
    client.headers["authorization"] = f"Bearer {tokens['access_token']}"
    return client


@pytest.mark.parametrize(
    "url",
    [
        "https://localhost/admin",  # the container itself
        "https://169.254.169.254/latest/meta-data/",  # cloud metadata
        "https://company-service/v1/companies",  # another internal service, by its Docker DNS name
        "https://postgres:5432/",
    ],
)
def test_job_import_cannot_reach_internal_addresses(url):
    api = user_client()
    resp = api.post("/v1/jobs/import", json={"url": url})
    assert resp.status_code == 422, resp.text
    assert resp.json()["error"]["code"] in {"blocked_address", "unsafe_url"}


def test_search_needs_a_job_source_and_jobs_start_empty():
    api = user_client()
    assert api.get("/v1/jobs").json() == {"jobs": [], "total": 0}
    assert api.get("/v1/searches").json() == {"runs": []}
    resp = api.post("/v1/searches")
    assert resp.status_code == 422
    assert resp.json()["error"]["code"] == "no_sources"


def test_discovery_routes_require_a_token():
    with httpx.Client(base_url=GATEWAY, timeout=10) as anonymous:
        for method, path in (("GET", "/v1/jobs"), ("POST", "/v1/searches"), ("POST", "/v1/jobs/import")):
            assert anonymous.request(method, path, json={}).status_code == 401
