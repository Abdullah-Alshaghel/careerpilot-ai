from datetime import datetime

import pytest
from pydantic import ValidationError

from careerpilot_common.events import EventEnvelope


def _base(**overrides):
    data = {
        "event_type": "job.discovered",
        "schema_version": 1,
        "correlation_id": "corr-12345678",
        "aggregate_id": "job-1",
        "producer": "job-discovery-service",
        "payload": {},
    }
    data.update(overrides)
    return data


def test_defaults_and_routing_key():
    env = EventEnvelope(**_base())
    assert env.routing_key == "job.discovered.v1"
    assert env.occurred_at.tzinfo is not None


def test_unknown_event_type_rejected():
    with pytest.raises(ValidationError):
        EventEnvelope(**_base(event_type="job.hacked"))


def test_naive_timestamp_rejected():
    with pytest.raises(ValidationError):
        EventEnvelope(**_base(occurred_at=datetime(2026, 1, 1)))


def test_extra_fields_rejected():
    with pytest.raises(ValidationError):
        EventEnvelope(**_base(unexpected=True))
