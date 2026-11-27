"""Worker tasks. PostgreSQL is the single source of truth for job status (specification 5.4):
a task gets only the job ID from the queue, reads its input from the database and writes status,
progress and results back to it.

Week 1: process_book runs the parsing stage only. Chunking, embedding and chapter notes are added
to it in later tasks.

What every task shares (skipping a finished job, "running", retries, failing with a code) is
run_job in app.worker.jobs. How failures are handled, so that a book never stays in "processing"
(specification 4.5):
- The parser rejects the file, breaks the contract, crashes or hangs: the job fails at once
  (JobFailed). The same file would fail the same way again.
- Anything else (database, network): run_job has ARQ run the job again until MAX_TRIES is reached,
  then fails it. A worker that died in the middle is covered there too.
- ARQ gives up without calling us (job timeout): fail_stale_jobs, run every few minutes, fails
  jobs that have been "running" longer than a job is allowed to run.
"""

import asyncio
import logging
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any

from sqlalchemy import delete, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.db.models import Book, BookStatus, Chapter, Job, JobStage, JobStatus, JobType
from app.parsing import (
    ContractViolation,
    ParsedBook,
    ParseError,
    get_parse_book,
    validate_parsed_book,
)
from app.worker.jobs import GENERIC_ERROR_MESSAGE, INTERNAL_ERROR, MAX_TRIES, JobFailed, run_job

__all__ = ["GENERIC_ERROR_MESSAGE", "MAX_TRIES", "fail_stale_jobs", "process_book"]

# ARQ stops a task that runs longer than this (WorkerSettings.job_timeout).
JOB_TIMEOUT_SECONDS = 600
# The contract promises a 300-page book in under 60 seconds (rule 16). A parser that runs this long
# is stuck; without a limit of our own, ARQ's job timeout would stop the task before it could mark
# the book failed. Must stay below JOB_TIMEOUT_SECONDS.
PARSE_TIMEOUT_SECONDS: float = 300
# A job still "running" this long after it started was abandoned (see fail_stale_jobs).
STALE_AFTER_SECONDS = JOB_TIMEOUT_SECONDS + 120

PARSER_CONTRACT_VIOLATION = "PARSER_CONTRACT_VIOLATION"
PARSER_INTERNAL_ERROR = "PARSER_INTERNAL_ERROR"

logger = logging.getLogger(__name__)


async def process_book(ctx: dict[str, Any], job_id: str) -> None:
    """Parse the book of a process_book job and store its chapters.

    ctx comes from ARQ: session_factory and storage_dir are set in WorkerSettings.on_startup.
    """
    await run_job(ctx, job_id, _parse_and_save, on_failure=_fail)


async def _parse_and_save(ctx: dict[str, Any], session: AsyncSession, job: Job) -> None:
    book = await session.get_one(Book, job.book_id)

    # Committed before the slow part, so the UI sees the stage while the book is parsed.
    job.stage = JobStage.PARSING
    book.status = BookStatus.PROCESSING
    book.error_code = None
    await session.commit()

    # books.file_path is the stored file's name relative to the storage directory.
    file_path = Path(ctx["storage_dir"]) / book.file_path
    # Whatever goes wrong inside this block is an answer about the file or a bug in the parser
    # (contract section 5.3).
    try:
        # The parser is synchronous and may take up to a minute (contract rule 16); in a thread it
        # does not block the worker's event loop.
        async with asyncio.timeout(PARSE_TIMEOUT_SECONDS):
            parsed = await asyncio.to_thread(get_parse_book(), file_path)
        validate_parsed_book(parsed)
    except ParseError as error:
        logger.info("Job %s: book %s not parsed: %s", job.id, book.id, error)
        raise JobFailed(error.code.value, error.user_message) from error
    except ContractViolation as error:
        logger.exception("Job %s: the parser broke the parse_book contract", job.id)
        raise JobFailed(PARSER_CONTRACT_VIOLATION) from error
    except Exception as error:
        logger.exception("Job %s: the parser raised an unexpected error", job.id)
        raise JobFailed(PARSER_INTERNAL_ERROR) from error

    # Not committed here: run_job commits the chapters together with the job becoming "done".
    await _save_parsed_book(session, book, parsed)


async def _save_parsed_book(session: AsyncSession, book: Book, parsed: ParsedBook) -> None:
    """Store the parser's result (contract section 5.4). validate_parsed_book() has already checked
    that titles, author and language fit their columns. The caller commits, so the old chapters
    are replaced by the new ones in one transaction and re-processing never duplicates them."""
    await session.execute(delete(Chapter).where(Chapter.book_id == book.id))
    session.add_all(
        Chapter(
            book_id=book.id,
            position=chapter.position,
            title=chapter.title,
            kind=chapter.kind.value,
            word_count=chapter.word_count,
        )
        for chapter in parsed.chapters
    )
    # Without a metadata title the parser only has the stored file name, which is a UUID; the title
    # set at upload (the cleaned original file name) is better, so it stays (contract 1.1, 5.4).
    if parsed.title_from_metadata:
        book.title = parsed.title
    book.author = parsed.author
    book.source_language = parsed.language
    book.word_count = parsed.word_count
    book.chapters_detected = parsed.chapters_detected
    # Week 1: parsing is the whole pipeline, so a parsed book is ready (contract section 5.3).
    book.status = BookStatus.READY


async def _fail(session: AsyncSession, job: Job) -> None:
    """run_job's on_failure: the book of a failed process_book job fails with it, in the same
    transaction, so a book never stays in "processing" (specification 4.5)."""
    book = await session.get_one(Book, job.book_id)
    book.status = BookStatus.FAILED
    book.error_code = job.error_code


async def fail_stale_jobs(ctx: dict[str, Any]) -> int:
    """Fail jobs that are "running" although nothing can be running them any more, and return how
    many there were. Runs on a schedule (WorkerSettings.cron_jobs).

    A try sets started_at and ARQ stops it after JOB_TIMEOUT_SECONDS, so a job that started longer
    ago than STALE_AFTER_SECONDS and is still "running" lost its worker without recording it.
    """
    started_before = datetime.now(UTC) - timedelta(seconds=STALE_AFTER_SECONDS)
    async with ctx["session_factory"]() as session:
        jobs = await session.scalars(
            select(Job)
            .where(Job.status == JobStatus.RUNNING, Job.started_at < started_before)
            .with_for_update(skip_locked=True)
        )
        failed = 0
        for job in jobs:
            logger.error(
                "Job %s: still running since %s, marking it failed", job.id, job.started_at
            )
            job.status = JobStatus.FAILED
            job.error_code = INTERNAL_ERROR
            job.error_message = GENERIC_ERROR_MESSAGE
            job.finished_at = datetime.now(UTC)
            book = await session.get_one(Book, job.book_id)
            # Only process_book owns the book's status; a failed summary leaves the book ready.
            if job.type == JobType.PROCESS_BOOK and book.status == BookStatus.PROCESSING:
                book.status = BookStatus.FAILED
                book.error_code = INTERNAL_ERROR
            failed += 1
        await session.commit()
        return failed
