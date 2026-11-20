"""The only way this service talks to the internet.

Every request goes through ``SafeFetcher``:

- **Connect-time address check.** A custom httpcore network backend resolves
  the host itself and connects only to public unicast addresses. Private,
  loopback, link-local (cloud metadata), CGNAT, multicast and reserved ranges
  are refused at the moment of connecting, so a URL that passed validation
  can't later be pointed at an internal service (DNS rebinding). TLS still
  verifies the certificate against the real hostname.
- **https only, on every hop.** Redirects are followed manually (at most 3),
  each one re-validated.
- **Bounded:** connect/read timeouts, a response-size cap enforced while
  streaming, per-host pacing, a fixed identifying User-Agent.
- **robots.txt** is honoured for crawled pages (careers pages, URL import);
  official public job-board APIs are called as documented by their vendors.
"""

from __future__ import annotations

import asyncio
import ipaddress
import socket
import time
from collections.abc import Awaitable, Callable, Iterable
from dataclasses import dataclass
from urllib.parse import urljoin, urlsplit
from urllib.robotparser import RobotFileParser

import httpcore
import httpx

USER_AGENT = "CareerPilotAI-JobDiscovery/0.1"
MAX_REDIRECTS = 3

Resolver = Callable[[str, int], Awaitable[list[str]]]


class FetchError(Exception):
    """``code`` is safe to store and show (e.g. on a search-run source)."""

    def __init__(self, code: str) -> None:
        super().__init__(code)
        self.code = code


def is_public_address(value: str) -> bool:
    try:
        ip = ipaddress.ip_address(value.split("%", 1)[0])
    except ValueError:
        return False
    if isinstance(ip, ipaddress.IPv6Address) and ip.ipv4_mapped is not None:
        ip = ip.ipv4_mapped
    return ip.is_global and not (ip.is_multicast or ip.is_reserved or ip.is_loopback or ip.is_link_local)


async def system_resolver(host: str, port: int) -> list[str]:
    loop = asyncio.get_running_loop()
    infos = await loop.getaddrinfo(host, port, type=socket.SOCK_STREAM)
    return [info[4][0] for info in infos]


class PublicOnlyBackend(httpcore.AsyncNetworkBackend):
    """Resolves and connects only to public addresses."""

    def __init__(self, resolver: Resolver = system_resolver, inner: httpcore.AsyncNetworkBackend | None = None) -> None:
        self._resolver = resolver
        self._inner = inner or httpcore.AnyIOBackend()

    async def connect_tcp(
        self,
        host: str,
        port: int,
        timeout: float | None = None,
        local_address: str | None = None,
        socket_options: Iterable | None = None,
    ) -> httpcore.AsyncNetworkStream:
        try:
            addresses = await asyncio.wait_for(self._resolver(host, port), timeout or 10)
        except (OSError, asyncio.TimeoutError) as exc:
            raise httpcore.ConnectError(f"dns:{host}") from exc
        public = [a for a in addresses if is_public_address(a)]
        if not public or len(public) != len(addresses):
            # Any non-public answer makes the whole name suspect.
            raise BlockedAddress()
        return await self._inner.connect_tcp(
            public[0], port, timeout=timeout, local_address=local_address, socket_options=socket_options
        )

    async def connect_unix_socket(self, *args, **kwargs):  # pragma: no cover - never used
        raise BlockedAddress()

    async def sleep(self, seconds: float) -> None:
        await self._inner.sleep(seconds)


class BlockedAddress(httpcore.ConnectError):
    pass


class _SafeTransport(httpx.AsyncHTTPTransport):
    def __init__(self, backend: httpcore.AsyncNetworkBackend) -> None:
        super().__init__(http2=False, retries=0)
        # httpx has no public hook for the network backend; replace its pool.
        self._pool = httpcore.AsyncConnectionPool(
            ssl_context=httpx.create_ssl_context(), max_connections=20, network_backend=backend
        )


@dataclass(frozen=True)
class Response:
    url: str
    status_code: int
    content_type: str
    body: bytes

    def text(self) -> str:
        return self.body.decode("utf-8", errors="replace")


class SafeFetcher:
    def __init__(
        self,
        *,
        resolver: Resolver = system_resolver,
        timeout: float = 15.0,
        max_bytes: int = 5 * 1024 * 1024,
        host_interval: float = 1.0,
        transport: httpx.AsyncBaseTransport | None = None,
    ) -> None:
        self._client = httpx.AsyncClient(
            transport=transport or _SafeTransport(PublicOnlyBackend(resolver)),
            timeout=httpx.Timeout(timeout, connect=5.0),
            follow_redirects=False,
            headers={"user-agent": USER_AGENT, "accept-encoding": "gzip, deflate"},
        )
        self._max_bytes = max_bytes
        self._host_interval = host_interval
        self._last_request: dict[str, float] = {}
        self._host_locks: dict[str, asyncio.Lock] = {}
        self._robots: dict[str, RobotFileParser | None] = {}

    async def aclose(self) -> None:
        await self._client.aclose()

    async def _pace(self, host: str) -> None:
        lock = self._host_locks.setdefault(host, asyncio.Lock())
        async with lock:
            wait = self._last_request.get(host, 0) + self._host_interval - time.monotonic()
            if wait > 0:
                await asyncio.sleep(wait)
            self._last_request[host] = time.monotonic()

    async def _once(self, url: str, accept: str) -> httpx.Response | Response:
        parts = urlsplit(url)
        if parts.scheme != "https" or not parts.hostname or parts.username or parts.password:
            raise FetchError("unsafe_url")
        await self._pace(parts.hostname)
        try:
            async with self._client.stream("GET", url, headers={"accept": accept}) as resp:
                if resp.is_redirect:
                    return resp
                chunks, size = [], 0
                async for chunk in resp.aiter_bytes():
                    size += len(chunk)
                    if size > self._max_bytes:
                        raise FetchError("response_too_large")
                    chunks.append(chunk)
                return Response(str(resp.url), resp.status_code, resp.headers.get("content-type", ""), b"".join(chunks))
        except FetchError:
            raise
        except httpx.TimeoutException as exc:
            raise FetchError("timeout") from exc
        except httpx.ConnectError as exc:
            if isinstance(exc.__cause__, BlockedAddress) or "BlockedAddress" in repr(exc.__context__):
                raise FetchError("blocked_address") from exc
            raise FetchError("unreachable") from exc
        except httpx.HTTPError as exc:
            raise FetchError("network_error") from exc

    async def get(self, url: str, *, accept: str = "*/*") -> Response:
        """GET with re-validated redirects. Raises FetchError (never leaks details)."""
        for _ in range(MAX_REDIRECTS + 1):
            result = await self._once(url, accept)
            if isinstance(result, Response):
                if result.status_code == 404:
                    raise FetchError("not_found")
                if result.status_code == 429:
                    raise FetchError("rate_limited_by_source")
                if result.status_code >= 400:
                    raise FetchError(f"http_{result.status_code}")
                return result
            location = result.headers.get("location")
            if not location:
                raise FetchError("bad_redirect")
            url = urljoin(url, location)
        raise FetchError("too_many_redirects")

    async def allowed_by_robots(self, url: str) -> bool:
        parts = urlsplit(url)
        origin = f"https://{parts.netloc}"
        if origin not in self._robots:
            parser: RobotFileParser | None = RobotFileParser()
            try:
                robots = await self.get(origin + "/robots.txt", accept="text/plain")
                parser.parse(robots.text().splitlines())  # type: ignore[union-attr]
            except FetchError as exc:
                if exc.code in {"blocked_address", "unsafe_url"}:
                    raise  # the page itself would be refused for the same reason: say so
                # RFC 9309: robots.txt unavailable (4xx) = no restrictions;
                # unreachable or a server error = assume complete disallow.
                unavailable = exc.code == "not_found" or exc.code.startswith("http_4")
                parser = None if unavailable else _deny_all()
            self._robots[origin] = parser
        parser = self._robots[origin]
        return True if parser is None else parser.can_fetch(USER_AGENT, url)

    async def get_page(self, url: str) -> Response:
        """A crawled page (careers page, imported job link): robots.txt applies."""
        if not await self.allowed_by_robots(url):
            raise FetchError("disallowed_by_robots")
        return await self.get(url, accept="text/html,application/xhtml+xml")


def _deny_all() -> RobotFileParser:
    parser = RobotFileParser()
    parser.parse(["User-agent: *", "Disallow: /"])
    return parser
