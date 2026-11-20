from __future__ import annotations

import uuid
from datetime import date
from types import SimpleNamespace

import pytest
from fastapi.testclient import TestClient
from sqlalchemy.ext.asyncio import create_async_engine

from career_profile_service.main import create_app
from career_profile_service.models import Base, FactType, VerificationStatus
from career_profile_service.parser import parse_cv
from career_profile_service.schemas import InvalidFactValue, dedupe_key, validate_value, validity
from career_profile_service.service import find_conflicts
from career_profile_service.settings import ProfileSettings


@pytest.mark.parametrize("sample", ["english_cv", "arabic_cv"])
def test_every_parser_candidate_satisfies_the_value_schema(sample, request):
    for candidate in parse_cv(request.getfixturevalue(sample)):
        validate_value(FactType(candidate.fact_type), candidate.value)


@pytest.mark.parametrize(
    ("fact_type", "value"),
    [
        (FactType.SKILL, {"name": ""}),
        (FactType.SKILL, {"name": "x" * 81}),
        (FactType.SKILL, {"name": "Go", "admin": True}),
        (FactType.PERSONAL, {"field": "password", "value": "x"}),
        (FactType.EMPLOYMENT, {"title": "Engineer", "start": "March 2020"}),
        (FactType.EMPLOYMENT, {"title": "Engineer", "start": "2020-13"}),
        (FactType.EDUCATION, {"start": "2010"}),
        (FactType.CERTIFICATION, {"name": "CKA", "year": 1800}),
    ],
)
def test_invalid_values_are_rejected(fact_type, value):
    with pytest.raises(InvalidFactValue):
        validate_value(fact_type, value)


def test_dedupe_key_ignores_case_and_punctuation():
    a = dedupe_key(FactType.EMPLOYMENT, {"title": "Senior Engineer", "organization": "Acme, Inc."})
    b = dedupe_key(FactType.EMPLOYMENT, {"title": "senior engineer", "organization": "ACME Inc"})
    assert a == b
    assert dedupe_key(FactType.SKILL, {"name": "Node.js"}) == dedupe_key(FactType.SKILL, {"name": "node js"})
    assert dedupe_key(FactType.SKILL, {"name": "Go"}) != dedupe_key(FactType.LANGUAGE, {"name": "Go"})


def test_validity_dates():
    assert validity(FactType.EMPLOYMENT, {"start": "2020-03", "end": "2022", "current": False}) == (
        date(2020, 3, 1),
        date(2022, 12, 1),
    )
    assert validity(FactType.EMPLOYMENT, {"start": "2020", "end": None, "current": True}) == (date(2020, 1, 1), None)
    assert validity(FactType.SKILL, {"name": "Go"}) == (None, None)


def fact(fact_type, value, status=VerificationStatus.EXTRACTED):
    return SimpleNamespace(
        id=uuid.uuid4(),
        fact_type=fact_type,
        value=value,
        dedupe_key=dedupe_key(fact_type, value),
        verification_status=status,
    )


def test_conflicting_names_are_reported_not_resolved():
    a = fact(FactType.PERSONAL, {"field": "full_name", "value": "Ada Lovelace"})
    b = fact(FactType.PERSONAL, {"field": "full_name", "value": "Ada King"})
    same = fact(FactType.PERSONAL, {"field": "full_name", "value": "ada lovelace"})
    (conflict,) = find_conflicts([a, b, same])
    assert conflict.kind == "multiple_values"
    assert set(conflict.fact_ids) == {a.id, b.id, same.id}


def test_same_job_with_different_dates_is_a_conflict():
    v1 = {"title": "Engineer", "organization": "Acme", "start": "2019", "end": "2021", "current": False}
    v2 = {**v1, "start": "2018"}
    a, b = fact(FactType.EMPLOYMENT, v1), fact(FactType.EMPLOYMENT, v2)
    (conflict,) = find_conflicts([a, b])
    assert conflict.kind == "different_dates"
    assert set(conflict.fact_ids) == {a.id, b.id}


def test_rejected_facts_never_cause_conflicts():
    a = fact(FactType.PERSONAL, {"field": "full_name", "value": "Ada Lovelace"})
    b = fact(FactType.PERSONAL, {"field": "full_name", "value": "Ada King"}, VerificationStatus.REJECTED)
    assert find_conflicts([a, b]) == []


def test_multiple_emails_are_not_a_conflict():
    a = fact(FactType.PERSONAL, {"field": "email", "value": "a@example.com"})
    b = fact(FactType.PERSONAL, {"field": "email", "value": "b@example.com"})
    assert find_conflicts([a, b]) == []


def test_language_level_disagreement():
    a = fact(FactType.LANGUAGE, {"name": "English", "level": "Fluent"})
    b = fact(FactType.LANGUAGE, {"name": "english", "level": "Intermediate"})
    (conflict,) = find_conflicts([a, b])
    assert conflict.kind == "different_details"


# ------------------------------------------------------------------ app


def _client() -> TestClient:
    app = create_app(ProfileSettings(environment="test"))
    app.state.engine = create_async_engine("postgresql+psycopg://nobody:nothing@127.0.0.1:9/none")
    app.state.health_checks = {}
    return TestClient(app)


def test_owned_tables():
    assert set(Base.metadata.tables) == {"career_profiles", "career_facts", "processed_events"}


@pytest.mark.parametrize(
    ("method", "path"),
    [
        ("GET", "/v1/profile"),
        ("POST", "/v1/profile/facts"),
        ("PATCH", f"/v1/profile/facts/{uuid.uuid4()}"),
        ("DELETE", f"/v1/profile/facts/{uuid.uuid4()}"),
        ("POST", "/v1/profile/facts/bulk-status"),
    ],
)
def test_every_route_requires_a_token(method, path):
    with _client() as client:
        resp = client.request(method, path, json={})
    assert resp.status_code == 401
    assert resp.json()["error"]["code"] == "unauthorized"


def test_forged_token_is_rejected_before_any_database_access():
    with _client() as client:
        # No JWKS is reachable in this test, so even a well-formed token can't verify.
        resp = client.get("/v1/profile", headers={"Authorization": "Bearer a.b.c"})
    assert resp.status_code in (401, 503)
