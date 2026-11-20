from __future__ import annotations

from pydantic import Field, SecretStr

from careerpilot_common.config import ServiceSettings


class CompanySettings(ServiceSettings):
    service_name: str = "company-service"

    # postgresql+psycopg://companies_svc:<password>@postgres:5432/companies
    database_url: SecretStr = SecretStr("postgresql+psycopg://companies_svc:companies@127.0.0.1:55432/companies")
    db_pool_size: int = 5
    identity_jwks_url: str = "http://127.0.0.1:8101/.well-known/jwks.json"

    max_companies_per_user: int = Field(default=200, ge=1)
    max_connectors_per_company: int = Field(default=10, ge=1)
