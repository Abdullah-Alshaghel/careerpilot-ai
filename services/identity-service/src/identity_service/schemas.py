"""Public request/response shapes. Mirrored in shared/api-contracts and
@careerpilot/contracts; contract tests keep them in sync."""

from __future__ import annotations

import uuid
from datetime import datetime
from typing import Annotated, Literal

from email_validator import EmailNotValidError, validate_email
from pydantic import AfterValidator, BaseModel, ConfigDict, Field

from identity_service.models import UserStatus

PASSWORD_MIN_LENGTH = 12
# Argon2 accepts any length; the cap stops multi-megabyte "passwords" being hashed.
PASSWORD_MAX_LENGTH = 128
Locale = Literal["en", "ar"]


def _normalize_new_email(value: str) -> str:
    try:
        result = validate_email(value.strip(), check_deliverability=False)
    except EmailNotValidError as exc:
        raise ValueError("Enter a valid email address") from exc
    return result.normalized.lower()


def _normalize_login_email(value: str) -> str:
    # No format validation at login: an invalid address simply won't match.
    return value.strip().lower()


def _password_not_blank(value: str) -> str:
    if not value.strip():
        raise ValueError("Password must not be blank")
    return value


NewEmail = Annotated[str, Field(min_length=3, max_length=254), AfterValidator(_normalize_new_email)]
LoginEmail = Annotated[str, Field(min_length=1, max_length=320), AfterValidator(_normalize_login_email)]


class _Request(BaseModel):
    model_config = ConfigDict(extra="forbid", str_strip_whitespace=False)


class RegisterRequest(_Request):
    email: NewEmail
    password: Annotated[
        str,
        Field(min_length=PASSWORD_MIN_LENGTH, max_length=PASSWORD_MAX_LENGTH),
        AfterValidator(_password_not_blank),
    ]
    locale: Locale = "en"


class LoginRequest(_Request):
    email: LoginEmail
    password: str = Field(min_length=1, max_length=PASSWORD_MAX_LENGTH)


class RefreshRequest(_Request):
    refresh_token: str = Field(min_length=1, max_length=256)


class UserOut(BaseModel):
    model_config = ConfigDict(extra="forbid", from_attributes=True)

    id: uuid.UUID
    email: str
    locale: Locale
    status: UserStatus
    created_at: datetime


class TokenResponse(BaseModel):
    model_config = ConfigDict(extra="forbid")

    access_token: str
    token_type: Literal["Bearer"] = "Bearer"  # noqa: S105 - OAuth token type, not a secret
    expires_in: int = Field(description="Access-token lifetime in seconds")
    refresh_token: str
    refresh_expires_in: int = Field(description="Seconds until the session ends")
    user: UserOut
