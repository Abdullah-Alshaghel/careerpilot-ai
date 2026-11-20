"""Careers-page URL rules and ATS detection.

These URLs will be fetched by the Job Discovery Service (Phase 5), so they are
validated as if a server were about to request them: HTTPS only, a public DNS
name (never an IP literal, localhost or an internal suffix), default port, no
embedded credentials. This blocks the obvious server-side request forgery
targets at entry time. The fetcher must still re-check the *resolved* address
before connecting (DNS can change; see docs/SECURITY.md).
"""

from __future__ import annotations

import ipaddress
import re
from dataclasses import dataclass
from urllib.parse import urlsplit, urlunsplit

MAX_URL_LENGTH = 2048
_BLOCKED_SUFFIXES = (
    ".localhost",
    ".local",
    ".internal",
    ".intranet",
    ".lan",
    ".home",
    ".corp",
    ".localdomain",
    ".home.arpa",
    ".arpa",
)
_LABEL = re.compile(r"^(?!-)[a-z0-9-]{1,63}(?<!-)$")
_SCHEME_ONLY = re.compile(r"^[A-Za-z][A-Za-z0-9+.-]*:(?!\d)")
_SLUG = re.compile(r"^[A-Za-z0-9][A-Za-z0-9_.-]{0,99}$")


class InvalidUrl(ValueError):
    """``code`` is safe to return to clients."""

    def __init__(self, code: str) -> None:
        super().__init__(code)
        self.code = code


def normalize_public_https_url(raw: str) -> str:
    """Return the normalized URL or raise InvalidUrl."""
    value = raw.strip()
    if not value or len(value) > MAX_URL_LENGTH or any(ch.isspace() or ord(ch) < 32 for ch in value):
        raise InvalidUrl("invalid_url")
    if "://" not in value:
        # "javascript:..." / "mailto:..." name a scheme; "acme.com:443/x" is a host and port.
        if _SCHEME_ONLY.match(value):
            raise InvalidUrl("https_required")
        value = "https://" + value  # "acme.com/careers" is a common way to paste a URL
    try:
        parts = urlsplit(value)
        port = parts.port
    except ValueError as exc:
        raise InvalidUrl("invalid_url") from exc
    if parts.scheme.lower() != "https":
        raise InvalidUrl("https_required")
    if parts.username is not None or parts.password is not None or "@" in parts.netloc:
        raise InvalidUrl("credentials_in_url")
    if port not in (None, 443):
        raise InvalidUrl("non_default_port")
    host = (parts.hostname or "").rstrip(".")
    if not host:
        raise InvalidUrl("invalid_url")
    try:
        ipaddress.ip_address(host.strip("[]"))
    except ValueError:
        pass
    else:
        raise InvalidUrl("ip_address_not_allowed")
    try:
        host = host.encode("idna").decode("ascii").lower()
    except UnicodeError as exc:
        raise InvalidUrl("invalid_url") from exc
    labels = host.split(".")
    if (
        len(labels) < 2
        or not all(_LABEL.match(label) for label in labels)
        or labels[-1].isdigit()
        or host == "localhost"
        or host.endswith(_BLOCKED_SUFFIXES)
    ):
        raise InvalidUrl("private_host")
    path = parts.path or "/"
    return urlunsplit(("https", host, path, parts.query, ""))  # fragments never reach a server


def is_valid_slug(value: str) -> bool:
    return bool(_SLUG.match(value))


@dataclass(frozen=True)
class DetectedConnector:
    connector_type: str
    config: dict[str, str]


# Public job-board hosts of the ATS vendors named in the architecture (§9).
_ATS_PATTERNS = (
    ("greenhouse", re.compile(r"^(?:boards|job-boards)(?:\.eu)?\.greenhouse\.io$"), "board_token"),
    ("lever", re.compile(r"^jobs(?:\.eu)?\.lever\.co$"), "company"),
    ("ashby", re.compile(r"^jobs\.ashbyhq\.com$"), "organization"),
)


def detect_connector(url: str) -> DetectedConnector | None:
    """Recognise a public ATS job board from its URL (``.../<slug>``)."""
    parts = urlsplit(url)
    host = (parts.hostname or "").lower()
    first = next((segment for segment in parts.path.split("/") if segment), "")
    for connector_type, pattern, key in _ATS_PATTERNS:
        if pattern.match(host) and is_valid_slug(first):
            return DetectedConnector(connector_type, {key: first})
    return None
