"""Structured JSON logging with secret redaction.

Never log passwords, access tokens or private CV contents (architecture §16).
The redaction filter is a safety net — callers must still avoid logging such
data in the first place.
"""

from __future__ import annotations

import json
import logging
import re
import sys
from datetime import datetime, timezone
from typing import Any

from careerpilot_common.correlation import get_correlation_id

REDACTED = "[REDACTED]"
_SENSITIVE_KEYS = re.compile(
    r"pass(word)?|secret|token|authorization|cookie|api[_-]?key|signing[_-]?key|private[_-]?key|credential|cv_text|document_text",
    re.IGNORECASE,
)
# user:password@ inside connection URLs
_URL_CREDENTIALS = re.compile(r"(?P<scheme>[a-z][a-z0-9+.-]*://)[^/\s:@]+:[^/\s@]+@", re.IGNORECASE)
_BEARER = re.compile(r"(bearer\s+)[A-Za-z0-9._~+/=-]+", re.IGNORECASE)

_STANDARD_ATTRS = set(logging.LogRecord("", 0, "", 0, "", (), None).__dict__.keys()) | {
    "message",
    "asctime",
    "taskName",
}


def redact_text(text: str) -> str:
    text = _URL_CREDENTIALS.sub(r"\g<scheme>" + REDACTED + "@", text)
    return _BEARER.sub(r"\1" + REDACTED, text)


def redact_value(key: str, value: Any) -> Any:
    if _SENSITIVE_KEYS.search(key):
        return REDACTED
    if isinstance(value, str):
        return redact_text(value)
    if isinstance(value, dict):
        return {k: redact_value(str(k), v) for k, v in value.items()}
    if isinstance(value, (list, tuple)):
        return [redact_value(key, v) for v in value]
    return value


class JsonFormatter(logging.Formatter):
    def __init__(self, service_name: str, environment: str) -> None:
        super().__init__()
        self.service_name = service_name
        self.environment = environment

    def format(self, record: logging.LogRecord) -> str:
        payload: dict[str, Any] = {
            "timestamp": datetime.fromtimestamp(record.created, tz=timezone.utc).isoformat(),
            "level": record.levelname,
            "logger": record.name,
            "message": redact_text(record.getMessage()),
            "service": self.service_name,
            "environment": self.environment,
        }
        cid = get_correlation_id()
        if cid:
            payload["correlation_id"] = cid
        for key, value in record.__dict__.items():
            if key not in _STANDARD_ATTRS and not key.startswith("_"):
                payload[key] = redact_value(key, value)
        if record.exc_info:
            # Exception type only in structured field; full traceback text is
            # still redacted for URL credentials / bearer tokens.
            payload["exception"] = redact_text(self.formatException(record.exc_info))
        return json.dumps(payload, default=str, ensure_ascii=False)


def configure_logging(service_name: str, environment: str, level: str = "INFO") -> None:
    handler = logging.StreamHandler(sys.stdout)
    handler.setFormatter(JsonFormatter(service_name, environment))
    root = logging.getLogger()
    # Replace only handlers we installed earlier (idempotent; keeps host/test handlers).
    root.handlers[:] = [h for h in root.handlers if not isinstance(h.formatter, JsonFormatter)] + [handler]
    root.setLevel(level.upper())
    # Uvicorn's own access log duplicates ours and logs query strings.
    logging.getLogger("uvicorn.access").disabled = True
    for name in ("uvicorn", "uvicorn.error"):
        logging.getLogger(name).handlers[:] = []
        logging.getLogger(name).propagate = True
