"""Book upload (FR-002) with de-duplication per user (FR-014)."""

import uuid
from hashlib import sha256
from pathlib import Path
from typing import Any

import pytest
from httpx import AsyncClient, Response
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.api import books
from app.api.books import MULTIPART_OVERHEAD_BYTES
from app.core.config import Settings
from app.db.models import Book, Job
from tests.api.conftest import UPLOAD_LIMIT, FakeQueue, register

pytestmark = pytest.mark.anyio

BOOKS = "/api/v1/books"
EPUB = b"PK\x03\x04" + b"the rest of an epub file"
PDF = b"%PDF-1.7" + b"the rest of a pdf file"


async def upload(client: AsyncClient, name: str, content: bytes) -> Response:
    return await client.post(BOOKS, files={"file": (name, content, "application/octet-stream")})


def stored_files(settings: Settings) -> list[Path]:
    storage = Path(settings.storage_dir)
    return sorted(storage.iterdir()) if storage.exists() else []


async def count(session: AsyncSession, model: type[Any]) -> int:
    return await session.scalar(select(func.count()).select_from(model)) or 0


@pytest.mark.parametrize(
    ("name", "content", "file_format"),
    [("Pride and Prejudice.epub", EPUB, "epub"), ("The_Prince.PDF", PDF, "pdf")],
)
async def test_upload_stores_the_file_and_queues_a_job(
    name: str,
    content: bytes,
    file_format: str,
    client: AsyncClient,
    registered: str,
    session: AsyncSession,
    settings: Settings,
    queue: FakeQueue,
) -> None:
    response = await upload(client, name, content)

    assert response.status_code == 202
    body = response.json()
    book = await session.get_one(Book, uuid.UUID(body["book"]["id"]))
    job = await session.get_one(Job, uuid.UUID(body["job"]["id"]))
    assert body == {
        "book": {"id": str(book.id), "title": book.title, "status": "uploaded"},
        "job": {"id": str(job.id), "status": "queued"},
    }

    assert book.owner_id == uuid.UUID(registered)
    assert book.title == name.rsplit(".", 1)[0].replace("_", " ")
    assert book.file_format == file_format
    assert book.file_sha256 == sha256(content).hexdigest()
    assert book.file_size_bytes == len(content)
    assert book.file_path == f"{book.id}.{file_format}"
    assert book.status == "uploaded"
    assert (job.book_id, job.type, job.status) == (book.id, "process_book", "queued")

    assert [path.name for path in stored_files(settings)] == [book.file_path]
    assert stored_files(settings)[0].read_bytes() == content
    assert queue.calls == [("process_book", (str(job.id),), {"_job_id": str(job.id)})]


async def test_same_file_again_returns_the_existing_book(
    client: AsyncClient,
    registered: str,
    session: AsyncSession,
    settings: Settings,
    queue: FakeQueue,
) -> None:
    first = await upload(client, "book.epub", EPUB)

    second = await upload(client, "another name.epub", EPUB)

    assert second.status_code == 200
    assert second.json() == first.json()
    assert await count(session, Book) == 1
    assert await count(session, Job) == 1
    assert len(stored_files(settings)) == 1
    # The job is still waiting, so it is handed to the queue again; the queue ignores a job
    # it already has. This rescues a job whose first enqueue never happened.
    assert queue.calls == [queue.calls[0]] * 2


async def test_same_file_from_another_user_is_a_separate_book(
    client: AsyncClient, session: AsyncSession, settings: Settings
) -> None:
    await register(client, "first@example.com")
    first = await upload(client, "book.epub", EPUB)
    await register(client, "second@example.com")

    second = await upload(client, "book.epub", EPUB)

    assert second.status_code == 202
    assert second.json()["book"]["id"] != first.json()["book"]["id"]
    assert await count(session, Book) == 2
    assert len(stored_files(settings)) == 2


async def test_upload_that_lost_a_race_returns_the_winner(
    client: AsyncClient,
    registered: str,
    session: AsyncSession,
    settings: Settings,
    queue: FakeQueue,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Two requests with the same file at the same moment: both look, neither finds a book, both
    insert. The unique index stops the second one, which must then answer like a repeated upload."""
    first = await upload(client, "book.epub", EPUB)
    real_find = books.find_book
    lookups = 0

    async def find_nothing_at_first(*args: Any) -> Book | None:
        nonlocal lookups
        lookups += 1
        return None if lookups == 1 else await real_find(*args)

    monkeypatch.setattr(books, "find_book", find_nothing_at_first)

    second = await upload(client, "book.epub", EPUB)

    assert second.status_code == 200
    assert second.json() == first.json()
    assert await count(session, Book) == 1
    assert len(stored_files(settings)) == 1
    assert queue.calls == [queue.calls[0]] * 2


async def test_largest_allowed_file_is_accepted(client: AsyncClient, registered: str) -> None:
    content = EPUB + b"x" * (UPLOAD_LIMIT - len(EPUB))

    assert (await upload(client, "book.epub", content)).status_code == 202


async def test_file_over_the_limit_is_refused(
    client: AsyncClient,
    registered: str,
    session: AsyncSession,
    settings: Settings,
    queue: FakeQueue,
) -> None:
    content = EPUB + b"x" * (UPLOAD_LIMIT - len(EPUB) + 1)

    response = await upload(client, "book.epub", content)

    assert response.status_code == 413
    assert response.json()["error"]["code"] == "FILE_TOO_LARGE"
    assert await count(session, Book) == 0
    assert stored_files(settings) == []
    assert queue.calls == []


@pytest.mark.parametrize(
    ("name", "content"),
    [
        ("book.txt", b"plain text"),
        ("book.mobi", EPUB),
        ("book", EPUB),  # no extension
        ("book.epub", b"not a zip archive"),
        ("book.pdf", b"not a pdf"),
        ("book.pdf", EPUB),  # an EPUB named .pdf
        ("book.epub", PDF),  # a PDF named .epub
        ("book.epub", b""),
        ("book.epub", b"PK"),  # shorter than a signature
        ("../../etc/passwd", EPUB),
    ],
)
async def test_file_that_is_not_an_epub_or_pdf_is_refused(
    name: str,
    content: bytes,
    client: AsyncClient,
    registered: str,
    session: AsyncSession,
    settings: Settings,
    queue: FakeQueue,
) -> None:
    response = await upload(client, name, content)

    assert response.status_code == 415
    assert response.json()["error"]["code"] == "UNSUPPORTED_FILE_TYPE"
    assert await count(session, Book) == 0
    assert stored_files(settings) == []
    assert queue.calls == []


async def test_file_name_never_decides_where_the_file_is_stored(
    client: AsyncClient, registered: str, session: AsyncSession, settings: Settings
) -> None:
    response = await upload(client, "../../outside.epub", EPUB)

    assert response.status_code == 202
    book = await session.get_one(Book, uuid.UUID(response.json()["book"]["id"]))
    assert [path.name for path in stored_files(settings)] == [f"{book.id}.epub"]
    assert book.title == "outside"


async def test_very_long_file_name_is_cut_to_the_title_length(
    client: AsyncClient, registered: str
) -> None:
    response = await upload(client, "a" * 600 + ".epub", EPUB)

    assert response.status_code == 202
    assert len(response.json()["book"]["title"]) == 500


async def test_upload_needs_a_session(client: AsyncClient, settings: Settings) -> None:
    response = await upload(client, "book.epub", EPUB)

    assert response.status_code == 401
    assert stored_files(settings) == []


async def test_request_without_a_file_is_invalid(client: AsyncClient, registered: str) -> None:
    response = await client.post(BOOKS, data={"title": "no file here"})

    assert response.status_code == 422
    assert response.json()["error"]["code"] == "VALIDATION_ERROR"


async def test_queue_failure_leaves_nothing_behind(
    client: AsyncClient,
    registered: str,
    session: AsyncSession,
    settings: Settings,
    queue: FakeQueue,
) -> None:
    queue.fail = True

    response = await upload(client, "book.epub", EPUB)

    assert response.status_code == 503
    assert response.json()["error"]["code"] == "QUEUE_UNAVAILABLE"
    assert await count(session, Book) == 0
    assert await count(session, Job) == 0
    assert stored_files(settings) == []

    # The user can simply try again once the queue is back.
    queue.fail = False
    assert (await upload(client, "book.epub", EPUB)).status_code == 202


async def test_repeated_upload_does_not_queue_a_finished_job_again(
    client: AsyncClient, registered: str, session: AsyncSession, queue: FakeQueue
) -> None:
    first = await upload(client, "book.epub", EPUB)
    job = await session.get_one(Job, uuid.UUID(first.json()["job"]["id"]))
    job.status = "done"
    await session.flush()

    second = await upload(client, "book.epub", EPUB)

    assert second.status_code == 200
    assert second.json()["job"] == {"id": str(job.id), "status": "done"}
    assert len(queue.calls) == 1


async def test_repeated_upload_while_the_queue_is_down_keeps_the_book(
    client: AsyncClient, registered: str, session: AsyncSession, queue: FakeQueue
) -> None:
    await upload(client, "book.epub", EPUB)
    queue.fail = True

    response = await upload(client, "book.epub", EPUB)

    assert response.status_code == 503
    assert response.json()["error"]["code"] == "QUEUE_UNAVAILABLE"
    assert await count(session, Book) == 1
    assert await count(session, Job) == 1


async def test_request_declared_far_over_the_limit_is_refused(
    client: AsyncClient, registered: str, settings: Settings
) -> None:
    content = EPUB + b"x" * (UPLOAD_LIMIT + MULTIPART_OVERHEAD_BYTES)

    response = await upload(client, "book.epub", content)

    assert response.status_code == 413
    assert response.json()["error"]["code"] == "FILE_TOO_LARGE"
    assert stored_files(settings) == []


async def test_file_name_with_spaces_around_it_is_accepted(
    client: AsyncClient, registered: str
) -> None:
    response = await upload(client, " book.epub ", EPUB)

    assert response.status_code == 202
    assert response.json()["book"]["title"] == "book"
