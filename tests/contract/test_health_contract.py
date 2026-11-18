"""Contract tests: the shared JSON Schemas, the canonical examples and the
Python models must agree. The TypeScript side runs the same examples through
its Zod schemas in packages/validation."""

from __future__ import annotations

import json
from pathlib import Path

import jsonschema
import pytest
from fastapi.testclient import TestClient

from careerpilot_common.app import create_service_app
from careerpilot_common.config import ServiceSettings
from careerpilot_common.events import KNOWN_EVENT_TYPES, EventEnvelope
from careerpilot_common.health import HealthResponse

ROOT = Path(__file__).resolve().parents[2]
API = ROOT / "shared" / "api-contracts"
EVENTS = ROOT / "shared" / "event-schemas"


def _load(path: Path) -> dict:
    return json.loads(path.read_text(encoding="utf-8"))


HEALTH_SCHEMA = _load(API / "schemas" / "health.v1.schema.json")
HEALTH_EXAMPLES = sorted((API / "examples").glob("health.*.json"))


@pytest.mark.parametrize("example", HEALTH_EXAMPLES, ids=lambda p: p.name)
def test_health_examples_match_schema_and_python_model(example: Path):
    payload = _load(example)
    jsonschema.validate(payload, HEALTH_SCHEMA, format_checker=jsonschema.FormatChecker())
    HealthResponse.model_validate(payload)


def test_live_service_response_matches_json_schema():
    async def ok() -> None:
        return None

    app = create_service_app(
        ServiceSettings(service_name="contract-svc", environment="test"), checks=lambda: {"db": ok}
    )
    body = TestClient(app).get("/health/ready").json()
    jsonschema.validate(body, HEALTH_SCHEMA, format_checker=jsonschema.FormatChecker())


def test_python_model_fields_match_schema_fields():
    py = HealthResponse.model_json_schema()
    assert set(py["properties"]) == set(HEALTH_SCHEMA["properties"])
    assert set(py["required"]) == set(HEALTH_SCHEMA["required"])


def test_event_envelope_schema_matches_python_registry():
    schema = _load(EVENTS / "envelope.v1.schema.json")
    assert set(schema["properties"]["event_type"]["enum"]) == set(KNOWN_EVENT_TYPES)
    assert set(schema["properties"]) == set(EventEnvelope.model_fields)
    for example in (EVENTS / "examples").glob("*.json"):
        payload = _load(example)
        jsonschema.validate(payload, schema, format_checker=jsonschema.FormatChecker())
        EventEnvelope.model_validate(payload)
