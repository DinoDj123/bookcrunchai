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
from tests.api.conftest import FakeCounter, FakeQueue, register

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


@pytest.mark.parametrize(
    ("book_status", "job_status"), [("processing", "running"), ("ready", "done")]
)
async def test_book_that_did_not_fail_cannot_be_retried(
    book_status: str,
    job_status: str,
    client: AsyncClient,
    registered: str,
    session: AsyncSession,
    queue: FakeQueue,
) -> None:
    uploaded = await upload(client)
    book = await session.get_one(Book, uuid.UUID(uploaded["book"]["id"]))
    job = await session.get_one(Job, uuid.UUID(uploaded["job"]["id"]))
    book.status, job.status = book_status, job_status
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


async def test_queue_failure_restores_the_stage_of_the_failure(
    client: AsyncClient, failed: dict[str, Any], session: AsyncSession, queue: FakeQueue
) -> None:
    queue.fail = True

    await retry(client, failed["book"]["id"])

    job = await session.get_one(Job, uuid.UUID(failed["job"]["id"]), populate_existing=True)
    assert (job.stage, job.progress) == ("parsing", 0)


async def test_job_still_held_by_the_queue_is_not_a_retry(
    client: AsyncClient, failed: dict[str, Any], session: AsyncSession, queue: FakeQueue
) -> None:
    """ARQ still has an earlier delivery of the job (enqueue answers None). That delivery may
    have no tries left, so the book must not be left "processing" on a promise."""
    queue.held = True

    response = await retry(client, failed["book"]["id"])

    assert response.status_code == 503
    assert response.json()["error"]["code"] == "QUEUE_UNAVAILABLE"
    book = await session.get_one(Book, uuid.UUID(failed["book"]["id"]), populate_existing=True)
    job = await session.get_one(Job, uuid.UUID(failed["job"]["id"]), populate_existing=True)
    assert (book.status, book.error_code) == ("failed", "CORRUPT_FILE")
    assert (job.status, job.error_code) == ("failed", "CORRUPT_FILE")

    queue.held = False
    assert (await retry(client, failed["book"]["id"])).status_code == 202


async def test_queue_error_does_not_overwrite_a_job_the_worker_started(
    client: AsyncClient,
    failed: dict[str, Any],
    settings: Settings,
    session: AsyncSession,
    session_factory: async_sessionmaker[AsyncSession],
    queue: FakeQueue,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Redis accepted the job but its answer got lost: the API sees an error while the worker
    already processes the book."""
    monkeypatch.delenv("FAKE_PARSER_ERROR")

    async def accepted_but_no_answer(function: str, job_id: str, **_: Any) -> object:
        await run_worker(job_id, settings, session_factory)
        raise TimeoutError("no answer from redis")

    monkeypatch.setattr(queue, "enqueue_job", accepted_but_no_answer)

    response = await retry(client, failed["book"]["id"])

    assert response.status_code == 202
    assert response.json()["book"]["status"] == "ready"
    assert response.json()["job"]["status"] == "done"
    book = await session.get_one(Book, uuid.UUID(failed["book"]["id"]), populate_existing=True)
    job = await session.get_one(Job, uuid.UUID(failed["job"]["id"]), populate_existing=True)
    assert (book.status, book.error_code) == ("ready", None)
    assert (job.status, job.error_code, job.progress) == ("done", None, 100)


async def test_retry_hands_a_waiting_job_to_the_queue_again(
    client: AsyncClient,
    failed: dict[str, Any],
    session: AsyncSession,
    queue: FakeQueue,
    counter: FakeCounter,
) -> None:
    """The API stopped after saving "processing"/"queued" and before enqueuing: without this the
    book could never be retried (409) and would stay "processing" for ever."""
    book_id, job_id = failed["book"]["id"], failed["job"]["id"]
    book = await session.get_one(Book, uuid.UUID(book_id), populate_existing=True)
    job = await session.get_one(Job, uuid.UUID(job_id), populate_existing=True)
    book.status, job.status = "processing", "queued"
    await session.flush()
    counted = dict(counter.counts)

    response = await retry(client, book_id)

    assert response.status_code == 202
    assert response.json() == {
        "book": {"id": book_id, "title": failed["book"]["title"], "status": "processing"},
        "job": {"id": job_id, "status": "queued"},
    }
    assert queue.calls[-1] == ("process_book", (job_id,), {"_job_id": job_id})
    assert counter.counts == counted  # no new work, so nothing counted

    queue.fail = True
    assert (await retry(client, book_id)).status_code == 503
    await session.refresh(book)
    assert book.status == "processing"
