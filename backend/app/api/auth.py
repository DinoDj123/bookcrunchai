"""Registration, login and logout (FR-001, specification 7.3)."""

import uuid
from functools import lru_cache
from typing import Annotated

from fastapi import APIRouter, Response
from fastapi.concurrency import run_in_threadpool
from pydantic import BaseModel, EmailStr, Field, field_validator
from sqlalchemy import select
from sqlalchemy.exc import IntegrityError

from app.api.deps import SESSION_COOKIE, CurrentUser, SessionDep, SettingsDep
from app.core.config import Settings
from app.core.errors import AppError
from app.core.security import (
    SESSION_LIFETIME,
    create_session_token,
    hash_password,
    verify_password,
)
from app.db.models import User

router = APIRouter(prefix="/auth", tags=["auth"])

PASSWORD_MIN_LENGTH = 8
# The upper limit keeps a huge "password" from tying up the server in the hash function.
PASSWORD_MAX_LENGTH = 128


class Credentials(BaseModel):
    email: EmailStr
    # Taken exactly as typed: spaces are part of a password.
    password: Annotated[str, Field(max_length=PASSWORD_MAX_LENGTH)]

    @field_validator("email", mode="before")
    @classmethod
    def without_spaces_around(cls, email: object) -> object:
        return email.strip() if isinstance(email, str) else email

    @field_validator("email")
    @classmethod
    def lower_case(cls, email: str) -> str:
        """One account per address, however it is typed (the database checks this too)."""
        return email.lower()


class NewAccount(Credentials):
    password: Annotated[str, Field(min_length=PASSWORD_MIN_LENGTH, max_length=PASSWORD_MAX_LENGTH)]


class UserOut(BaseModel):
    id: uuid.UUID
    email: str


def start_session(response: Response, user: User, settings: Settings) -> None:
    response.set_cookie(
        SESSION_COOKIE,
        create_session_token(user.id, settings.jwt_secret),
        max_age=int(SESSION_LIFETIME.total_seconds()),
        httponly=True,  # page scripts cannot read it
        samesite="lax",
        # Browsers send a Secure cookie only over HTTPS; local development runs on plain HTTP.
        secure=not settings.is_local,
    )


@lru_cache
def _hash_for_unknown_email() -> str:
    return hash_password("no account has this password")


@router.post("/register", status_code=201)
async def register(
    body: NewAccount, response: Response, session: SessionDep, settings: SettingsDep
) -> UserOut:
    """Create an account and log the new user in."""
    # Hashing takes tens of milliseconds of CPU on purpose; in a thread it does not hold up
    # other requests.
    password_hash = await run_in_threadpool(hash_password, body.password)
    user = User(id=uuid.uuid4(), email=body.email, password_hash=password_hash)
    session.add(user)
    try:
        await session.commit()
    except IntegrityError as error:  # the unique index on users.email
        raise AppError(409, "EMAIL_TAKEN", "An account with this email already exists.") from error
    start_session(response, user, settings)
    return UserOut(id=user.id, email=user.email)


@router.post("/login")
async def login(
    body: Credentials, response: Response, session: SessionDep, settings: SettingsDep
) -> UserOut:
    user = await session.scalar(select(User).where(User.email == body.email))
    # A hash is checked even when the email is unknown, so that the response time does not
    # reveal which addresses have an account.
    password_hash = user.password_hash if user else _hash_for_unknown_email()
    password_ok = await run_in_threadpool(verify_password, body.password, password_hash)
    if user is None or not password_ok:
        raise AppError(401, "INVALID_CREDENTIALS", "Invalid email or password.")
    start_session(response, user, settings)
    return UserOut(id=user.id, email=user.email)


@router.post("/logout", status_code=204)
async def logout(_user: CurrentUser, response: Response, settings: SettingsDep) -> None:
    # Same attributes as when the cookie was set.
    response.delete_cookie(
        SESSION_COOKIE, httponly=True, samesite="lax", secure=not settings.is_local
    )


@router.get("/me")
async def me(user: CurrentUser) -> UserOut:
    return UserOut(id=user.id, email=user.email)
