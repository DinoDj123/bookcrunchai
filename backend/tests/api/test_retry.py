"""Retry of failed processing (FR-012): POST /api/v1/books/{id}/retry."""

import uuid
from typing import Any

import pytest
from httpx import AsyncClient, Response
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from app.core.config import Settings
from app.db.models import Book, Job
from app.worker.tasks import process_book
from tests.api.conftest import FakeQueue, register

pytestmark = pytest.mark.anyio

EPUB = b"PK\x03\x04" + b"the rest of an epub file"


async def upload(client: AsyncClient) -> dict[str, Any]:
    response = await client.post(
        "/api/v1/books", files={"file": ("book.epub", EPUB, "application/epub+zip")}
    )
    assert response.status_code in (200, 202), response.text
    body: dict[str, Any] = response.json()
    return body


async def retry(client: AsyncClient, book_id: str) -> Response:
    return await client.post(f"/api/v1/books/{book_id}/retry")


async def run_worker(
    job_id: str, settings: Settings, session_factory: async_sessionmaker[AsyncSession]
) -> None:
    ctx = {"session_factory": session_factory, "storage_dir": settings.storage_dir, "job_try": 1}
    await process_book(ctx, job_id)


@pytest.fixture
async def failed(
    client: AsyncClient,
    registered: str,
    settings: Settings,
    session_factory: async_sessionmaker[AsyncSession],
    monkeypatch: pytest.MonkeyPatch,
) -> dict[str, Any]:
    """An uploaded book whose processing failed, as the upload answered it."""
    monkeypatch.setenv("BOOKCRUNCH_PARSER", "fake")
    monkeypatch.setenv("FAKE_PARSER_ERROR", "CORRUPT_FILE")
    for name in ("FAKE_PARSER_DELAY", "FAKE_PARSER_FORMAT"):
        monkeypatch.delenv(name, raising=False)
    uploaded = await upload(client)
    await run_worker(uploaded["job"]["id"], settings, session_factory)
    return uploaded


async def test_retry_queues_the_failed_job_again(
    client: AsyncClient, failed: dict[str, Any], session: AsyncSession, queue: FakeQueue
) -> None:
    book_id, job_id = failed["book"]["id"], failed["job"]["id"]

    response = await retry(client, book_id)

    assert response.status_code == 202
    assert response.json() == {
        "book": {"id": book_id, "title": failed["book"]["title"], "status": "processing"},
        "job": {"id": job_id, "status": "queued"},
    }
    book = await session.get_one(Book, uuid.UUID(book_id), populate_existing=True)
    job = await session.get_one(Job, uuid.UUID(job_id), populate_existing=True)
    assert (book.status, book.error_code) == ("processing", None)
    assert (job.status, job.stage, job.progress) == ("queued", None, 0)
    assert (job.error_code, job.error_message, job.finished_at) == (None, None, None)
    # The same job, under the same ID: no second job row.
    assert await session.scalar(select(func.count()).select_from(Job)) == 1
    assert queue.calls[-1] == ("process_book", (job_id,), {"_job_id": job_id})
    assert len(queue.calls) == 2


async def test_retried_book_is_processed_when_the_cause_is_gone(
    client: AsyncClient,
    failed: dict[str, Any],
    settings: Settings,
    session_factory: async_sessionmaker[AsyncSession],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.delenv("FAKE_PARSER_ERROR")
    await retry(client, failed["book"]["id"])

    await run_worker(failed["job"]["id"], settings, session_factory)

    job = (await client.get(f"/api/v1/jobs/{failed['job']['id']}")).json()
    assert (job["status"], job["progress"], job["error_code"]) == ("done", 100, None)
    # The same file uploaded again now returns the processed book.
    assert (await upload(client))["book"]["status"] == "ready"


@pytest.mark.parametrize("status", ["uploaded", "processing", "ready"])
async def test_book_that_did_not_fail_cannot_be_retried(
    status: str,
    client: AsyncClient,
    registered: str,
    session: AsyncSession,
    queue: FakeQueue,
) -> None:
    uploaded = await upload(client)
    book = await session.get_one(Book, uuid.UUID(uploaded["book"]["id"]))
    book.status = status
    await session.flush()

    response = await retry(client, uploaded["book"]["id"])

    assert response.status_code == 409
    assert response.json()["error"]["code"] == "BOOK_NOT_FAILED"
    assert len(queue.calls) == 1  # only the upload


async def test_book_of_another_user_looks_like_it_does_not_exist(
    client: AsyncClient, failed: dict[str, Any], session: AsyncSession, queue: FakeQueue
) -> None:
    await register(client, "second@example.com")

    response = await retry(client, failed["book"]["id"])

    assert response.status_code == 404
    assert response.json()["error"]["code"] == "BOOK_NOT_FOUND"
    book = await session.get_one(Book, uuid.UUID(failed["book"]["id"]), populate_existing=True)
    assert book.status == "failed"
    assert len(queue.calls) == 1


async def test_unknown_book_is_not_found(client: AsyncClient, registered: str) -> None:
    response = await retry(client, str(uuid.uuid4()))

    assert response.status_code == 404
    assert response.json()["error"]["code"] == "BOOK_NOT_FOUND"


async def test_retry_needs_a_session(client: AsyncClient) -> None:
    assert (await retry(client, str(uuid.uuid4()))).status_code == 401


async def test_queue_failure_leaves_the_book_failed_and_retryable(
    client: AsyncClient, failed: dict[str, Any], session: AsyncSession, queue: FakeQueue
) -> None:
    queue.fail = True

    response = await retry(client, failed["book"]["id"])

    assert response.status_code == 503
    assert response.json()["error"]["code"] == "QUEUE_UNAVAILABLE"
    book = await session.get_one(Book, uuid.UUID(failed["book"]["id"]), populate_existing=True)
    job = await session.get_one(Job, uuid.UUID(failed["job"]["id"]), populate_existing=True)
    assert (book.status, book.error_code) == ("failed", "CORRUPT_FILE")
    assert (job.status, job.error_code) == ("failed", "CORRUPT_FILE")
    assert job.error_message and job.finished_at is not None

    queue.fail = False
    assert (await retry(client, failed["book"]["id"])).status_code == 202
