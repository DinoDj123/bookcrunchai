"""run_job on its own, with small handlers instead of a real task: what happens to the jobs row,
and that the thing the job works on is left to the handler and on_failure."""

from typing import Any

import arq
import pytest
from sqlalchemy.ext.asyncio import AsyncSession

from app.db.models import Book, Job
from app.worker.jobs import GENERIC_ERROR_MESSAGE, MAX_TRIES, JobFailed, run_job

pytestmark = pytest.mark.anyio


async def rename_book(_ctx: dict[str, Any], session: AsyncSession, job: Job) -> None:
    """A handler that changes something and leaves the commit to run_job."""
    book = await session.get_one(Book, job.book_id)
    book.title = "Renamed"


async def fail_the_book(session: AsyncSession, job: Job) -> None:
    book = await session.get_one(Book, job.book_id)
    book.status = "failed"
    book.error_code = job.error_code


async def test_handler_runs_with_the_job_running_and_then_the_job_is_done(
    ctx: dict[str, Any], session: AsyncSession, book: Book, queued_job: Job
) -> None:
    seen: dict[str, Any] = {}

    async def handler(handler_ctx: dict[str, Any], handler_session: AsyncSession, job: Job) -> None:
        seen.update(status=job.status, attempts=job.attempts, storage=handler_ctx["storage_dir"])
        await rename_book(handler_ctx, handler_session, job)

    await run_job(ctx, str(queued_job.id), handler)
    await session.refresh(queued_job)
    await session.refresh(book)

    assert seen == {"status": "running", "attempts": 1, "storage": ctx["storage_dir"]}
    assert (queued_job.status, queued_job.progress) == ("done", 100)
    assert queued_job.started_at is not None
    assert queued_job.finished_at is not None
    assert book.title == "Renamed"  # the handler's uncommitted change was committed with "done"
    assert book.status == "uploaded"  # run_job itself never touches the book


async def test_done_job_is_skipped(
    ctx: dict[str, Any], session: AsyncSession, book: Book, queued_job: Job
) -> None:
    queued_job.status = "done"
    await session.flush()

    await run_job(ctx, str(queued_job.id), rename_book)
    await session.refresh(book)

    assert book.title == "book"


async def test_job_failed_fails_the_job_with_its_code_and_discards_the_handlers_changes(
    ctx: dict[str, Any], session: AsyncSession, book: Book, queued_job: Job
) -> None:
    async def handler(handler_ctx: dict[str, Any], handler_session: AsyncSession, job: Job) -> None:
        await rename_book(handler_ctx, handler_session, job)
        raise JobFailed("COST_LIMIT_EXCEEDED", "This book has reached its cost limit.")

    await run_job(ctx, str(queued_job.id), handler, on_failure=fail_the_book)
    await session.refresh(queued_job)
    await session.refresh(book)

    assert queued_job.status == "failed"
    assert queued_job.error_code == "COST_LIMIT_EXCEEDED"
    assert queued_job.error_message == "This book has reached its cost limit."
    assert queued_job.finished_at is not None
    assert book.title == "book"
    assert (book.status, book.error_code) == ("failed", "COST_LIMIT_EXCEEDED")


async def test_job_failed_without_a_message_shows_the_generic_one(
    ctx: dict[str, Any], session: AsyncSession, book: Book, queued_job: Job
) -> None:
    async def handler(*_args: Any) -> None:
        raise JobFailed("SOME_CODE")

    await run_job(ctx, str(queued_job.id), handler)
    await session.refresh(queued_job)
    await session.refresh(book)

    assert (queued_job.error_code, queued_job.error_message) == ("SOME_CODE", GENERIC_ERROR_MESSAGE)
    assert book.status == "uploaded"  # no on_failure: nothing but the job changes


async def test_other_error_is_retried_and_the_handlers_changes_are_discarded(
    ctx: dict[str, Any], session: AsyncSession, book: Book, queued_job: Job
) -> None:
    async def handler(handler_ctx: dict[str, Any], handler_session: AsyncSession, job: Job) -> None:
        await rename_book(handler_ctx, handler_session, job)
        raise RuntimeError("network is gone")

    with pytest.raises(arq.Retry):
        await run_job(ctx, str(queued_job.id), handler, on_failure=fail_the_book)
    await session.refresh(queued_job)
    await session.refresh(book)

    assert queued_job.status == "running"  # waiting for the next try
    assert (book.title, book.status) == ("book", "uploaded")


async def test_other_error_on_the_last_try_fails_with_internal_error(
    ctx: dict[str, Any], session: AsyncSession, book: Book, queued_job: Job
) -> None:
    async def handler(*_args: Any) -> None:
        raise RuntimeError("network is gone")

    await run_job(
        ctx | {"job_try": MAX_TRIES}, str(queued_job.id), handler, on_failure=fail_the_book
    )
    await session.refresh(queued_job)
    await session.refresh(book)

    assert (queued_job.status, queued_job.attempts) == ("failed", MAX_TRIES)
    assert (queued_job.error_code, queued_job.error_message) == (
        "INTERNAL_ERROR",
        GENERIC_ERROR_MESSAGE,
    )
    assert (book.status, book.error_code) == ("failed", "INTERNAL_ERROR")


async def test_try_after_the_last_one_fails_the_job_without_running_the_handler(
    ctx: dict[str, Any], session: AsyncSession, book: Book, queued_job: Job
) -> None:
    await run_job(
        ctx | {"job_try": MAX_TRIES + 1}, str(queued_job.id), rename_book, on_failure=fail_the_book
    )
    await session.refresh(queued_job)
    await session.refresh(book)

    assert (queued_job.status, queued_job.error_code) == ("failed", "INTERNAL_ERROR")
    assert (book.title, book.status) == ("book", "failed")
