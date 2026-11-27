"""process_book, failures: parser outcomes are mapped as in the parse_book contract (section 5.3)
and fail at once; other errors are retried up to MAX_TRIES. Needs TEST_DATABASE_URL."""

import dataclasses
import time
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any

import arq
import pytest
from sqlalchemy import delete, func, select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from app.db.models import Book, Chapter, Chunk, Job
from app.parsing import ParsedBook, ParseErrorCode
from app.parsing.contract import USER_MESSAGES
from app.parsing.fake import fake_parse_book
from app.worker import tasks
from app.worker.tasks import (
    GENERIC_ERROR_MESSAGE,
    JOB_TIMEOUT_SECONDS,
    MAX_TRIES,
    fail_stale_jobs,
    process_book,
)
from tests.worker.conftest import make_job

pytestmark = pytest.mark.anyio


async def chapter_count(session: AsyncSession, book: Book) -> int:
    count = await session.scalar(
        select(func.count()).select_from(Chapter).where(Chapter.book_id == book.id)
    )
    return count or 0


def assert_failed(job: Job, book: Book, code: str, message: str) -> None:
    assert job.status == "failed"
    assert job.error_code == code
    assert job.error_message == message
    assert job.finished_at is not None
    assert book.status == "failed"
    assert book.error_code == code


async def without_chunks(session: AsyncSession, book: Book) -> None:
    """A book processed before chunking existed (week 1): chapters, no chunks. A new job parses it
    again; a book that has chunks continues from the chapter notes instead."""
    await session.execute(delete(Chunk).where(Chunk.book_id == book.id))
    await session.flush()


def break_saving(monkeypatch: pytest.MonkeyPatch) -> None:
    """Stands in for an infrastructure error, e.g. the database going away while saving."""

    async def broken_save(*_args: Any) -> None:
        raise RuntimeError("database is gone")

    monkeypatch.setattr(tasks, "_save_parsed_book", broken_save)


@pytest.mark.parametrize("code", list(ParseErrorCode))
async def test_parse_error_fails_job_and_book(
    code: ParseErrorCode,
    ctx: dict[str, Any],
    session: AsyncSession,
    book: Book,
    queued_job: Job,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("FAKE_PARSER_ERROR", code.value)

    await process_book(ctx, str(queued_job.id))
    await session.refresh(queued_job)
    await session.refresh(book)

    assert_failed(queued_job, book, code.value, USER_MESSAGES[code])
    assert queued_job.attempts == 1
    assert await chapter_count(session, book) == 0


async def test_contract_violation_fails_with_generic_message(
    ctx: dict[str, Any],
    session: AsyncSession,
    book: Book,
    queued_job: Job,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    def parse(file_path: Path) -> ParsedBook:
        return dataclasses.replace(fake_parse_book(file_path), chapters=())

    monkeypatch.setattr(tasks, "get_parse_book", lambda: parse)

    await process_book(ctx, str(queued_job.id))
    await session.refresh(queued_job)
    await session.refresh(book)

    assert_failed(queued_job, book, "PARSER_CONTRACT_VIOLATION", GENERIC_ERROR_MESSAGE)


async def test_other_parser_exception_fails_without_retry(
    ctx: dict[str, Any],
    session: AsyncSession,
    book: Book,
    queued_job: Job,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    def parse(_file_path: Path) -> ParsedBook:
        raise RuntimeError("boom")

    monkeypatch.setattr(tasks, "get_parse_book", lambda: parse)

    await process_book(ctx, str(queued_job.id))
    await session.refresh(queued_job)
    await session.refresh(book)

    assert_failed(queued_job, book, "PARSER_INTERNAL_ERROR", GENERIC_ERROR_MESSAGE)


async def test_parser_that_takes_too_long_fails_the_job(
    ctx: dict[str, Any],
    session: AsyncSession,
    book: Book,
    queued_job: Job,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    def parse(file_path: Path) -> ParsedBook:
        time.sleep(0.5)
        return fake_parse_book(file_path)

    monkeypatch.setattr(tasks, "get_parse_book", lambda: parse)
    monkeypatch.setattr(tasks, "PARSE_TIMEOUT_SECONDS", 0.05)

    await process_book(ctx, str(queued_job.id))
    await session.refresh(queued_job)
    await session.refresh(book)

    assert_failed(queued_job, book, "PARSER_INTERNAL_ERROR", GENERIC_ERROR_MESSAGE)
    assert await chapter_count(session, book) == 0


async def test_failed_parse_keeps_existing_chapters_untouched(
    ctx: dict[str, Any],
    session: AsyncSession,
    book: Book,
    queued_job: Job,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    await process_book(ctx, str(queued_job.id))
    await without_chunks(session, book)
    second_job = make_job(book)
    session.add(second_job)
    await session.flush()
    monkeypatch.setenv("FAKE_PARSER_ERROR", "CORRUPT_FILE")

    await process_book(ctx, str(second_job.id))
    await session.refresh(book)

    assert book.status == "failed"
    assert await chapter_count(session, book) == 5


@pytest.mark.parametrize("job_try", [1, 2])
async def test_infrastructure_error_is_retried(
    job_try: int,
    ctx: dict[str, Any],
    session: AsyncSession,
    book: Book,
    queued_job: Job,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    break_saving(monkeypatch)

    with pytest.raises(arq.Retry):
        await process_book(ctx | {"job_try": job_try}, str(queued_job.id))
    await session.refresh(queued_job)
    await session.refresh(book)

    assert queued_job.status == "running"
    assert queued_job.attempts == job_try
    assert book.status == "processing"
    assert await chapter_count(session, book) == 0


async def test_infrastructure_error_on_last_try_fails_job_and_book(
    ctx: dict[str, Any],
    session: AsyncSession,
    book: Book,
    queued_job: Job,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    break_saving(monkeypatch)

    await process_book(ctx | {"job_try": MAX_TRIES}, str(queued_job.id))
    await session.refresh(queued_job)
    await session.refresh(book)

    assert_failed(queued_job, book, "INTERNAL_ERROR", GENERIC_ERROR_MESSAGE)
    assert queued_job.attempts == 3
    assert await chapter_count(session, book) == 0


async def test_retry_after_infrastructure_error_succeeds(
    ctx: dict[str, Any], session: AsyncSession, book: Book, queued_job: Job
) -> None:
    with pytest.MonkeyPatch.context() as patch:
        break_saving(patch)
        with pytest.raises(arq.Retry):
            await process_book(ctx, str(queued_job.id))

    await process_book(ctx | {"job_try": 2}, str(queued_job.id))
    await session.refresh(queued_job)
    await session.refresh(book)

    assert queued_job.status == "done"
    assert queued_job.attempts == 2
    assert book.status == "ready"
    assert await chapter_count(session, book) == 5


async def test_failed_save_rolls_back_and_keeps_the_old_chapters(
    ctx: dict[str, Any],
    session: AsyncSession,
    book: Book,
    queued_job: Job,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    await process_book(ctx, str(queued_job.id))
    await without_chunks(session, book)
    second_job = make_job(book)
    session.add(second_job)
    await session.flush()
    real_save = tasks._save_parsed_book

    async def save_then_break(db: AsyncSession, saved_book: Book, *rest: Any) -> None:
        await real_save(db, saved_book, *rest)
        saved_book.title = "Half saved"
        await db.flush()  # the old chapters are deleted and the new ones written, then it breaks
        raise RuntimeError("database is gone")

    monkeypatch.setattr(tasks, "_save_parsed_book", save_then_break)

    await process_book(ctx | {"job_try": MAX_TRIES}, str(second_job.id))
    await session.refresh(second_job)
    await session.refresh(book)

    assert_failed(second_job, book, "INTERNAL_ERROR", GENERIC_ERROR_MESSAGE)
    assert book.title == "Fake Book"
    assert await chapter_count(session, book) == 5


def failing_once(factory: async_sessionmaker[AsyncSession]) -> Any:
    """A session factory whose first use fails, like a database that is down for a moment."""
    calls = 0

    def make_session() -> AsyncSession:
        nonlocal calls
        calls += 1
        if calls == 1:
            raise RuntimeError("database is gone")
        return factory()

    return make_session


async def test_error_before_parsing_is_retried(
    ctx: dict[str, Any], session: AsyncSession, book: Book, queued_job: Job
) -> None:
    broken_ctx = ctx | {"session_factory": failing_once(ctx["session_factory"])}

    with pytest.raises(arq.Retry):
        await process_book(broken_ctx, str(queued_job.id))
    await session.refresh(queued_job)

    assert queued_job.status == "queued"


async def test_error_before_parsing_on_last_try_fails_job_and_book(
    ctx: dict[str, Any], session: AsyncSession, book: Book, queued_job: Job
) -> None:
    broken_ctx = ctx | {
        "session_factory": failing_once(ctx["session_factory"]),
        "job_try": MAX_TRIES,
    }

    await process_book(broken_ctx, str(queued_job.id))
    await session.refresh(queued_job)
    await session.refresh(book)

    assert_failed(queued_job, book, "INTERNAL_ERROR", GENERIC_ERROR_MESSAGE)


async def test_failure_that_cannot_be_recorded_is_retried(
    ctx: dict[str, Any], book: Book, queued_job: Job, monkeypatch: pytest.MonkeyPatch
) -> None:
    async def broken_fail(*_args: Any) -> None:
        raise RuntimeError("database is gone")

    monkeypatch.setenv("FAKE_PARSER_ERROR", "NO_TEXT_LAYER")
    monkeypatch.setattr(tasks, "_fail", broken_fail)

    with pytest.raises(arq.Retry):
        await process_book(ctx, str(queued_job.id))


async def test_try_after_the_last_one_fails_the_job_without_parsing(
    ctx: dict[str, Any],
    session: AsyncSession,
    book: Book,
    queued_job: Job,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """ARQ starts one more try when earlier ones died with the worker (killed, out of memory)."""
    parsed_files: list[Path] = []

    def parse(file_path: Path) -> ParsedBook:
        parsed_files.append(file_path)
        return fake_parse_book(file_path)

    monkeypatch.setattr(tasks, "get_parse_book", lambda: parse)

    await process_book(ctx | {"job_try": MAX_TRIES + 1}, str(queued_job.id))
    await session.refresh(queued_job)
    await session.refresh(book)

    assert_failed(queued_job, book, "INTERNAL_ERROR", GENERIC_ERROR_MESSAGE)
    assert parsed_files == []


async def test_stale_running_job_is_failed_with_its_book(
    ctx: dict[str, Any], session: AsyncSession, book: Book, queued_job: Job
) -> None:
    queued_job.status = "running"
    queued_job.started_at = datetime.now(UTC) - timedelta(seconds=JOB_TIMEOUT_SECONDS + 600)
    book.status = "processing"
    await session.flush()

    assert await fail_stale_jobs(ctx) == 1
    await session.refresh(queued_job)
    await session.refresh(book)

    assert_failed(queued_job, book, "INTERNAL_ERROR", GENERIC_ERROR_MESSAGE)


async def test_jobs_that_are_not_stale_are_left_alone(
    ctx: dict[str, Any], session: AsyncSession, book: Book, queued_job: Job
) -> None:
    running = make_job(book)
    running.status = "running"
    running.started_at = datetime.now(UTC) - timedelta(seconds=30)
    done = make_job(book)
    done.status = "done"
    done.started_at = datetime.now(UTC) - timedelta(days=1)
    session.add_all([running, done])
    book.status = "processing"
    await session.flush()

    assert await fail_stale_jobs(ctx) == 0
    for job in (queued_job, running, done):
        await session.refresh(job)
    await session.refresh(book)

    assert (queued_job.status, running.status, done.status) == ("queued", "running", "done")
    assert book.status == "processing"
