"""Fixtures for worker tests: a queued process_book job for an uploaded book, and the ARQ context
the task receives. The fake parser is used, so no real book file is needed."""

import uuid
from hashlib import sha256
from pathlib import Path
from typing import Any

import pytest
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from app.db.models import Book, Job, User

BOOK_FILE_NAME = "book.epub"


@pytest.fixture(autouse=True)
def fake_parser(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("BOOKCRUNCH_PARSER", "fake")
    for name in ("FAKE_PARSER_ERROR", "FAKE_PARSER_DELAY", "FAKE_PARSER_FORMAT"):
        monkeypatch.delenv(name, raising=False)


@pytest.fixture
def ctx(session_factory: async_sessionmaker[AsyncSession], tmp_path: Path) -> dict[str, Any]:
    """What ARQ passes to a task: our startup values plus job_try (1 on the first attempt)."""
    return {"session_factory": session_factory, "storage_dir": str(tmp_path), "job_try": 1}


def make_job(book: Book) -> Job:
    return Job(id=uuid.uuid4(), book_id=book.id, type="process_book", status="queued")


@pytest.fixture
async def book(session: AsyncSession, tmp_path: Path) -> Book:
    """An uploaded book whose file exists in the storage directory."""
    content = b"not a real epub; the fake parser never reads it"
    (tmp_path / BOOK_FILE_NAME).write_bytes(content)
    user = User(id=uuid.uuid4(), email=f"{uuid.uuid4().hex}@example.com", password_hash="hash")
    uploaded = Book(
        id=uuid.uuid4(),
        owner_id=user.id,
        title="book",
        file_format="epub",
        file_sha256=sha256(content).hexdigest(),
        file_path=BOOK_FILE_NAME,
        file_size_bytes=len(content),
        status="uploaded",
    )
    session.add(user)
    await session.flush()
    session.add(uploaded)
    await session.flush()
    return uploaded


@pytest.fixture
async def queued_job(session: AsyncSession, book: Book) -> Job:
    job = make_job(book)
    session.add(job)
    await session.flush()
    return job
