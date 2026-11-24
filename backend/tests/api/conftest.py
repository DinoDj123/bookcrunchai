"""Fixtures for API tests: the real application with its database session, settings and job queue
replaced, so tests need PostgreSQL (TEST_DATABASE_URL, see tests/conftest.py) but no Redis."""

from collections.abc import AsyncIterator
from pathlib import Path
from typing import Any

import pytest
from httpx import ASGITransport, AsyncClient
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from app.api.deps import get_queue
from app.api.rate_limit import get_request_counter
from app.core.config import Settings, get_settings
from app.db.session import get_session
from app.main import app

EMAIL = "reader@example.com"
PASSWORD = "correct horse"
UPLOAD_LIMIT = 1024  # bytes; small, so the "too large" tests stay fast


class FakeQueue:
    """Stands in for the ARQ pool: records what was enqueued, or fails like Redis being down."""

    def __init__(self) -> None:
        self.fail = False
        self.calls: list[tuple[str, tuple[Any, ...], dict[str, Any]]] = []

    async def enqueue_job(self, function: str, *args: Any, **kwargs: Any) -> object:
        if self.fail:
            raise ConnectionError("redis is down")
        self.calls.append((function, args, kwargs))
        return object()


class FakeCounter:
    """Stands in for RequestCounter: counts in memory (the window never ends during a test), or
    fails like Redis being down."""

    WINDOW_LEFT = 42  # seconds, reported for every key

    def __init__(self) -> None:
        self.fail = False
        self.counts: dict[str, int] = {}

    async def hit(self, key: str, window_seconds: int) -> tuple[int, int]:
        if self.fail:
            raise ConnectionError("redis is down")
        self.counts[key] = self.counts.get(key, 0) + 1
        return self.counts[key], self.WINDOW_LEFT


@pytest.fixture
def settings(tmp_path: Path) -> Settings:
    return Settings(
        _env_file=None,
        environment="local",
        storage_dir=str(tmp_path / "storage"),
        jwt_secret="test-secret-that-is-long-enough-for-hs256",
        max_upload_bytes=UPLOAD_LIMIT,
    )


@pytest.fixture
def queue() -> FakeQueue:
    return FakeQueue()


@pytest.fixture
def counter() -> FakeCounter:
    return FakeCounter()


@pytest.fixture
async def client(
    session_factory: async_sessionmaker[AsyncSession],
    settings: Settings,
    queue: FakeQueue,
    counter: FakeCounter,
) -> AsyncIterator[AsyncClient]:
    async def session_for_request() -> AsyncIterator[AsyncSession]:
        async with session_factory() as db_session:
            yield db_session

    app.dependency_overrides[get_session] = session_for_request
    app.dependency_overrides[get_settings] = lambda: settings
    app.dependency_overrides[get_queue] = lambda: queue
    app.dependency_overrides[get_request_counter] = lambda: counter
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as http:
        yield http
    app.dependency_overrides.clear()


async def register(client: AsyncClient, email: str = EMAIL, password: str = PASSWORD) -> str:
    """Register a user, leave the client logged in as that user and return the user's ID."""
    response = await client.post(
        "/api/v1/auth/register", json={"email": email, "password": password}
    )
    assert response.status_code == 201, response.text
    user_id: str = response.json()["id"]
    return user_id


@pytest.fixture
async def registered(client: AsyncClient) -> str:
    return await register(client)
