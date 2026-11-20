"""Career profile routes. Everything is scoped to the caller's verified user id."""

from __future__ import annotations

import uuid
from collections.abc import AsyncIterator
from typing import Annotated

from fastapi import APIRouter, Depends, Request, Response, status
from sqlalchemy.ext.asyncio import AsyncSession

from career_profile_service import service
from career_profile_service.schemas import BulkStatusUpdate, FactCreate, FactOut, FactUpdate, ProfileOut
from careerpilot_common.auth import AccessTokenClaims, require_user


async def get_db(request: Request) -> AsyncIterator[AsyncSession]:
    async with request.app.state.sessionmaker() as session:
        yield session


current_user = require_user(lambda request: request.app.state.jwks)

Db = Annotated[AsyncSession, Depends(get_db)]
User = Annotated[AccessTokenClaims, Depends(current_user)]


def build_router() -> APIRouter:
    router = APIRouter(prefix="/v1/profile", tags=["profile"])

    @router.get("")
    async def get_profile(user: User, db: Db) -> ProfileOut:
        return await service.read_profile(db, user.user_id)

    @router.post("/facts", status_code=status.HTTP_201_CREATED)
    async def create_fact(body: FactCreate, user: User, db: Db) -> FactOut:
        return await service.add_fact(db, user.user_id, body.fact_type, body.value)

    # Declared before /facts/{fact_id} so "bulk-status" is not read as an id.
    @router.post("/facts/bulk-status")
    async def bulk_status(body: BulkStatusUpdate, user: User, db: Db) -> dict[str, int]:
        updated = await service.set_status_bulk(db, user.user_id, body.fact_ids, body.verification_status)
        return {"updated": updated}

    @router.patch("/facts/{fact_id}")
    async def patch_fact(fact_id: uuid.UUID, body: FactUpdate, user: User, db: Db) -> FactOut:
        return await service.update_fact(db, user.user_id, fact_id, body.value, body.verification_status)

    @router.delete("/facts/{fact_id}", status_code=status.HTTP_204_NO_CONTENT)
    async def remove_fact(fact_id: uuid.UUID, user: User, db: Db) -> Response:
        await service.delete_fact(db, user.user_id, fact_id)
        return Response(status_code=status.HTTP_204_NO_CONTENT)

    return router
