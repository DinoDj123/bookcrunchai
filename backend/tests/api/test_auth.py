"""Registration, login and the session cookie (FR-001, specification 7.3)."""

import uuid
from datetime import UTC, datetime, timedelta

import jwt
import pytest
from httpx import AsyncClient
from sqlalchemy import delete, func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.deps import SESSION_COOKIE
from app.core.config import Settings
from app.core.security import create_session_token
from app.db.models import User
from tests.api.conftest import EMAIL, PASSWORD, register

pytestmark = pytest.mark.anyio

REGISTER = "/api/v1/auth/register"
LOGIN = "/api/v1/auth/login"
LOGOUT = "/api/v1/auth/logout"
ME = "/api/v1/auth/me"


def error_code(response: object) -> str:
    code: str = response.json()["error"]["code"]  # type: ignore[attr-defined]
    return code


async def test_register_creates_the_user_and_starts_a_session(
    client: AsyncClient, session: AsyncSession
) -> None:
    response = await client.post(REGISTER, json={"email": EMAIL, "password": PASSWORD})

    assert response.status_code == 201
    user = await session.scalar(select(User).where(User.email == EMAIL))
    assert user is not None
    assert response.json() == {"id": str(user.id), "email": EMAIL}
    assert user.password_hash.startswith("$argon2id$")
    assert PASSWORD not in user.password_hash

    cookie = response.headers["set-cookie"]
    assert cookie.startswith(f"{SESSION_COOKIE}=")
    assert "HttpOnly" in cookie
    assert "SameSite=lax" in cookie
    assert "Max-Age=86400" in cookie
    assert "Secure" not in cookie  # local development runs on plain HTTP
    assert (await client.get(ME)).json() == {"id": str(user.id), "email": EMAIL}


async def test_cookie_is_secure_outside_local_development(
    client: AsyncClient, settings: Settings
) -> None:
    settings.environment = "production"

    response = await client.post(REGISTER, json={"email": EMAIL, "password": PASSWORD})

    assert "Secure" in response.headers["set-cookie"]


async def test_email_is_stored_in_lower_case_without_spaces(
    client: AsyncClient, session: AsyncSession
) -> None:
    response = await client.post(
        REGISTER, json={"email": "  Reader@Example.COM ", "password": PASSWORD}
    )

    assert response.status_code == 201
    assert response.json()["email"] == EMAIL
    assert await session.scalar(select(func.count()).select_from(User)) == 1


async def test_same_email_in_another_case_is_taken(client: AsyncClient, registered: str) -> None:
    response = await client.post(REGISTER, json={"email": EMAIL.upper(), "password": PASSWORD})

    assert response.status_code == 409
    assert error_code(response) == "EMAIL_TAKEN"


@pytest.mark.parametrize(
    "body",
    [
        {"email": EMAIL, "password": "1234567"},  # 7 characters
        {"email": EMAIL, "password": "x" * 129},
        {"email": "not-an-email", "password": PASSWORD},
        {"email": EMAIL},
    ],
)
async def test_invalid_registration_is_rejected(
    body: dict[str, str], client: AsyncClient, session: AsyncSession
) -> None:
    response = await client.post(REGISTER, json=body)

    assert response.status_code == 422
    assert error_code(response) == "VALIDATION_ERROR"
    assert await session.scalar(select(func.count()).select_from(User)) == 0


async def test_validation_error_does_not_echo_the_password(client: AsyncClient) -> None:
    response = await client.post(REGISTER, json={"email": EMAIL, "password": "secret7"})

    assert "secret7" not in response.text


async def test_login_starts_a_session(client: AsyncClient, registered: str) -> None:
    client.cookies.clear()

    response = await client.post(LOGIN, json={"email": EMAIL.upper(), "password": PASSWORD})

    assert response.status_code == 200
    assert response.json() == {"id": registered, "email": EMAIL}
    assert (await client.get(ME)).status_code == 200


async def test_wrong_password_and_unknown_email_get_the_same_answer(
    client: AsyncClient, registered: str
) -> None:
    client.cookies.clear()

    wrong_password = await client.post(LOGIN, json={"email": EMAIL, "password": "wrong horse"})
    unknown_email = await client.post(
        LOGIN, json={"email": "nobody@example.com", "password": PASSWORD}
    )

    assert wrong_password.status_code == unknown_email.status_code == 401
    assert wrong_password.json() == unknown_email.json()
    assert error_code(wrong_password) == "INVALID_CREDENTIALS"
    assert wrong_password.json()["error"]["message"] == "Invalid email or password."
    assert "set-cookie" not in wrong_password.headers


async def test_me_without_a_session_is_unauthenticated(client: AsyncClient) -> None:
    response = await client.get(ME)

    assert response.status_code == 401
    assert error_code(response) == "UNAUTHENTICATED"


async def test_tokens_that_must_not_be_accepted(
    client: AsyncClient, registered: str, settings: Settings
) -> None:
    user_id = uuid.UUID(registered)
    expired = jwt.encode(
        {"sub": registered, "exp": datetime.now(UTC) - timedelta(minutes=1)},
        settings.jwt_secret,
        algorithm="HS256",
    )
    without_expiry = jwt.encode({"sub": registered}, settings.jwt_secret, algorithm="HS256")
    bad_tokens = {
        "signed with another secret": create_session_token(user_id, "another-secret-" * 3),
        "expired": expired,
        "without an expiry time": without_expiry,
        "for a user that does not exist": create_session_token(uuid.uuid4(), settings.jwt_secret),
        "not a user ID": jwt.encode(
            {"sub": "42", "exp": datetime.now(UTC) + timedelta(hours=1)},
            settings.jwt_secret,
            algorithm="HS256",
        ),
        "not a token": "garbage",
    }

    for reason, token in bad_tokens.items():
        client.cookies.clear()
        client.cookies.set(SESSION_COOKIE, token)
        response = await client.get(ME)
        assert response.status_code == 401, reason
        assert error_code(response) == "UNAUTHENTICATED", reason


async def test_session_of_a_deleted_user_stops_working(
    client: AsyncClient, registered: str, session: AsyncSession
) -> None:
    await session.execute(delete(User).where(User.id == uuid.UUID(registered)))
    await session.flush()

    assert (await client.get(ME)).status_code == 401


async def test_logout_ends_the_session(client: AsyncClient, registered: str) -> None:
    response = await client.post(LOGOUT)

    assert response.status_code == 204
    assert (await client.get(ME)).status_code == 401


async def test_logout_needs_a_session(client: AsyncClient) -> None:
    assert (await client.post(LOGOUT)).status_code == 401


async def test_two_users_have_separate_sessions(client: AsyncClient) -> None:
    first = await register(client, "first@example.com")
    second = await register(client, "second@example.com")

    assert first != second
    assert (await client.get(ME)).json()["email"] == "second@example.com"


async def test_cookie_is_secure_when_the_site_has_a_domain(
    client: AsyncClient, settings: Settings
) -> None:
    settings.site_address = "bookcrunch.example.com"

    response = await client.post(REGISTER, json={"email": EMAIL, "password": PASSWORD})

    assert "Secure" in response.headers["set-cookie"]


async def test_spaces_are_part_of_a_password(client: AsyncClient) -> None:
    await client.post(REGISTER, json={"email": EMAIL, "password": "  spaced out  "})
    client.cookies.clear()

    trimmed = await client.post(LOGIN, json={"email": EMAIL, "password": "spaced out"})
    exact = await client.post(LOGIN, json={"email": EMAIL, "password": "  spaced out  "})

    assert trimmed.status_code == 401
    assert exact.status_code == 200


async def test_password_of_only_spaces_is_long_enough_if_it_has_8(client: AsyncClient) -> None:
    response = await client.post(REGISTER, json={"email": EMAIL, "password": " " * 8})

    assert response.status_code == 201


async def test_login_refuses_an_oversized_password(client: AsyncClient, registered: str) -> None:
    response = await client.post(LOGIN, json={"email": EMAIL, "password": "x" * 129})

    assert response.status_code == 422
