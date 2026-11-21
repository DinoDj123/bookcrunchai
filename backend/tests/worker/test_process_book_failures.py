"""process_book, failures: parser outcomes are mapped as in the parse_book contract (section 5.3)
and fail at once; other errors are retried up to MAX_TRIES. Needs TEST_DATABASE_URL."""

import dataclasses
import time
from pathlib import Path
from typing import Any

import arq
import pytest
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.db.models import Book, Chapter, Job
from app.parsing import ParsedBook, ParseErrorCode
from app.parsing.contract import USER_MESSAGES
from app.parsing.fake import fake_parse_book
from app.worker import tasks
from app.worker.tasks import GENERIC_ERROR_MESSAGE, MAX_TRIES, process_book
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
