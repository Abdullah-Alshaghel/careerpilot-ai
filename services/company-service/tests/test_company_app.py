from __future__ import annotations

import uuid

import pytest
from fastapi.testclient import TestClient
from sqlalchemy.ext.asyncio import create_async_engine

from company_service.main import create_app
from company_service.models import Base, ConnectorType
from company_service.schemas import InvalidConnector, name_key, validate_connector_config
from company_service.settings import CompanySettings


def _client() -> TestClient:
    app = create_app(CompanySettings(environment="test"))
    app.state.engine = create_async_engine("postgresql+psycopg://nobody:nothing@127.0.0.1:9/none")
    app.state.health_checks = {}
    return TestClient(app)


def test_owned_tables():
    assert set(Base.metadata.tables) == {"companies", "connectors"}


@pytest.mark.parametrize(
    ("method", "path"),
    [
        ("GET", "/v1/companies"),
        ("POST", "/v1/companies"),
        ("GET", f"/v1/companies/{uuid.uuid4()}"),
        ("PATCH", f"/v1/companies/{uuid.uuid4()}"),
        ("DELETE", f"/v1/companies/{uuid.uuid4()}"),
        ("POST", f"/v1/companies/{uuid.uuid4()}/connectors"),
        ("PATCH", f"/v1/companies/{uuid.uuid4()}/connectors/{uuid.uuid4()}"),
        ("DELETE", f"/v1/companies/{uuid.uuid4()}/connectors/{uuid.uuid4()}"),
    ],
)
def test_every_route_requires_a_token(method, path):
    with _client() as client:
        resp = client.request(method, path, json={})
    assert resp.status_code == 401


def test_name_key_ignores_case_and_punctuation():
    assert name_key("Acme, Inc.") == name_key("ACME inc") == "acme inc"
    assert name_key("شركة الاتصالات") == "شركة الاتصالات"


@pytest.mark.parametrize(
    ("connector_type", "config", "expected"),
    [
        (ConnectorType.GREENHOUSE, {"board_token": " acme "}, ({"board_token": "acme"}, "acme")),
        (ConnectorType.LEVER, {"company": "Acme-Inc"}, ({"company": "Acme-Inc"}, "acme-inc")),
        (ConnectorType.ASHBY, {"organization": "acme"}, ({"organization": "acme"}, "acme")),
        (
            ConnectorType.CAREERS_PAGE,
            {"url": "acme.com/jobs"},
            ({"url": "https://acme.com/jobs"}, "https://acme.com/jobs"),
        ),
    ],
)
def test_connector_configs_are_normalized(connector_type, config, expected):
    assert validate_connector_config(connector_type, config) == expected


@pytest.mark.parametrize(
    ("connector_type", "config", "code"),
    [
        # Only public identifiers: anything that looks like a credential is refused.
        (ConnectorType.GREENHOUSE, {"board_token": "acme", "api_key": "sk_live_x"}, "invalid_connector_config"),
        (ConnectorType.GREENHOUSE, {"board_token": "../../etc"}, "invalid_connector_config"),
        (ConnectorType.LEVER, {"company": ""}, "invalid_connector_config"),
        (ConnectorType.ASHBY, {"organization": 42}, "invalid_connector_config"),
        (ConnectorType.CAREERS_PAGE, {"url": "http://acme.com"}, "https_required"),
        (ConnectorType.CAREERS_PAGE, {"url": "https://10.0.0.1/"}, "ip_address_not_allowed"),
        (ConnectorType.CAREERS_PAGE, {"url": "https://acme.com", "password": "x"}, "invalid_connector_config"),
    ],
)
def test_bad_connector_configs_are_refused(connector_type, config, code):
    with pytest.raises(InvalidConnector) as err:
        validate_connector_config(connector_type, config)
    assert err.value.code == code
