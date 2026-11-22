"""Password hashing and session tokens (specification 4.2 and 7.3).

Passwords are hashed with Argon2id. A session is a JWT signed with HS256 that carries the user ID
and an expiry time; there is no refresh token in the MVP, so after 24 hours the user logs in again.
"""

import uuid
from datetime import UTC, datetime, timedelta

import jwt
from argon2 import PasswordHasher
from argon2.exceptions import Argon2Error, InvalidHashError

SESSION_LIFETIME = timedelta(hours=24)
JWT_ALGORITHM = "HS256"

# The defaults of the library are Argon2id with the parameters it recommends.
_hasher = PasswordHasher()


def hash_password(password: str) -> str:
    return _hasher.hash(password)


def verify_password(password: str, password_hash: str) -> bool:
    try:
        return _hasher.verify(password_hash, password)
    except (Argon2Error, InvalidHashError):
        return False


def create_session_token(user_id: uuid.UUID, secret: str) -> str:
    now = datetime.now(UTC)
    payload = {"sub": str(user_id), "iat": now, "exp": now + SESSION_LIFETIME}
    return jwt.encode(payload, secret, algorithm=JWT_ALGORITHM)


def read_session_token(token: str, secret: str) -> uuid.UUID | None:
    """The user ID of a valid token; None if the token is forged, expired or malformed."""
    try:
        payload = jwt.decode(
            token,
            secret,
            # Only our algorithm, so a token that names another one (or "none") is refused.
            algorithms=[JWT_ALGORITHM],
            options={"require": ["exp", "sub"]},
        )
        return uuid.UUID(payload["sub"])
    except (jwt.InvalidTokenError, ValueError):
        return None
