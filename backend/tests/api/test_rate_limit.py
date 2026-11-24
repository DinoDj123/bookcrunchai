"""Rate limits on login and upload (specification 4.2 and 7.2)."""

from typing import Any

import pytest
from httpx import AsyncClient, Response
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.rate_limit import LOGIN_WINDOW_SECONDS, RequestCounter
from app.core.config import Settings
from app.db.models import Book
from tests.api.conftest import EMAIL, PASSWORD, FakeCounter, register

pytestmark = pytest.mark.anyio

LOGIN = "/api/v1/auth/login"
BOOKS = "/api/v1/books"
EPUB = b"PK\x03\x04" + b"the rest of an epub file"


async def login(client: AsyncClient, password: str = PASSWORD) -> Response:
    return await client.post(LOGIN, json={"email": EMAIL, "password": password})


async def upload(client: AsyncClient, content: bytes = EPUB) -> Response:
    return await client.post(BOOKS, files={"file": ("book.epub", content, "application/epub+zip")})


def assert_rate_limited(response: Response) -> None:
    assert response.status_code == 429
    assert response.json()["error"] == {
        "code": "RATE_LIMITED",
        "message": "Too many requests. Try again later.",
        "details": {"retry_after_seconds": FakeCounter.WINDOW_LEFT},
    }


async def test_login_attempts_over_the_limit_are_refused(
    client: AsyncClient, registered: str, settings: Settings
) -> None:
    settings.login_rate_limit = 3
    for _ in range(3):
        assert (await login(client, "a wrong password")).status_code == 401

    # Even the right password: otherwise the limit would not slow guessing down at all.
    assert_rate_limited(await login(client))


async def test_logins_within_the_limit_pass(
    client: AsyncClient, registered: str, settings: Settings
) -> None:
    settings.login_rate_limit = 3

    for _ in range(3):
        assert (await login(client)).status_code == 200


async def test_uploads_over_the_limit_are_refused_before_anything_is_stored(
    client: AsyncClient, registered: str, settings: Settings, session: AsyncSession
) -> None:
    settings.upload_rate_limit = 2
    for number in range(2):
        assert (await upload(client, EPUB + bytes([number]))).status_code == 202

    assert_rate_limited(await upload(client, EPUB + b"a third book"))
    assert await session.scalar(select(func.count()).select_from(Book)) == 2


async def test_upload_limit_is_counted_per_user(client: AsyncClient, settings: Settings) -> None:
    settings.upload_rate_limit = 1
    await register(client, "first@example.com")
    assert (await upload(client)).status_code == 202
    assert (await upload(client)).status_code == 429
    await register(client, "second@example.com")

    assert (await upload(client)).status_code == 202


async def test_anonymous_upload_is_unauthenticated_not_rate_limited(
    client: AsyncClient, counter: FakeCounter
) -> None:
    assert (await upload(client)).status_code == 401
    assert counter.counts == {}


async def test_requests_pass_while_the_counter_is_unavailable(
    client: AsyncClient, registered: str, settings: Settings, counter: FakeCounter
) -> None:
    settings.login_rate_limit = 0
    counter.fail = True

    assert (await login(client)).status_code == 200


class FakePipeline:
    """Records the commands RequestCounter sends in one transaction."""

    def __init__(self, replies: list[Any]) -> None:
        self.replies = replies
        self.commands: list[tuple[Any, ...]] = []

    async def __aenter__(self) -> "FakePipeline":
        return self

    async def __aexit__(self, *_: object) -> None:
        return None

    def incr(self, key: str) -> None:
        self.commands.append(("incr", key))

    def expire(self, key: str, seconds: int, nx: bool = False) -> None:
        self.commands.append(("expire", key, seconds, nx))

    def ttl(self, key: str) -> None:
        self.commands.append(("ttl", key))

    async def execute(self) -> list[Any]:
        return self.replies


class FakeRedis:
    def __init__(self, pipeline: FakePipeline) -> None:
        self._pipeline = pipeline
        self.transaction: bool | None = None

    def pipeline(self, transaction: bool) -> FakePipeline:
        self.transaction = transaction
        return self._pipeline


async def test_counter_sets_the_expiry_only_once_and_in_one_transaction() -> None:
    pipeline = FakePipeline(replies=[4, False, 17])
    redis = FakeRedis(pipeline)

    result = await RequestCounter(redis).hit("rate:login:1.2.3.4", LOGIN_WINDOW_SECONDS)  # type: ignore[arg-type]

    assert result == (4, 17)
    assert redis.transaction is True
    assert pipeline.commands == [
        ("incr", "rate:login:1.2.3.4"),
        ("expire", "rate:login:1.2.3.4", LOGIN_WINDOW_SECONDS, True),
        ("ttl", "rate:login:1.2.3.4"),
    ]
