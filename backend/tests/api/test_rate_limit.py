"""Rate limits on login and upload (specification 4.2 and 7.2)."""

from typing import Any

import pytest
from httpx import AsyncClient, Response
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from app.api import rate_limit
from app.api.rate_limit import LOGIN_WINDOW_SECONDS, RequestCounter
from app.core.config import Settings
from app.db.models import Book, User
from app.worker.tasks import process_book
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
    assert response.headers["retry-after"] == str(FakeCounter.WINDOW_LEFT)
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
    assert (await upload(client, EPUB + b"another book")).status_code == 429
    await register(client, "second@example.com")

    assert (await upload(client)).status_code == 202


async def test_uploads_that_store_nothing_are_not_counted(
    client: AsyncClient, registered: str, settings: Settings
) -> None:
    settings.upload_rate_limit = 2
    assert (await upload(client)).status_code == 202
    for _ in range(3):
        assert (await upload(client)).status_code == 200  # the same file again
        assert (await upload(client, b"not a book")).status_code == 415

    assert (await upload(client, EPUB + b"a second book")).status_code == 202
    assert_rate_limited(await upload(client, EPUB + b"a third book"))


async def test_retries_count_as_uploads(
    client: AsyncClient,
    registered: str,
    settings: Settings,
    session_factory: async_sessionmaker[AsyncSession],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Otherwise one file that always fails could be processed without limit."""
    settings.upload_rate_limit = 3
    monkeypatch.setenv("BOOKCRUNCH_PARSER", "fake")
    monkeypatch.setenv("FAKE_PARSER_ERROR", "CORRUPT_FILE")
    for name in ("FAKE_PARSER_DELAY", "FAKE_PARSER_FORMAT"):
        monkeypatch.delenv(name, raising=False)
    uploaded = (await upload(client)).json()
    retry_url = f"{BOOKS}/{uploaded['book']['id']}/retry"
    ctx = {"session_factory": session_factory, "storage_dir": settings.storage_dir, "job_try": 1}

    for _ in range(2):
        await process_book(ctx, uploaded["job"]["id"])  # fails
        assert (await client.post(retry_url)).status_code == 202
    await process_book(ctx, uploaded["job"]["id"])

    assert_rate_limited(await client.post(retry_url))


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

    def set(self, key: str, value: int, ex: int, nx: bool) -> None:
        self.commands.append(("set", key, value, ex, nx))

    def incr(self, key: str) -> None:
        self.commands.append(("incr", key))

    def get(self, key: str) -> None:
        self.commands.append(("get", key))

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
    pipeline = FakePipeline(replies=[None, 4, 17])
    redis = FakeRedis(pipeline)

    result = await RequestCounter(redis).hit("rate:login:1.2.3.4", LOGIN_WINDOW_SECONDS)  # type: ignore[arg-type]

    assert result == (4, 17)
    assert redis.transaction is True
    assert pipeline.commands == [
        # SET ... NX, not EXPIRE ... NX: works on Redis older than 7 too.
        ("set", "rate:login:1.2.3.4", 0, LOGIN_WINDOW_SECONDS, True),
        ("incr", "rate:login:1.2.3.4"),
        ("ttl", "rate:login:1.2.3.4"),
    ]


@pytest.mark.parametrize(
    ("replies", "expected"),
    [([b"4", 17], (4, 17)), ([None, -2], (0, 1))],  # a running window; no counter yet
)
async def test_counter_can_be_read_without_counting(
    replies: list[Any], expected: tuple[int, int]
) -> None:
    pipeline = FakePipeline(replies=replies)

    result = await RequestCounter(FakeRedis(pipeline)).peek("rate:upload:1")  # type: ignore[arg-type]

    assert result == expected
    assert pipeline.commands == [("get", "rate:upload:1"), ("ttl", "rate:upload:1")]


async def test_unavailable_counter_is_logged_without_the_client_address(
    client: AsyncClient, registered: str, counter: FakeCounter, monkeypatch: pytest.MonkeyPatch
) -> None:
    logged: list[tuple[tuple[Any, ...], dict[str, Any]]] = []
    monkeypatch.setattr(
        rate_limit.logger, "warning", lambda *args, **kwargs: logged.append((args, kwargs))
    )
    counter.fail = True

    await login(client)

    ((args, kwargs),) = logged
    assert "login" in args
    assert "127.0.0.1" not in str(args)  # the address httpx's test transport reports
    assert kwargs == {}  # no traceback: this is written on every request while Redis is away


REGISTER = "/api/v1/auth/register"


async def test_registrations_over_the_limit_are_refused(
    client: AsyncClient, settings: Settings, session: AsyncSession
) -> None:
    settings.register_rate_limit = 2
    for number in range(2):
        response = await client.post(
            REGISTER, json={"email": f"user{number}@example.com", "password": PASSWORD}
        )
        assert response.status_code == 201

    assert_rate_limited(
        await client.post(REGISTER, json={"email": "third@example.com", "password": PASSWORD})
    )
    assert await session.scalar(select(func.count()).select_from(User)) == 2


async def test_failed_registrations_are_counted_too(
    client: AsyncClient, registered: str, settings: Settings
) -> None:
    settings.register_rate_limit = 2  # one is used by the `registered` fixture
    taken = await client.post(REGISTER, json={"email": EMAIL, "password": PASSWORD})
    assert taken.status_code == 409

    assert_rate_limited(
        await client.post(REGISTER, json={"email": "new@example.com", "password": PASSWORD})
    )


async def test_registration_and_login_have_separate_counters(
    client: AsyncClient, registered: str, settings: Settings
) -> None:
    settings.register_rate_limit = 1

    assert (await login(client)).status_code == 200
