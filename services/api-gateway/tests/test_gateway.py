from __future__ import annotations

import json
import time
import uuid

import httpx
import jwt
import pytest
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey
from fastapi.testclient import TestClient

from api_gateway.main import create_app
from api_gateway.proxy import resolve_upstream
from api_gateway.settings import GatewaySettings
from careerpilot_common.auth import AUDIENCE, ISSUER, JwksCache, public_jwk

IDENTITY = "http://identity.test"
SIGNING_KEY = Ed25519PrivateKey.generate()
JWKS = {"keys": [public_jwk(SIGNING_KEY.public_key())]}
USER_ID, SESSION_ID = uuid.uuid4(), uuid.uuid4()


def access_token(key: Ed25519PrivateKey = SIGNING_KEY, *, expires_in: int = 600) -> str:
    now = int(time.time())
    claims = {
        "iss": ISSUER,
        "aud": AUDIENCE,
        "sub": str(USER_ID),
        "sid": str(SESSION_ID),
        "iat": now,
        "exp": now + expires_in,
        "jti": uuid.uuid4().hex,
    }
    kid = public_jwk(key.public_key())["kid"]
    return jwt.encode(claims, key, algorithm="EdDSA", headers={"kid": kid, "typ": "at+jwt"})


def auth(token: str | None = None) -> dict[str, str]:
    return {"Authorization": f"Bearer {token or access_token()}"}


class _Stream(httpx.AsyncByteStream):
    """Unread upstream body, like a real network response (``httpx.Response(json=...)``
    pre-reads its content, which a streaming proxy cannot re-stream)."""

    def __init__(self, data: bytes) -> None:
        self.data = data

    async def __aiter__(self):
        yield self.data


class FakeRedis:
    """Just enough of redis.asyncio for health checks and the rate limiter."""

    def __init__(self, healthy: bool = True) -> None:
        self.healthy = healthy
        self.values: dict[str, int] = {}
        self.ttls: dict[str, int] = {}

    def require_up(self) -> None:
        if not self.healthy:
            raise ConnectionError("redis down")

    async def ping(self) -> bool:
        self.require_up()
        return True

    def pipeline(self, transaction: bool = True) -> FakePipeline:
        return FakePipeline(self)

    async def aclose(self) -> None:
        return None


class FakePipeline:
    def __init__(self, redis: FakeRedis) -> None:
        self.redis = redis
        self.ops: list[tuple] = []

    def incr(self, key):
        self.ops.append(("incr", key))

    def expire(self, key, seconds, nx=False):
        self.ops.append(("expire", key, seconds, nx))

    def ttl(self, key):
        self.ops.append(("ttl", key))

    def get(self, key):
        self.ops.append(("get", key))

    async def execute(self):
        self.redis.require_up()
        r, out = self.redis, []
        for op in self.ops:
            if op[0] == "incr":
                r.values[op[1]] = r.values.get(op[1], 0) + 1
                out.append(r.values[op[1]])
            elif op[0] == "expire":
                if not (op[3] and op[1] in r.ttls):
                    r.ttls[op[1]] = op[2]
                out.append(True)
            elif op[0] == "ttl":
                out.append(r.ttls.get(op[1], -2))
            else:
                value = r.values.get(op[1])
                out.append(None if value is None else str(value).encode())
        return out


def make_client(
    handler,
    *,
    redis: FakeRedis | None = None,
    redis_ok: bool = True,
    origins: str = "http://localhost:3000",
    jwks_available: bool = True,
    **overrides,
) -> TestClient:
    settings = GatewaySettings(
        environment="test",
        identity_service_url=IDENTITY,
        cors_allowed_origins=origins,
        health_check_timeout_seconds=0.5,
        **overrides,
    )
    app = create_app(settings)
    app.state.http = httpx.AsyncClient(transport=httpx.MockTransport(handler))
    app.state.redis = redis or FakeRedis(redis_ok)

    async def fetch_jwks():
        if not jwks_available:
            raise httpx.ConnectError("identity down")
        return JWKS

    app.state.jwks = JwksCache(fetch_jwks)
    return TestClient(app)


def healthy_upstream(request: httpx.Request) -> httpx.Response:
    if request.url.path == "/health/ready":
        return httpx.Response(200, json={"status": "ok"})
    return httpx.Response(404)


def echo_upstream(request: httpx.Request) -> httpx.Response:
    """Returns the headers the service received."""
    body = json.dumps({"headers": dict(request.headers)}).encode()
    return httpx.Response(200, headers={"content-type": "application/json"}, stream=_Stream(body))


def login(client: TestClient, email: str = "a@example.com", **kwargs) -> httpx.Response:
    return client.post("/v1/auth/login", json={"email": email, "password": "x"}, **kwargs)


# -------------------------------------------------------------------- health


def test_public_health_ok():
    with make_client(healthy_upstream) as client:
        resp = client.get("/v1/health")
    assert resp.status_code == 200
    body = resp.json()
    assert body["service"] == "api-gateway"
    assert body["status"] == "ok"
    assert {c["name"]: c["status"] for c in body["checks"]} == {
        "redis": "up",
        "identity-service": "up",
        "document-service": "up",
        "career-profile-service": "up",
    }


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
    assert "retry-after" in allowed.headers["access-control-expose-headers"]
    assert "access-control-allow-origin" not in denied.headers


def test_wildcard_cors_is_rejected():
    with pytest.raises(ValueError):
        GatewaySettings(cors_allowed_origins="*")


# --------------------------------------------------------------------- proxy


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
        resp = client.get("/v1/users/me", headers=auth())
    assert resp.status_code == 502
    assert resp.json()["error"]["code"] == "upstream_unavailable"
    assert "10.0.0.5" not in resp.text


def test_proxy_upstream_timeout_is_504():
    def slow(request: httpx.Request) -> httpx.Response:
        raise httpx.ReadTimeout("slow")

    with make_client(slow) as client:
        resp = client.get("/v1/users/me", headers=auth())
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


def test_uploads_get_a_longer_upstream_timeout():
    seen: dict[str, object] = {}

    def upstream(request: httpx.Request) -> httpx.Response:
        seen[f"{request.method} {request.url.path}"] = request.extensions["timeout"]["read"]
        return httpx.Response(200, stream=_Stream(b"{}"))

    with make_client(upstream, upstream_timeout_seconds=15, upload_timeout_seconds=60) as client:
        client.post("/v1/documents", headers=auth(), files={"file": ("cv.txt", b"x")})
        client.get("/v1/documents", headers=auth())
    assert seen["POST /v1/documents"] == 60
    assert seen["GET /v1/documents"] != 60  # everything else keeps the client default


# ------------------------------------------------------------ authentication


def test_protected_route_without_token_is_401_at_the_edge():
    called = []

    def upstream(request):
        called.append(request)
        return httpx.Response(200)

    with make_client(upstream) as client:
        resp = client.get("/v1/users/me")
    assert resp.status_code == 401
    assert resp.headers["www-authenticate"] == "Bearer"
    assert resp.json()["error"]["code"] == "unauthorized"
    assert called == []


@pytest.mark.parametrize(
    ("token", "code"),
    [
        (access_token(expires_in=-120), "token_expired"),
        (access_token(Ed25519PrivateKey.generate()), "invalid_token"),
        ("not.a.jwt", "invalid_token"),
    ],
)
def test_bad_tokens_are_rejected_at_the_edge(token, code):
    with make_client(echo_upstream) as client:
        resp = client.get("/v1/users/me", headers=auth(token))
    assert resp.status_code == 401
    assert resp.json()["error"]["code"] == code


def test_valid_token_is_forwarded_with_verified_identity_and_spoofed_headers_dropped():
    with make_client(echo_upstream) as client:
        resp = client.get(
            "/v1/users/me",
            headers={
                **auth(),
                "X-Authenticated-User-Id": str(uuid.uuid4()),
                "X-User-Id": str(uuid.uuid4()),
                "X-Forwarded-For": "6.6.6.6",
            },
        )
    assert resp.status_code == 200
    seen = resp.json()["headers"]
    assert seen["x-authenticated-user-id"] == str(USER_ID)
    assert seen["x-authenticated-session-id"] == str(SESSION_ID)
    assert "x-user-id" not in seen
    assert seen["x-forwarded-for"] != "6.6.6.6"
    assert seen["authorization"].startswith("Bearer ")  # services verify it again


def test_spoofed_identity_header_is_dropped_on_public_routes_too():
    with make_client(echo_upstream) as client:
        resp = login(client, headers={"X-Authenticated-User-Id": str(uuid.uuid4())})
    assert "x-authenticated-user-id" not in resp.json()["headers"]


def test_auth_unavailable_when_keys_cannot_be_loaded():
    with make_client(echo_upstream, jwks_available=False) as client:
        resp = client.get("/v1/users/me", headers=auth())
    assert resp.status_code == 503
    assert resp.json()["error"]["code"] == "auth_unavailable"


def test_unauthenticated_upload_is_rejected_before_the_body_is_buffered():
    with make_client(echo_upstream) as client:
        resp = client.post("/v1/users/me", content=b"x" * (12 * 1024 * 1024 + 1))
    assert resp.status_code == 401


# ------------------------------------------------------------- rate limiting


def test_login_is_rate_limited_per_ip_with_retry_after():
    with make_client(echo_upstream, rate_limit_login_per_ip=3) as client:
        statuses = [login(client, f"u{i}@example.com").status_code for i in range(3)]
        blocked = login(client, "z@example.com")
    assert statuses == [200, 200, 200]
    assert blocked.status_code == 429
    assert blocked.json()["error"]["code"] == "rate_limited"
    assert int(blocked.headers["retry-after"]) > 0


def test_failed_logins_lock_the_account_not_the_ip():
    def identity(request: httpx.Request) -> httpx.Response:
        return httpx.Response(401, stream=_Stream(b'{"error": {"code": "invalid_credentials"}}'))

    with make_client(identity, rate_limit_login_failures_per_account=3) as client:
        attempts = [login(client, "victim@example.com").status_code for _ in range(3)]
        # Case/whitespace variants of the same address share the counter.
        locked = login(client, " Victim@Example.com")
        other = login(client, "someone@example.com")
    assert attempts == [401, 401, 401]
    assert locked.status_code == 429
    assert other.status_code == 401


def test_successful_logins_do_not_count_as_failures():
    with make_client(echo_upstream, rate_limit_login_failures_per_account=2) as client:
        assert [login(client).status_code for _ in range(4)] == [200] * 4


def test_register_is_rate_limited_per_ip():
    with make_client(echo_upstream, rate_limit_register_per_ip=2) as client:
        codes = [client.post("/v1/auth/register", json={}).status_code for _ in range(3)]
    assert codes == [200, 200, 429]


def test_rate_limit_keys_do_not_contain_emails_or_ips():
    redis = FakeRedis()
    with make_client(echo_upstream, redis=redis) as client:
        login(client, "private@example.com")
    assert redis.values
    assert not any("private" in k or "testclient" in k for k in redis.values)


def test_rate_limiter_fails_open_when_redis_is_down():
    with make_client(echo_upstream, redis_ok=False, rate_limit_login_per_ip=1) as client:
        assert [login(client).status_code for _ in range(3)] == [200] * 3


def test_forwarded_for_is_ignored_unless_the_peer_is_a_trusted_proxy():
    # TestClient's peer address is "testclient", never a trusted proxy, so a
    # client rotating X-Forwarded-For can't evade the per-IP limit.
    with make_client(echo_upstream, trusted_proxy_cidrs="10.0.0.0/8", rate_limit_login_per_ip=1) as client:
        first = login(client, headers={"X-Forwarded-For": "1.1.1.1"})
        second = login(client, headers={"X-Forwarded-For": "2.2.2.2"})
    assert first.status_code == 200
    assert second.status_code == 429
