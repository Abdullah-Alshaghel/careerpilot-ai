"""Company workspace routes. Another user's company is indistinguishable from a
missing one (404)."""

from __future__ import annotations

import uuid
from collections.abc import AsyncIterator
from typing import Annotated

from fastapi import APIRouter, Depends, Request, Response, status
from sqlalchemy.ext.asyncio import AsyncSession

from careerpilot_common.auth import AccessTokenClaims, require_user
from company_service import service
from company_service.schemas import (
    CompanyCreate,
    CompanyList,
    CompanyOut,
    CompanyUpdate,
    ConnectorCreate,
    ConnectorUpdate,
)
from company_service.settings import CompanySettings


async def get_db(request: Request) -> AsyncIterator[AsyncSession]:
    async with request.app.state.sessionmaker() as session:
        yield session


def get_settings(request: Request) -> CompanySettings:
    return request.app.state.settings


current_user = require_user(lambda request: request.app.state.jwks)

Db = Annotated[AsyncSession, Depends(get_db)]
User = Annotated[AccessTokenClaims, Depends(current_user)]
Settings = Annotated[CompanySettings, Depends(get_settings)]


def build_router() -> APIRouter:
    router = APIRouter(prefix="/v1/companies", tags=["companies"])

    @router.get("")
    async def list_companies(user: User, db: Db) -> CompanyList:
        return CompanyList(companies=await service.list_companies(db, user.user_id))

    @router.post("", status_code=status.HTTP_201_CREATED)
    async def create_company(body: CompanyCreate, user: User, db: Db, settings: Settings) -> CompanyOut:
        return await service.create_company(db, settings, user.user_id, body)

    @router.get("/{company_id}")
    async def get_company(company_id: uuid.UUID, user: User, db: Db) -> CompanyOut:
        return await service.get_company(db, user.user_id, company_id)

    @router.patch("/{company_id}")
    async def update_company(company_id: uuid.UUID, body: CompanyUpdate, user: User, db: Db) -> CompanyOut:
        return await service.update_company(db, user.user_id, company_id, body)

    @router.delete("/{company_id}", status_code=status.HTTP_204_NO_CONTENT)
    async def delete_company(company_id: uuid.UUID, user: User, db: Db) -> Response:
        await service.delete_company(db, user.user_id, company_id)
        return Response(status_code=status.HTTP_204_NO_CONTENT)

    @router.post("/{company_id}/connectors", status_code=status.HTTP_201_CREATED)
    async def add_connector(
        company_id: uuid.UUID, body: ConnectorCreate, user: User, db: Db, settings: Settings
    ) -> CompanyOut:
        return await service.add_connector(db, settings, user.user_id, company_id, body)

    @router.patch("/{company_id}/connectors/{connector_id}")
    async def update_connector(
        company_id: uuid.UUID, connector_id: uuid.UUID, body: ConnectorUpdate, user: User, db: Db
    ) -> CompanyOut:
        return await service.update_connector(db, user.user_id, company_id, connector_id, body)

    @router.delete("/{company_id}/connectors/{connector_id}")
    async def delete_connector(company_id: uuid.UUID, connector_id: uuid.UUID, user: User, db: Db) -> CompanyOut:
        return await service.delete_connector(db, user.user_id, company_id, connector_id)

    return router
