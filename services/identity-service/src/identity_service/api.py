"""HTTP routes. Public paths are reached through the gateway; the JWKS
document is internal (the gateway only proxies ``/v1/*``)."""

from __future__ import annotations

import ipaddress
from collections.abc import AsyncIterator
from typing import Annotated

from fastapi import APIRouter, Depends, Request, Response, status
from sqlalchemy.ext.asyncio import AsyncSession

from careerpilot_common.auth import TokenError, bearer_token, verify_access_token
from careerpilot_common.correlation import CLIENT_TYPE_HEADER
from careerpilot_common.errors import ServiceError
from identity_service import service
from identity_service.models import ClientType, User
from identity_service.schemas import LoginRequest, RefreshRequest, RegisterRequest, TokenResponse, UserOut

_NO_STORE = {"Cache-Control": "no-store", "Pragma": "no-cache"}


async def get_db(request: Request) -> AsyncIterator[AsyncSession]:
    async with request.app.state.sessionmaker() as session:
        yield session


def get_auth(request: Request) -> service.AuthContext:
    return request.app.state.auth


def client_info(request: Request) -> service.ClientInfo:
    raw_type = request.headers.get(CLIENT_TYPE_HEADER, "")
    client_type = ClientType(raw_type) if raw_type in {c.value for c in ClientType} else ClientType.WEB
    # The gateway replaces X-Forwarded-For with the single client address it saw.
    forwarded = request.headers.get("x-forwarded-for", "").split(",")[-1].strip()
    candidate = forwarded or (request.client.host if request.client else "")
    try:
        ip = str(ipaddress.ip_address(candidate))
    except ValueError:
        ip = None
    user_agent = request.headers.get("user-agent")
    return service.ClientInfo(client_type, user_agent[:512] if user_agent else None, ip)


async def current_user(
    request: Request,
    db: Annotated[AsyncSession, Depends(get_db)],
    auth: Annotated[service.AuthContext, Depends(get_auth)],
) -> User:
    token = bearer_token(request.headers.get("authorization"))
    if token is None:
        raise ServiceError(401, "unauthorized", "Sign in required", headers={"WWW-Authenticate": "Bearer"})
    try:
        claims = verify_access_token(token, auth.signer.key_set())
    except TokenError as exc:
        raise ServiceError(401, exc.code, "Sign in again", headers={"WWW-Authenticate": "Bearer"}) from exc
    return await service.active_user(db, claims.user_id, claims.session_id)


Db = Annotated[AsyncSession, Depends(get_db)]
Auth = Annotated[service.AuthContext, Depends(get_auth)]
Client = Annotated[service.ClientInfo, Depends(client_info)]


def build_router() -> APIRouter:
    router = APIRouter()

    @router.post("/v1/auth/register", status_code=status.HTTP_201_CREATED, tags=["auth"])
    async def register(body: RegisterRequest, db: Db, auth: Auth, client: Client, response: Response) -> TokenResponse:
        response.headers.update(_NO_STORE)
        return await service.register(db, auth, body, client)

    @router.post("/v1/auth/login", tags=["auth"])
    async def login(body: LoginRequest, db: Db, auth: Auth, client: Client, response: Response) -> TokenResponse:
        response.headers.update(_NO_STORE)
        return await service.login(db, auth, body, client)

    @router.post("/v1/auth/refresh", tags=["auth"])
    async def refresh(body: RefreshRequest, db: Db, auth: Auth, client: Client, response: Response) -> TokenResponse:
        response.headers.update(_NO_STORE)
        return await service.refresh(db, auth, body.refresh_token, client)

    @router.post("/v1/auth/logout", status_code=status.HTTP_204_NO_CONTENT, tags=["auth"])
    async def logout(body: RefreshRequest, db: Db) -> Response:
        await service.logout(db, body.refresh_token)
        return Response(status_code=status.HTTP_204_NO_CONTENT)

    @router.get("/v1/users/me", tags=["users"])
    async def me(user: Annotated[User, Depends(current_user)], response: Response) -> UserOut:
        response.headers.update(_NO_STORE)
        return UserOut.model_validate(user)

    @router.get("/.well-known/jwks.json", include_in_schema=False)
    async def jwks(auth: Auth, response: Response) -> dict:
        response.headers["Cache-Control"] = "public, max-age=300"
        return auth.signer.jwks()

    return router
