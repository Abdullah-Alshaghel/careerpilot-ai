"""Company workspace CRUD through the real gateway (Phase 4 acceptance).
Run with CAREERPILOT_INTEGRATION=1."""

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
    for key in r.scan_iter("rl:*"):  # register limit is tested in test_auth_stack
        r.delete(key)
    r.close()
    client = httpx.Client(base_url=GATEWAY, timeout=30, headers={"x-client-type": "desktop"})
    tokens = client.post(
        "/v1/auth/register",
        json={"email": f"co-{uuid.uuid4().hex[:10]}@example.com", "password": "correct horse battery staple"},
    ).json()
    client.headers["authorization"] = f"Bearer {tokens['access_token']}"
    return client


def test_full_company_crud_with_connectors():
    api = user_client()
    created = api.post(
        "/v1/companies",
        json={"name": "Acme", "careers_url": "https://jobs.lever.co/acme", "priority": "high", "region": "Riyadh"},
    )
    assert created.status_code == 201
    company = created.json()
    assert company["detected_connector"] == {"connector_type": "lever", "config": {"company": "acme"}}

    added = api.post(f"/v1/companies/{company['id']}/connectors", json=company["detected_connector"])
    assert added.status_code == 201
    assert added.json()["detected_connector"] is None
    connector_id = added.json()["connectors"][0]["id"]

    patched = api.patch(
        f"/v1/companies/{company['id']}", json={"notes": "Apply in Q1", "version": added.json()["version"]}
    )
    assert patched.status_code == 200
    stale = api.patch(f"/v1/companies/{company['id']}", json={"notes": "x", "version": 1})
    assert stale.status_code == 409 and stale.json()["error"]["code"] == "version_conflict"

    off = api.patch(f"/v1/companies/{company['id']}/connectors/{connector_id}", json={"enabled": False})
    assert off.json()["connectors"][0]["enabled"] is False

    listing = api.get("/v1/companies").json()["companies"]
    assert [c["name"] for c in listing] == ["Acme"]

    assert api.delete(f"/v1/companies/{company['id']}").status_code == 204
    assert api.get(f"/v1/companies/{company['id']}").status_code == 404


def test_unsafe_url_is_refused_with_its_reason():
    api = user_client()
    resp = api.post("/v1/companies", json={"name": "Metadata", "careers_url": "https://169.254.169.254/"})
    assert resp.status_code == 422
    assert resp.json()["error"]["code"] == "ip_address_not_allowed"


def test_companies_are_private_and_require_a_token():
    alice, bob = user_client(), user_client()
    company = alice.post("/v1/companies", json={"name": "Private Co"}).json()
    for method, path in (
        ("GET", f"/v1/companies/{company['id']}"),
        ("PATCH", f"/v1/companies/{company['id']}"),
        ("DELETE", f"/v1/companies/{company['id']}"),
    ):
        assert bob.request(method, path, json={"name": "x"}).status_code == 404
    assert bob.get("/v1/companies").json()["companies"] == []
    with httpx.Client(base_url=GATEWAY, timeout=10) as anonymous:
        assert anonymous.get("/v1/companies").status_code == 401
