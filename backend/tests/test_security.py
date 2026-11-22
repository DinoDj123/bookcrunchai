"""Password hashing and session tokens (specification 4.2 and 7.3). No database needed."""

import uuid
from datetime import UTC, datetime, timedelta

import jwt

from app.core.security import (
    SESSION_LIFETIME,
    create_session_token,
    hash_password,
    read_session_token,
    verify_password,
)

SECRET = "test-secret-that-is-long-enough-for-hs256"


def test_password_hash_is_argon2id_and_verifies() -> None:
    password_hash = hash_password("correct horse")

    assert password_hash.startswith("$argon2id$")
    assert verify_password("correct horse", password_hash) is True
    assert verify_password("wrong horse", password_hash) is False


def test_same_password_gives_different_hashes() -> None:
    assert hash_password("correct horse") != hash_password("correct horse")


def test_verify_password_survives_a_broken_hash() -> None:
    assert verify_password("correct horse", "not-a-hash") is False


def test_token_carries_the_user_id_for_24_hours() -> None:
    user_id = uuid.uuid4()

    token = create_session_token(user_id, SECRET)
    payload = jwt.decode(token, SECRET, algorithms=["HS256"])

    assert read_session_token(token, SECRET) == user_id
    assert SESSION_LIFETIME == timedelta(hours=24)
    expires = datetime.fromtimestamp(payload["exp"], UTC)
    assert abs(expires - (datetime.now(UTC) + SESSION_LIFETIME)) < timedelta(minutes=1)


def test_token_with_another_algorithm_is_refused() -> None:
    unsigned = jwt.encode(
        {"sub": str(uuid.uuid4()), "exp": datetime.now(UTC) + timedelta(hours=1)},
        None,
        algorithm="none",
    )

    assert read_session_token(unsigned, SECRET) is None
