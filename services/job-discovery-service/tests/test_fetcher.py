"""SafeFetcher against a scripted network: real httpx/httpcore stack, fake DNS
and fake sockets. No real network access."""

from __future__ import annotations

import httpcore
import pytest

from job_discovery_service.fetcher import FetchError, PublicOnlyBackend, SafeFetcher, _SafeTransport, is_public_address

PUBLIC_A, PUBLIC_B = "93.184.215.14", "151.101.1.69"


def http(status: int, body: bytes = b"", headers: dict[str, str] | None = None) -> bytes:
    reason = {200: "OK", 301: "Moved", 302: "Found", 404: "Not Found", 500: "Error"}.get(status, "X")
    head = {"content-length": str(len(body)), "connection": "close", "content-type": "text/html", **(headers or {})}
    lines = [f"HTTP/1.1 {status} {reason}"] + [f"{k}: {v}" for k, v in head.items()]
    return ("\r\n".join(lines) + "\r\n\r\n").encode() + body


class Network(httpcore.AsyncNetworkBackend):
    """Fake sockets: each connection to an IP replays that IP's next response."""

    def __init__(self, responses: dict[str, list[bytes]]) -> None:
        self.responses = responses
        self.connected: list[str] = []

    async def connect_tcp(self, host, port, timeout=None, local_address=None, socket_options=None):
        self.connected.append(host)
        return httpcore.AsyncMockStream([self.responses[host].pop(0)])

    async def connect_unix_socket(self, *args, **kwargs):
        raise AssertionError("unused")

    async def sleep(self, seconds):
        return None


def fetcher(dns: dict[str, list[str]], network: Network, **kwargs) -> SafeFetcher:
    async def resolve(host: str, port: int) -> list[str]:
        return dns[host]

    return SafeFetcher(transport=_SafeTransport(PublicOnlyBackend(resolve, inner=network)), host_interval=0, **kwargs)


@pytest.mark.parametrize(
    ("address", "public"),
    [
        ("93.184.215.14", True),
        ("2606:2800:220:1:248:1893:25c8:1946", True),
        ("127.0.0.1", False),
        ("10.1.2.3", False),
        ("172.16.0.1", False),
        ("192.168.1.1", False),
        ("169.254.169.254", False),  # cloud metadata
        ("100.64.0.1", False),  # carrier-grade NAT
        ("0.0.0.0", False),  # noqa: S104 - an address we must refuse, not bind
        ("224.0.0.1", False),
        ("::1", False),
        ("fe80::1", False),
        ("fd00::1", False),
        ("::ffff:127.0.0.1", False),  # IPv4-mapped loopback
        ("not-an-ip", False),
    ],
)
def test_is_public_address(address, public):
    assert is_public_address(address) is public


async def test_fetches_a_public_https_page():
    net = Network({PUBLIC_A: [http(200, b"<h1>Jobs</h1>")]})
    f = fetcher({"jobs.example.com": [PUBLIC_A]}, net)
    resp = await f.get("https://jobs.example.com/careers")
    assert (resp.status_code, resp.body) == (200, b"<h1>Jobs</h1>")
    assert net.connected == [PUBLIC_A]
    await f.aclose()


@pytest.mark.parametrize(
    "answers",
    [["127.0.0.1"], ["169.254.169.254"], ["10.0.0.5"], [PUBLIC_A, "10.0.0.5"], ["::1"]],
)
async def test_names_resolving_to_internal_addresses_are_never_connected(answers):
    net = Network({})
    f = fetcher({"rebind.example.com": answers}, net)
    with pytest.raises(FetchError) as err:
        await f.get("https://rebind.example.com/")
    assert err.value.code == "blocked_address"
    assert net.connected == []
    await f.aclose()


@pytest.mark.parametrize(
    "url", ["http://jobs.example.com/", "ftp://jobs.example.com/", "https://u:p@jobs.example.com/"]
)
async def test_only_plain_https_urls(url):
    f = fetcher({}, Network({}))
    with pytest.raises(FetchError) as err:
        await f.get(url)
    assert err.value.code == "unsafe_url"
    await f.aclose()


async def test_redirects_are_revalidated_on_every_hop():
    net = Network({PUBLIC_A: [http(302, headers={"location": "https://internal.example.com/admin"})]})
    f = fetcher({"jobs.example.com": [PUBLIC_A], "internal.example.com": ["192.168.0.10"]}, net)
    with pytest.raises(FetchError) as err:
        await f.get("https://jobs.example.com/")
    assert err.value.code == "blocked_address"
    assert net.connected == [PUBLIC_A]
    await f.aclose()


async def test_redirect_to_plain_http_is_refused():
    net = Network({PUBLIC_A: [http(301, headers={"location": "http://jobs.example.com/"})]})
    f = fetcher({"jobs.example.com": [PUBLIC_A]}, net)
    with pytest.raises(FetchError) as err:
        await f.get("https://jobs.example.com/")
    assert err.value.code == "unsafe_url"
    await f.aclose()


async def test_follows_a_safe_redirect_and_caps_the_chain():
    ok = Network(
        {
            PUBLIC_A: [http(302, headers={"location": "/careers"}), http(200, b"done")],
        }
    )
    f = fetcher({"jobs.example.com": [PUBLIC_A]}, ok)
    assert (await f.get("https://jobs.example.com/")).body == b"done"
    await f.aclose()

    loop = Network({PUBLIC_A: [http(302, headers={"location": "/again"})] * 10})
    f = fetcher({"jobs.example.com": [PUBLIC_A]}, loop)
    with pytest.raises(FetchError) as err:
        await f.get("https://jobs.example.com/")
    assert err.value.code == "too_many_redirects"
    await f.aclose()


async def test_response_size_is_capped_while_streaming():
    net = Network({PUBLIC_A: [http(200, b"x" * 5000)]})
    f = fetcher({"jobs.example.com": [PUBLIC_A]}, net, max_bytes=1000)
    with pytest.raises(FetchError) as err:
        await f.get("https://jobs.example.com/")
    assert err.value.code == "response_too_large"
    await f.aclose()


@pytest.mark.parametrize(("status", "code"), [(404, "not_found"), (500, "http_500")])
async def test_http_errors_become_codes(status, code):
    f = fetcher({"jobs.example.com": [PUBLIC_A]}, Network({PUBLIC_A: [http(status)]}))
    with pytest.raises(FetchError) as err:
        await f.get("https://jobs.example.com/")
    assert err.value.code == code
    await f.aclose()


async def test_robots_txt_disallow_is_honoured():
    robots = b"User-agent: *\nDisallow: /private/\n"
    net = Network({PUBLIC_A: [http(200, robots, {"content-type": "text/plain"}), http(200, b"public page")]})
    f = fetcher({"acme.example.com": [PUBLIC_A]}, net)
    with pytest.raises(FetchError) as err:
        await f.get_page("https://acme.example.com/private/jobs")
    assert err.value.code == "disallowed_by_robots"
    assert (await f.get_page("https://acme.example.com/careers")).body == b"public page"
    await f.aclose()


async def test_internal_host_is_reported_as_blocked_not_as_robots():
    net = Network({})
    f = fetcher({"intranet.example.com": ["10.0.0.7"]}, net)
    with pytest.raises(FetchError) as err:
        await f.get_page("https://intranet.example.com/jobs")
    assert err.value.code == "blocked_address"
    assert net.connected == []
    await f.aclose()


@pytest.mark.parametrize(("robots_status", "allowed"), [(404, True), (500, False)])
async def test_missing_robots_allows_but_a_broken_one_does_not(robots_status, allowed):
    net = Network({PUBLIC_A: [http(robots_status), http(200, b"page")]})
    f = fetcher({"acme.example.com": [PUBLIC_A]}, net)
    assert await f.allowed_by_robots("https://acme.example.com/careers") is allowed
    await f.aclose()
