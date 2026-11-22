"""process_book, success path: the parsed book is stored as the parse_book contract (section 5)
prescribes. Needs TEST_DATABASE_URL (see tests/conftest.py)."""

import uuid
from typing import Any

import pytest
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.db.models import Book, Chapter, Job
from app.worker.tasks import process_book
from tests.worker.conftest import make_job

pytestmark = pytest.mark.anyio


async def chapters_of(session: AsyncSession, book: Book) -> list[Chapter]:
    result = await session.scalars(
        select(Chapter).where(Chapter.book_id == book.id).order_by(Chapter.position)
    )
    return list(result)


async def test_success_marks_job_done_and_book_ready(
    ctx: dict[str, Any], session: AsyncSession, book: Book, queued_job: Job
) -> None:
    await process_book(ctx, str(queued_job.id))
    await session.refresh(queued_job)
    await session.refresh(book)

    assert queued_job.status == "done"
    assert queued_job.stage == "parsing"
    assert queued_job.progress == 100
    assert queued_job.attempts == 1
    assert queued_job.started_at is not None
    assert queued_job.finished_at is not None
    assert queued_job.error_code is None
    assert queued_job.error_message is None

    assert book.status == "ready"
    assert book.error_code is None
    assert book.title == "Fake Book"
    assert book.author == "Fake Author"
    assert book.source_language == "en"
    assert book.chapters_detected is True
    chapters = await chapters_of(session, book)
    assert book.word_count == sum(chapter.word_count for chapter in chapters)


async def test_success_stores_chapters_in_order(
    ctx: dict[str, Any], session: AsyncSession, book: Book, queued_job: Job
) -> None:
    await process_book(ctx, str(queued_job.id))

    chapters = await chapters_of(session, book)
    assert [(c.position, c.title, c.kind) for c in chapters] == [
        (1, "Preface", "front_matter"),
        (2, "Chapter 1. The Beginning", "chapter"),
        (3, "Chapter 2. The Middle", "chapter"),
        (4, "Chapter 3. The End", "chapter"),
        (5, "Notes", "back_matter"),
    ]
    assert all(c.word_count > 0 for c in chapters)


async def test_reprocessing_replaces_chapters(
    ctx: dict[str, Any], session: AsyncSession, book: Book, queued_job: Job
) -> None:
    second_job = make_job(book)
    session.add(second_job)
    await session.flush()

    await process_book(ctx, str(queued_job.id))
    await process_book(ctx, str(second_job.id))

    assert len(await chapters_of(session, book)) == 5
    await session.refresh(second_job)
    assert second_job.status == "done"


async def test_success_clears_an_earlier_failure(
    ctx: dict[str, Any], session: AsyncSession, book: Book, queued_job: Job
) -> None:
    book.status = "failed"
    book.error_code = "NO_TEXT_LAYER"
    queued_job.error_code = "NO_TEXT_LAYER"
    queued_job.error_message = "x"
    await session.flush()

    await process_book(ctx, str(queued_job.id))
    await session.refresh(queued_job)
    await session.refresh(book)

    assert book.status == "ready"
    assert book.error_code is None
    assert queued_job.error_code is None
    assert queued_job.error_message is None


async def test_done_job_is_not_run_again(
    ctx: dict[str, Any], session: AsyncSession, book: Book, queued_job: Job
) -> None:
    await process_book(ctx, str(queued_job.id))
    await session.refresh(book)
    book.title = "Changed"
    await session.flush()

    await process_book(ctx | {"job_try": 2}, str(queued_job.id))
    await session.refresh(book)
    await session.refresh(queued_job)

    assert book.title == "Changed"
    assert queued_job.attempts == 1


async def test_unknown_job_id_is_ignored(ctx: dict[str, Any], queued_job: Job) -> None:
    assert await process_book(ctx, str(uuid.uuid4())) is None
