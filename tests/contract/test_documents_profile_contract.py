"""Contracts for documents, the career profile and document events.

Schemas are generated from the services' Pydantic models
(scripts/export-contracts.py); these tests fail when a model changes without
regenerating, when an example breaks, or when the producer's and consumer's
copies of an event payload disagree.
"""

from __future__ import annotations

import json
import subprocess
import sys
from pathlib import Path

import jsonschema
import pytest

from career_profile_service.schemas import ProfileOut
from career_profile_service.worker import DocumentDeletedV1 as ConsumerDeleted
from career_profile_service.worker import DocumentTextExtractedV1 as ConsumerExtracted
from careerpilot_common.events import EventEnvelope
from company_service.schemas import CompanyList, CompanyOut
from document_service.schemas import DocumentDeletedV1, DocumentList, DocumentOut, DocumentTextExtractedV1
from job_discovery_service.schemas import JobDiscoveredV1, JobList, JobOut, SearchRunList, SearchRunOut

ROOT = Path(__file__).resolve().parents[2]
API = ROOT / "shared" / "api-contracts"
EVENTS = ROOT / "shared" / "event-schemas"


def load(path: Path) -> dict:
    return json.loads(path.read_text(encoding="utf-8"))


def validate(payload: dict, schema_path: Path) -> None:
    jsonschema.Draft202012Validator(load(schema_path), format_checker=jsonschema.FormatChecker()).validate(payload)


def test_generated_schemas_are_up_to_date():
    result = subprocess.run(  # noqa: S603
        [sys.executable, str(ROOT / "scripts" / "export-contracts.py"), "--check"], capture_output=True, text=True
    )
    assert result.returncode == 0, result.stderr


@pytest.mark.parametrize(
    ("example", "schema", "model"),
    [
        ("document.json", "document.v1.schema.json", DocumentOut),
        ("document-list.json", "document-list.v1.schema.json", DocumentList),
        ("career-profile.json", "career-profile.v1.schema.json", ProfileOut),
        ("company.json", "company.v1.schema.json", CompanyOut),
        ("company-list.json", "company-list.v1.schema.json", CompanyList),
        ("search-run.json", "search-run.v1.schema.json", SearchRunOut),
        ("search-run-list.json", "search-run-list.v1.schema.json", SearchRunList),
        ("job.json", "job.v1.schema.json", JobOut),
        ("job-list.json", "job-list.v1.schema.json", JobList),
    ],
)
def test_api_examples_match_schema_and_model(example, schema, model):
    payload = load(API / "examples" / example)
    validate(payload, API / "schemas" / schema)
    assert model.model_validate(payload).model_dump(mode="json") == payload


@pytest.mark.parametrize(
    ("example", "schema", "producer", "consumer"),
    [
        (
            "document.text_extracted.v1.json",
            "document.text_extracted.v1.schema.json",
            DocumentTextExtractedV1,
            ConsumerExtracted,
        ),
        ("document.deleted.v1.json", "document.deleted.v1.schema.json", DocumentDeletedV1, ConsumerDeleted),
    ],
)
def test_event_examples_satisfy_envelope_payload_producer_and_consumer(example, schema, producer, consumer):
    event = load(EVENTS / "examples" / example)
    validate(event, EVENTS / "envelope.v1.schema.json")
    envelope = EventEnvelope.model_validate(event)
    validate(event["payload"], EVENTS / "payloads" / schema)
    produced = producer.model_validate(event["payload"])
    consumed = consumer.model_validate(event["payload"])
    # The consumer reads every field the producer sends, with the same values.
    assert consumed.model_dump(mode="json") == produced.model_dump(mode="json")
    assert envelope.routing_key == example.removesuffix(".json")


def test_text_extracted_payload_uses_a_log_redacted_key():
    # careerpilot_common.logging redacts keys named like document_text, so an
    # accidentally logged payload never reveals CV contents.
    from careerpilot_common.logging import REDACTED, redact_value

    payload = load(EVENTS / "examples" / "document.text_extracted.v1.json")["payload"]
    assert redact_value("payload", payload)["document_text"] == REDACTED


def test_job_discovered_example_matches_envelope_and_payload():
    event = load(EVENTS / "examples" / "job.discovered.v1.json")
    validate(event, EVENTS / "envelope.v1.schema.json")
    assert EventEnvelope.model_validate(event).routing_key == "job.discovered.v1"
    validate(event["payload"], EVENTS / "payloads" / "job.discovered.v1.schema.json")
    assert JobDiscoveredV1.model_validate(event["payload"]).model_dump(mode="json") == event["payload"]
