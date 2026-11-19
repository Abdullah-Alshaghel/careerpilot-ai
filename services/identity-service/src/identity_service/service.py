"""Authentication use cases. Every function runs inside one DB transaction."""

from __future__ import annotations

import logging
import uuid
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone

from sqlalchemy import select, update
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from careerpilot_common.errors import ServiceError
from identity_service.models import ClientType, User, UserSession, UserStatus
from identity_service.schemas import LoginRequest, RegisterRequest, TokenResponse, UserOut
from identity_service.security import PasswordHasher, TokenSigner, hash_refresh_token, new_refresh_token

log = logging.getLogger("identity_service.auth")

_BEARER_CHALLENGE = {"WWW-Authenticate": "Bearer"}


def _unauthorized(code: str, message: str) -> ServiceError:
    return ServiceError(401, code, message, headers=_BEARER_CHALLENGE)


INVALID_CREDENTIALS = ("invalid_credentials", "Email or password is incorrect")
INVALID_REFRESH = ("invalid_refresh_token", "Session expired. Sign in again")


@dataclass(frozen=True)
class ClientInfo:
    client_type: ClientType
    user_agent: str | None
    ip_address: str | None


@dataclass(frozen=True)
class AuthContext:
    hasher: PasswordHasher
    signer: TokenSigner
    refresh_token_ttl: timedelta
    reuse_grace: timedelta


def utcnow() -> datetime:
    return datetime.now(timezone.utc)


def _token_response(ctx: AuthContext, user: User, session: UserSession, refresh_token: str, now: datetime):
    return TokenResponse(
        access_token=ctx.signer.issue_access_token(user.id, session.id, now),
        expires_in=int(ctx.signer.access_token_ttl.total_seconds()),
        refresh_token=refresh_token,
        refresh_expires_in=max(0, int((session.expires_at - now).total_seconds())),
        user=UserOut.model_validate(user),
    )


async def _start_session(db: AsyncSession, ctx: AuthContext, user: User, client: ClientInfo, now: datetime):
    refresh_token = new_refresh_token()
    session = UserSession(
        id=uuid.uuid4(),
        user_id=user.id,
        refresh_token_hash=hash_refresh_token(refresh_token),
        client_type=client.client_type,
        user_agent=client.user_agent,
        ip_address=client.ip_address,
        created_at=now,
        last_used_at=now,
        expires_at=now + ctx.refresh_token_ttl,
    )
    db.add(session)
    await db.flush()
    return session, refresh_token


async def register(db: AsyncSession, ctx: AuthContext, body: RegisterRequest, client: ClientInfo) -> TokenResponse:
    password_hash = await ctx.hasher.hash(body.password)
    now = utcnow()
    user = User(
        id=uuid.uuid4(),
        email=body.email,
        password_hash=password_hash,
        locale=body.locale,
        status=UserStatus.ACTIVE,
        created_at=now,
        updated_at=now,
    )
    db.add(user)
    try:
        # Insert first and let the unique constraint decide: no check-then-insert race.
        await db.flush()
    except IntegrityError as exc:
        await db.rollback()
        raise ServiceError(409, "email_taken", "An account with this email already exists") from exc
    session, refresh_token = await _start_session(db, ctx, user, client, now)
    response = _token_response(ctx, user, session, refresh_token, now)
    await db.commit()
    log.info("user registered", extra={"user_id": str(user.id), "client_type": client.client_type.value})
    return response


async def login(db: AsyncSession, ctx: AuthContext, body: LoginRequest, client: ClientInfo) -> TokenResponse:
    user = await db.scalar(select(User).where(User.email == body.email))
    matches, needs_rehash = await ctx.hasher.verify(user.password_hash if user else None, body.password)
    if user is None or not matches:
        raise _unauthorized(*INVALID_CREDENTIALS)
    if user.status != UserStatus.ACTIVE:
        # Only revealed to someone who knows the password.
        raise ServiceError(403, "account_disabled", "This account is disabled")
    if needs_rehash:
        user.password_hash = await ctx.hasher.hash(body.password)
    now = utcnow()
    session, refresh_token = await _start_session(db, ctx, user, client, now)
    response = _token_response(ctx, user, session, refresh_token, now)
    await db.commit()
    log.info("user logged in", extra={"user_id": str(user.id), "client_type": client.client_type.value})
    return response


async def refresh(db: AsyncSession, ctx: AuthContext, refresh_token: str, client: ClientInfo) -> TokenResponse:
    token_hash = hash_refresh_token(refresh_token)
    if token_hash is None:
        raise _unauthorized(*INVALID_REFRESH)
    now = utcnow()

    # Row lock: two concurrent refreshes of the same token serialize here, and
    # exactly one of them rotates it.
    session = await db.scalar(select(UserSession).where(UserSession.refresh_token_hash == token_hash).with_for_update())
    if session is None:
        await _handle_unknown_refresh_token(db, ctx, token_hash, now)
        raise _unauthorized(*INVALID_REFRESH)  # pragma: no cover - the handler always raises

    if session.revoked_at is not None or session.expires_at <= now:
        raise _unauthorized(*INVALID_REFRESH)
    user = await db.get(User, session.user_id)
    if user is None or user.status != UserStatus.ACTIVE:
        session.revoked_at = now
        await db.commit()
        raise _unauthorized(*INVALID_REFRESH)

    new_token = new_refresh_token()
    session.previous_refresh_token_hash = session.refresh_token_hash
    session.refresh_token_hash = hash_refresh_token(new_token)
    session.rotated_at = now
    session.last_used_at = now
    session.ip_address = client.ip_address
    session.user_agent = client.user_agent
    response = _token_response(ctx, user, session, new_token, now)
    await db.commit()
    return response


async def _handle_unknown_refresh_token(db: AsyncSession, ctx: AuthContext, token_hash: bytes, now: datetime) -> None:
    """A token that is not current. If it is the one we just rotated away from,
    decide between a benign race and token theft."""
    session = await db.scalar(
        select(UserSession).where(UserSession.previous_refresh_token_hash == token_hash).with_for_update()
    )
    if session is None or session.revoked_at is not None:
        raise _unauthorized(*INVALID_REFRESH)
    if session.rotated_at is not None and now - session.rotated_at <= ctx.reuse_grace:
        # Another request (e.g. a second browser tab) refreshed moments ago.
        # The client should retry with the token it now holds.
        raise _unauthorized("refresh_token_superseded", "Session was refreshed by another request")
    # An old token came back after the grace window: someone else holds a copy.
    # Revoke the whole session so neither copy works any more.
    session.revoked_at = now
    await db.commit()
    log.warning("refresh token reuse detected; session revoked", extra={"user_id": str(session.user_id)})
    raise _unauthorized("refresh_token_reused", "Session ended for your security. Sign in again")


async def logout(db: AsyncSession, refresh_token: str) -> None:
    """Idempotent: unknown or already-revoked tokens are not an error."""
    token_hash = hash_refresh_token(refresh_token)
    if token_hash is None:
        return
    await db.execute(
        update(UserSession)
        .where(UserSession.refresh_token_hash == token_hash, UserSession.revoked_at.is_(None))
        .values(revoked_at=utcnow())
    )
    await db.commit()


async def active_user(db: AsyncSession, user_id: uuid.UUID, session_id: uuid.UUID) -> User:
    """User for a verified access token, provided its session is still live.

    This makes logout and reuse-revocation take effect immediately for the
    Identity Service itself; other services rely on the short token lifetime.
    """
    row = (
        await db.execute(
            select(User, UserSession)
            .join(UserSession, UserSession.user_id == User.id)
            .where(UserSession.id == session_id, User.id == user_id)
        )
    ).first()
    if row is None:
        raise _unauthorized("invalid_token", "Sign in again")
    user, session = row
    if session.revoked_at is not None or session.expires_at <= utcnow() or user.status != UserStatus.ACTIVE:
        raise _unauthorized("invalid_token", "Sign in again")
    return user
