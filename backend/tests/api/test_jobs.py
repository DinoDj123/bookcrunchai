"""Job status (FR-012): GET /api/v1/jobs/{id}, visible only to the owner of the book."""

import uuid
from datetime import datetime
from hashlib import sha256
from typing import Any

import pytest
from httpx import AsyncClient
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from app.core.config import Settings
from app.db.models import Book, Job, User
from app.worker.tasks import process_book
from tests.api.conftest import register

pytestmark = pytest.mark.anyio

EPUB = b"PK\x03\x04" + b"the rest of an epub file"


async def upload(client: AsyncClient) -> dict[str, Any]:
    response = await client.post(
        "/api/v1/books", files={"file": ("book.epub", EPUB, "application/epub+zip")}
    )
    assert response.status_code == 202, response.text
    body: dict[str, Any] = response.json()
    return body


async def test_owner_reads_a_queued_job(client: AsyncClient, registered: str) -> None:
    uploaded = await upload(client)

    response = await client.get(f"/api/v1/jobs/{uploaded['job']['id']}")

    assert response.status_code == 200
    assert response.json() == {
        "id": uploaded["job"]["id"],
        "book_id": uploaded["book"]["id"],
        "type": "process_book",
        "status": "queued",
        "stage": None,
        "progress": 0,
        "error_code": None,
        "error_message": None,
        "started_at": None,
        "finished_at": None,
    }


async def test_status_follows_the_worker(
    client: AsyncClient,
    registered: str,
    settings: Settings,
    session_factory: async_sessionmaker[AsyncSession],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Upload, then the worker task on the uploaded file, then the status the UI would poll."""
    monkeypatch.setenv("BOOKCRUNCH_PARSER", "fake")
    for name in ("FAKE_PARSER_ERROR", "FAKE_PARSER_DELAY", "FAKE_PARSER_FORMAT"):
        monkeypatch.delenv(name, raising=False)
    uploaded = await upload(client)
    job_id = uploaded["job"]["id"]

    worker_ctx = {
        "session_factory": session_factory,
        "storage_dir": settings.storage_dir,
        "job_try": 1,
    }
    await process_book(worker_ctx, job_id)
    job = (await client.get(f"/api/v1/jobs/{job_id}")).json()

    assert (job["status"], job["stage"], job["progress"]) == ("done", "parsing", 100)
    assert job["error_code"] is None
    # Timestamps are ISO 8601 with a UTC offset (specification 7.1).
    started = datetime.fromisoformat(job["started_at"])
    finished = datetime.fromisoformat(job["finished_at"])
    assert started.utcoffset() is not None and started <= finished


async def test_failed_job_shows_the_reason(
    client: AsyncClient,
    registered: str,
    settings: Settings,
    session_factory: async_sessionmaker[AsyncSession],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("BOOKCRUNCH_PARSER", "fake")
    monkeypatch.setenv("FAKE_PARSER_ERROR", "NO_TEXT_LAYER")
    uploaded = await upload(client)
    job_id = uploaded["job"]["id"]

    worker_ctx = {
        "session_factory": session_factory,
        "storage_dir": settings.storage_dir,
        "job_try": 1,
    }
    await process_book(worker_ctx, job_id)
    job = (await client.get(f"/api/v1/jobs/{job_id}")).json()

    assert job["status"] == "failed"
    assert job["error_code"] == "NO_TEXT_LAYER"
    assert job["error_message"].startswith("This PDF contains scanned images")


async def test_job_of_another_user_looks_like_it_does_not_exist(client: AsyncClient) -> None:
    await register(client, "first@example.com")
    uploaded = await upload(client)
    await register(client, "second@example.com")

    response = await client.get(f"/api/v1/jobs/{uploaded['job']['id']}")
    unknown = await client.get(f"/api/v1/jobs/{uuid.uuid4()}")

    assert response.status_code == unknown.status_code == 404
    assert response.json() == unknown.json()
    assert response.json()["error"]["code"] == "JOB_NOT_FOUND"


async def test_job_of_a_demo_book_is_visible_to_every_user(
    client: AsyncClient, registered: str, session: AsyncSession
) -> None:
    owner = User(id=uuid.uuid4(), email="demo-owner@example.com", password_hash="hash")
    book = Book(
        id=uuid.uuid4(),
        owner_id=owner.id,
        title="Meditations",
        file_format="epub",
        file_sha256=sha256(b"demo").hexdigest(),
        file_path="demo.epub",
        file_size_bytes=4,
        status="ready",
        is_demo=True,
    )
    job = Job(id=uuid.uuid4(), book_id=book.id, type="process_book", status="done", progress=100)
    session.add(owner)
    await session.flush()
    session.add(book)
    await session.flush()
    session.add(job)
    await session.flush()

    response = await client.get(f"/api/v1/jobs/{job.id}")

    assert response.status_code == 200
    assert response.json()["status"] == "done"


async def test_malformed_job_id_is_a_validation_error(client: AsyncClient, registered: str) -> None:
    response = await client.get("/api/v1/jobs/not-a-uuid")

    assert response.status_code == 422
    assert response.json()["error"]["code"] == "VALIDATION_ERROR"


async def test_job_status_needs_a_session(client: AsyncClient, registered: str) -> None:
    uploaded = await upload(client)
    client.cookies.clear()

    response = await client.get(f"/api/v1/jobs/{uploaded['job']['id']}")

    assert response.status_code == 401
    assert response.json()["error"]["code"] == "UNAUTHENTICATED"
