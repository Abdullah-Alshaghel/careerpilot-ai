import json
import logging

from careerpilot_common.correlation import correlation_id_var
from careerpilot_common.logging import REDACTED, JsonFormatter, redact_text


def _format(record_kwargs: dict, msg: str = "hello") -> dict:
    record = logging.LogRecord("test", logging.INFO, __file__, 1, msg, (), None)
    for k, v in record_kwargs.items():
        setattr(record, k, v)
    return json.loads(JsonFormatter("svc", "test").format(record))


def test_sensitive_extra_fields_are_redacted():
    out = _format({"password": "hunter2", "access_token": "abc", "cv_text": "my private cv", "user_id": "u1"})
    assert out["password"] == REDACTED
    assert out["access_token"] == REDACTED
    assert out["cv_text"] == REDACTED
    assert out["user_id"] == "u1"


def test_nested_sensitive_keys_are_redacted():
    out = _format({"ctx": {"Authorization": "Bearer xyz", "ok": 1}})
    assert out["ctx"] == {"Authorization": REDACTED, "ok": 1}


def test_url_credentials_and_bearer_tokens_are_redacted_in_messages():
    text = redact_text("connect postgresql+psycopg://svc:s3cret@db:5432/x with Bearer eyJhbGciOi.abc")
    assert "s3cret" not in text
    assert "eyJhbGciOi" not in text
    assert "postgresql+psycopg://[REDACTED]@db:5432/x" in text


def test_correlation_id_is_included():
    token = correlation_id_var.set("cid-12345678")
    try:
        out = _format({})
    finally:
        correlation_id_var.reset(token)
    assert out["correlation_id"] == "cid-12345678"
    assert out["service"] == "svc"
