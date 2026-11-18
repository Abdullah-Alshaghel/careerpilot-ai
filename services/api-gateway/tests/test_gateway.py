from __future__ import annotations

import httpx
import pytest
from fastapi.testclient import TestClient

from api_gateway.main import create_app
from api_gateway.proxy import resolve_upstream
from api_gateway.settings import GatewaySettings

IDENTITY = "http://identity.test"


class _Stream(httpx.AsyncByteStream):
    """Unread upstream body, like a real network response (``httpx.Response(json=...)``
    pre-reads its content, which a streaming proxy cannot re-stream)."""

    def __init__(self, data: bytes) -> None:
        self.data = data

    async def __aiter__(self):
        yield self.data


class FakeRedis:
    def __init__(self, healthy: bool = True) -> None:
        self.healthy = healthy

    async def ping(self) -> bool:
        if not self.healthy:
            raise ConnectionError("redis down")
        return True

    async def aclose(self) -> None:
        return None


def make_client(handler, *, redis_ok: bool = True, origins: str = "http://localhost:3000") -> TestClient:
    settings = GatewaySettings(
        environment="test",
        identity_service_url=IDENTITY,
        cors_allowed_origins=origins,
        health_check_timeout_seconds=0.5,
    )
    app = create_app(settings)
    app.state.http = httpx.AsyncClient(transport=httpx.MockTransport(handler))
    app.state.redis = FakeRedis(redis_ok)
    return TestClient(app)


def healthy_upstream(request: httpx.Request) -> httpx.Response:
    if request.url.path == "/health/ready":
        return httpx.Response(200, json={"status": "ok"})
    return httpx.Response(404)


def test_public_health_ok():
    with make_client(healthy_upstream) as client:
        resp = client.get("/v1/health")
    assert resp.status_code == 200
    body = resp.json()
    assert body["service"] == "api-gateway"
    assert body["status"] == "ok"
    assert {c["name"]: c["status"] for c in body["checks"]} == {"redis": "up", "identity-service": "up"}


def test_public_health_degraded_is_still_200_but_ready_is_503():
    def down(request: httpx.Request) -> httpx.Response:
        return httpx.Response(503)

    with make_client(down, redis_ok=True) as client:
        public = client.get("/v1/health")
        ready = client.get("/health/ready")
    assert public.status_code == 200
    assert public.json()["status"] == "degraded"
    assert ready.status_code == 503


def test_cors_allows_listed_origin_only():
    with make_client(healthy_upstream, origins="http://localhost:3000,http://tauri.localhost") as client:
        allowed = client.get("/v1/health", headers={"Origin": "http://tauri.localhost"})
        denied = client.get("/v1/health", headers={"Origin": "https://evil.example"})
    assert allowed.headers["access-control-allow-origin"] == "http://tauri.localhost"
    assert "x-correlation-id" in allowed.headers["access-control-expose-headers"]
    assert "access-control-allow-origin" not in denied.headers


def test_wildcard_cors_is_rejected():
    with pytest.raises(ValueError):
        GatewaySettings(cors_allowed_origins="*")


def test_proxy_forwards_path_query_body_and_correlation_id():
    seen: dict = {}

    def upstream(request: httpx.Request) -> httpx.Response:
        seen["url"] = str(request.url)
        seen["headers"] = dict(request.headers)
        seen["body"] = request.read()
        return httpx.Response(
            201,
            headers={"x-upstream": "1", "content-type": "application/json"},
            stream=_Stream(b'{"created": true}'),
        )

    with make_client(upstream) as client:
        resp = client.post(
            "/v1/auth/register?x=1",
            json={"email": "a@b.c"},
            headers={"X-Correlation-ID": "corr-abcdef12", "Authorization": "Bearer t"},
        )
    assert resp.status_code == 201
    assert resp.json() == {"created": True}
    assert resp.headers["x-upstream"] == "1"
    assert resp.headers["x-correlation-id"] == "corr-abcdef12"
    assert seen["url"] == f"{IDENTITY}/v1/auth/register?x=1"
    assert seen["headers"]["x-correlation-id"] == "corr-abcdef12"
    assert seen["headers"]["authorization"] == "Bearer t"
    assert seen["body"] == b'{"email":"a@b.c"}'


def test_proxy_rejects_oversized_body_before_contacting_upstream():
    called = []

    def upstream(request: httpx.Request) -> httpx.Response:
        called.append(request)
        return httpx.Response(200)

    with make_client(upstream) as client:
        resp = client.post("/v1/auth/register", content=b"x" * (12 * 1024 * 1024 + 1))
    assert resp.status_code == 413
    assert resp.json()["error"]["code"] == "payload_too_large"
    assert called == []


def test_proxy_unknown_route_404():
    with make_client(healthy_upstream) as client:
        resp = client.get("/v1/does-not-exist")
    assert resp.status_code == 404
    assert resp.json()["error"]["code"] == "not_found"


def test_proxy_upstream_down_is_502_without_details():
    def boom(request: httpx.Request) -> httpx.Response:
        raise httpx.ConnectError("connection refused to 10.0.0.5")

    with make_client(boom) as client:
        resp = client.get("/v1/auth/me")
    assert resp.status_code == 502
    assert resp.json()["error"]["code"] == "upstream_unavailable"
    assert "10.0.0.5" not in resp.text


def test_proxy_upstream_timeout_is_504():
    def slow(request: httpx.Request) -> httpx.Response:
        raise httpx.ReadTimeout("slow")

    with make_client(slow) as client:
        resp = client.get("/v1/auth/me")
    assert resp.status_code == 504


@pytest.mark.parametrize(
    ("path", "expected"),
    [
        ("/v1/auth", "A"),
        ("/v1/auth/login", "A"),
        ("/v1/authx", None),
        ("/v1/auth/admin/x", "B"),
        ("/v2/auth", None),
    ],
)
def test_resolve_upstream_longest_prefix(path, expected):
    assert resolve_upstream(path, {"/v1/auth": "A", "/v1/auth/admin": "B"}) == expected
