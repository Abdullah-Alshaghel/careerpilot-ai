"""Job discovery routes. Another user's search or job is a plain 404."""

from __future__ import annotations

import uuid
from collections.abc import AsyncIterator
from typing import Annotated

from fastapi import APIRouter, Depends, Query, Request, Response, status
from sqlalchemy.ext.asyncio import AsyncSession

from careerpilot_common.auth import AccessTokenClaims, require_user
from job_discovery_service import service
from job_discovery_service.companies import fetch_companies
from job_discovery_service.schemas import ImportRequest, JobList, JobOut, SearchRunList, SearchRunOut
from job_discovery_service.settings import DiscoverySettings


async def get_db(request: Request) -> AsyncIterator[AsyncSession]:
    async with request.app.state.sessionmaker() as session:
        yield session


current_user = require_user(lambda request: request.app.state.jwks)

Db = Annotated[AsyncSession, Depends(get_db)]
User = Annotated[AccessTokenClaims, Depends(current_user)]


def build_router() -> APIRouter:
    router = APIRouter(tags=["discovery"])

    @router.post("/v1/searches", status_code=status.HTTP_202_ACCEPTED)
    async def start_search(request: Request, user: User, db: Db) -> SearchRunOut:
        settings: DiscoverySettings = request.app.state.settings
        companies = await fetch_companies(
            request.app.state.http, settings.company_service_url, request.headers["authorization"]
        )
        run = await service.start_search(db, settings, user.user_id, companies)
        request.app.state.wake_worker.set()
        return run

    @router.get("/v1/searches")
    async def list_searches(user: User, db: Db, limit: Annotated[int, Query(ge=1, le=50)] = 10) -> SearchRunList:
        return SearchRunList(runs=await service.list_runs(db, user.user_id, limit))

    @router.get("/v1/searches/{run_id}")
    async def get_search(run_id: uuid.UUID, user: User, db: Db) -> SearchRunOut:
        return await service.get_run(db, user.user_id, run_id)

    @router.get("/v1/jobs")
    async def list_jobs(
        user: User,
        db: Db,
        q: Annotated[str | None, Query(max_length=100)] = None,
        company_id: uuid.UUID | None = None,
        remote_only: bool = False,
        include_unlisted: bool = False,
        include_duplicates: bool = False,
        limit: Annotated[int, Query(ge=1, le=100)] = 50,
        offset: Annotated[int, Query(ge=0, le=10_000)] = 0,
    ) -> JobList:
        return await service.list_jobs(
            db,
            user.user_id,
            q=q.strip() if q else None,
            company_id=company_id,
            remote_only=remote_only,
            include_unlisted=include_unlisted,
            include_duplicates=include_duplicates,
            limit=limit,
            offset=offset,
        )

    @router.get("/v1/jobs/{job_id}")
    async def get_job(job_id: uuid.UUID, user: User, db: Db) -> JobOut:
        return await service.get_job(db, user.user_id, job_id)

    @router.post("/v1/jobs/import", status_code=status.HTTP_201_CREATED)
    async def import_job(body: ImportRequest, request: Request, response: Response, user: User, db: Db) -> JobOut:
        job, created = await service.import_url(db, request.app.state.fetcher, user.user_id, body.url)
        if not created:
            response.status_code = status.HTTP_200_OK
        return job

    return router
