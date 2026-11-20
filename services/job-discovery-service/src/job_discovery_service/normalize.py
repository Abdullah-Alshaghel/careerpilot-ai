"""Turning third-party job data into one safe, normalized shape.

Descriptions arrive as HTML from every source. They are converted to plain
text here and stored only as text: the app never renders third-party markup.
Job text is untrusted data (it may contain prompt-injection attempts aimed at
later AI phases); it is stored and displayed, never interpreted.
"""

from __future__ import annotations

import hashlib
import html
import json
import re
import unicodedata
from datetime import datetime, timezone
from html.parser import HTMLParser
from urllib.parse import urlsplit

from pydantic import BaseModel, ConfigDict, Field, field_validator

MAX_DESCRIPTION = 20_000
_BLOCK_TAGS = {
    "p", "div", "br", "li", "ul", "ol", "h1", "h2", "h3", "h4", "h5", "h6", "tr", "section", "article",
    "header", "footer", "table", "blockquote", "pre", "hr",
}  # fmt: skip
_BIDI_CONTROLS = frozenset(chr(c) for c in (*range(0x202A, 0x202F), *range(0x2066, 0x206A)))
_SKIP_TAGS = {"script", "style", "noscript", "template", "svg", "iframe", "object", "head"}


class _TextExtractor(HTMLParser):
    def __init__(self) -> None:
        super().__init__(convert_charrefs=True)
        self.parts: list[str] = []
        self._skip = 0

    def handle_starttag(self, tag: str, attrs) -> None:
        if tag in _SKIP_TAGS:
            self._skip += 1
        elif tag == "li":
            self.parts.append("\n• ")
        elif tag in _BLOCK_TAGS:
            self.parts.append("\n")

    def handle_endtag(self, tag: str) -> None:
        if tag in _SKIP_TAGS:
            self._skip = max(0, self._skip - 1)
        elif tag in _BLOCK_TAGS and tag != "li":  # the next <li> starts its own line
            self.parts.append("\n")

    def handle_data(self, data: str) -> None:
        if not self._skip:
            self.parts.append(data)


def clean_text(text: str, limit: int | None = None) -> str:
    text = unicodedata.normalize("NFC", text)
    # Drop control characters and bidi embedding/override/isolate characters,
    # which can make displayed text differ from stored text ("Trojan Source").
    # Joiners and LRM/RLM stay: Arabic and Persian text relies on them.
    text = "".join(ch for ch in text if ch in "\n\t" or (unicodedata.category(ch) != "Cc" and ch not in _BIDI_CONTROLS))
    lines = [re.sub(r"[ \t]+", " ", line).strip() for line in text.split("\n")]
    out: list[str] = []
    for line in lines:
        if line or (out and out[-1]):
            out.append(line)
    result = "\n".join(out).strip()
    return result[:limit] if limit else result


def html_to_text(markup: str, limit: int = MAX_DESCRIPTION) -> str:
    """Plain text from HTML (also handles HTML that was itself entity-escaped)."""
    if "&lt;" in markup and "<" not in markup:
        markup = html.unescape(markup)  # Greenhouse returns escaped HTML
    parser = _TextExtractor()
    parser.feed(markup)
    parser.close()
    return clean_text("".join(parser.parts), limit)


def _https_url(value: object) -> str | None:
    if not isinstance(value, str):
        return None
    parts = urlsplit(value.strip())
    if parts.scheme != "https" or not parts.hostname or parts.username or parts.password:
        return None
    return value.strip()[:2048]


class NormalizedJob(BaseModel):
    """One job, as every connector must deliver it."""

    model_config = ConfigDict(str_strip_whitespace=True)

    external_id: str = Field(min_length=1, max_length=200)
    title: str = Field(min_length=1, max_length=300)
    url: str | None = None
    location: str | None = Field(default=None, max_length=200)
    remote: bool | None = None
    employment_type: str | None = Field(default=None, max_length=60)
    department: str | None = Field(default=None, max_length=200)
    description: str = Field(default="", max_length=MAX_DESCRIPTION)
    posted_at: datetime | None = None
    # Only for pages read via schema.org (hiringOrganization); ATS connectors know the company already.
    company: str | None = Field(default=None, max_length=200)

    @field_validator("title", "location", "department", "employment_type", "company", mode="before")
    @classmethod
    def _flatten(cls, value: object) -> object:
        if isinstance(value, str):
            text = clean_text(value).replace("\n", " ")
            return text or None
        return value

    @field_validator("url", mode="before")
    @classmethod
    def _url(cls, value: object) -> str | None:
        return _https_url(value)

    @field_validator("posted_at")
    @classmethod
    def _utc(cls, value: datetime | None) -> datetime | None:
        if value is None:
            return None
        return value.replace(tzinfo=timezone.utc) if value.tzinfo is None else value.astimezone(timezone.utc)


def infer_remote(*hints: object) -> bool | None:
    text = " ".join(str(h) for h in hints if h).casefold()
    if not text:
        return None
    if re.search(r"\b(remote|anywhere|work from home|wfh)\b|عن بعد|عن بُعد", text):
        return True
    return None


def description_hash(title: str, description: str) -> str:
    """Same posting published on two sources -> same hash (case/space-insensitive)."""
    norm = re.sub(r"\s+", " ", f"{title}\n{description}").strip().casefold()
    return hashlib.sha256(norm.encode("utf-8")).hexdigest()


def company_key(name: str) -> str:
    return re.sub(r"[\W_]+", " ", name, flags=re.UNICODE).strip().casefold()[:200]


# ------------------------------------------------------------ schema.org


class _JsonLdScripts(HTMLParser):
    def __init__(self) -> None:
        super().__init__(convert_charrefs=True)
        self.blocks: list[str] = []
        self._inside = False
        self._buffer: list[str] = []

    def handle_starttag(self, tag: str, attrs) -> None:
        if tag == "script" and ("type", "application/ld+json") in [(k, (v or "").lower()) for k, v in attrs]:
            self._inside, self._buffer = True, []

    def handle_endtag(self, tag: str) -> None:
        if tag == "script" and self._inside:
            self.blocks.append("".join(self._buffer))
            self._inside = False

    def handle_data(self, data: str) -> None:
        if self._inside:
            self._buffer.append(data)


def _walk_job_postings(node: object, found: list[dict]) -> None:
    if isinstance(node, list):
        for item in node:
            _walk_job_postings(item, found)
    elif isinstance(node, dict):
        kind = node.get("@type")
        kinds = kind if isinstance(kind, list) else [kind]
        if "JobPosting" in kinds:
            found.append(node)
        for key in ("@graph", "itemListElement", "item", "mainEntity"):
            if key in node:
                _walk_job_postings(node[key], found)


def _place(value: object) -> str | None:
    if isinstance(value, list):
        names = [p for p in (_place(v) for v in value) if p]
        return "; ".join(names) or None
    if isinstance(value, dict):
        address = value.get("address", value)
        if isinstance(address, dict):
            parts = [address.get(k) for k in ("addressLocality", "addressRegion", "addressCountry")]
            parts = [p.get("name") if isinstance(p, dict) else p for p in parts]
            return ", ".join(str(p) for p in parts if p) or None
        if isinstance(address, str):
            return address
    return None


def job_postings_from_html(page: str, page_url: str, limit: int = 500) -> list[NormalizedJob]:
    """schema.org ``JobPosting`` objects embedded as JSON-LD (what search engines read)."""
    reader = _JsonLdScripts()
    reader.feed(page)
    postings: list[dict] = []
    for block in reader.blocks:
        try:
            _walk_job_postings(json.loads(block), postings)
        except ValueError:
            continue
    jobs: list[NormalizedJob] = []
    for posting in postings[:limit]:
        title = posting.get("title")
        if not isinstance(title, str) or not title.strip():
            continue
        identifier = posting.get("identifier")
        if isinstance(identifier, dict):
            identifier = identifier.get("value")
        url = posting.get("url") if isinstance(posting.get("url"), str) else page_url
        try:
            jobs.append(
                NormalizedJob(
                    external_id=str(identifier or url or title)[:200],
                    title=title,
                    url=url,
                    location=_place(posting.get("jobLocation")),
                    remote=True if posting.get("jobLocationType") == "TELECOMMUTE" else infer_remote(title),
                    employment_type=posting.get("employmentType")
                    if isinstance(posting.get("employmentType"), str)
                    else None,
                    department=None,
                    description=html_to_text(str(posting.get("description") or "")),
                    posted_at=posting.get("datePosted") or None,
                    company=(posting.get("hiringOrganization") or {}).get("name")
                    if isinstance(posting.get("hiringOrganization"), dict)
                    else None,
                )
            )
        except ValueError:
            continue
    return jobs
