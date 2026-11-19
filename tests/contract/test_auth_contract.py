"""Contract tests for identity endpoints: JSON Schemas, canonical examples and
the Identity Service's Pydantic models must agree. The TypeScript side checks
the same examples with its Zod schemas (packages/validation)."""

from __future__ import annotations

import json
from pathlib import Path

import jsonschema
import pytest
from referencing import Registry, Resource

from identity_service.schemas import TokenResponse, UserOut

API = Path(__file__).resolve().parents[2] / "shared" / "api-contracts"


def _load(path: Path) -> dict:
    return json.loads(path.read_text(encoding="utf-8"))


SCHEMAS = {p.name: _load(p) for p in (API / "schemas").glob("*.schema.json")}
REGISTRY = Registry().with_resources((s["$id"], Resource.from_contents(s)) for s in SCHEMAS.values())


def validate(payload: dict, schema_file: str) -> None:
    validator = jsonschema.Draft202012Validator(
        SCHEMAS[schema_file], registry=REGISTRY, format_checker=jsonschema.FormatChecker()
    )
    validator.validate(payload)


@pytest.mark.parametrize(
    ("example", "schema_file", "model"),
    [
        ("user.json", "user.v1.schema.json", UserOut),
        ("auth-token.json", "auth-token.v1.schema.json", TokenResponse),
        ("auth-session.json", "auth-session.v1.schema.json", None),
    ],
)
def test_examples_match_schema_and_python_model(example, schema_file, model):
    payload = _load(API / "examples" / example)
    validate(payload, schema_file)
    if model is not None:
        assert model.model_validate(payload).model_dump(mode="json") == payload


@pytest.mark.parametrize(
    ("model", "schema_file"), [(UserOut, "user.v1.schema.json"), (TokenResponse, "auth-token.v1.schema.json")]
)
def test_python_model_fields_match_schema_fields(model, schema_file):
    schema = SCHEMAS[schema_file]
    assert set(model.model_fields) == set(schema["properties"])
    assert set(schema["required"]) <= set(model.model_fields)


def test_browser_session_is_the_token_response_minus_refresh_fields():
    token, session = SCHEMAS["auth-token.v1.schema.json"], SCHEMAS["auth-session.v1.schema.json"]
    assert set(session["properties"]) == {k for k in token["properties"] if not k.startswith("refresh")}


def test_browser_session_schema_forbids_refresh_tokens():
    payload = _load(API / "examples" / "auth-session.json")
    with pytest.raises(jsonschema.ValidationError):
        validate({**payload, "refresh_token": "x" * 43}, "auth-session.v1.schema.json")
