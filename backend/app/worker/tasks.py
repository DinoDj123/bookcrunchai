"""Worker tasks. PostgreSQL is the single source of truth for job status (specification 5.4):
a task gets only the job ID from the queue, reads its input from the database and writes status,
progress and results back to it.

Week 1: process_book runs the parsing stage only. Chunking, embedding and chapter notes are added
to it in later tasks.

How failures are handled, so that a book never stays in "processing" (specification 4.5):
- The parser rejects the file, breaks the contract, crashes or hangs: the job fails at once. The
  same file would fail the same way again.
- Anything else (database, network): ARQ only runs a job again when the task raises Retry, so
  process_book turns every such error into Retry until MAX_TRIES is reached, then fails the job.
- The worker dies in the middle (killed, out of memory): ARQ starts the job again and counts a
  try. WorkerSettings allows one try more than MAX_TRIES; that extra try only marks the job failed.
- ARQ gives up without calling us (job timeout): fail_stale_jobs, run every few minutes, fails
  jobs that have been "running" longer than a job is allowed to run.
"""

import asyncio
import logging
import uuid
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any

from arq import Retry
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

MAX_TRIES = 3
# ARQ stops a task that runs longer than this (WorkerSettings.job_timeout).
JOB_TIMEOUT_SECONDS = 600
# The contract promises a 300-page book in under 60 seconds (rule 16). A parser that runs this long
# is stuck; without a limit of our own, ARQ's job timeout would stop the task before it could mark
# the book failed. Must stay below JOB_TIMEOUT_SECONDS.
PARSE_TIMEOUT_SECONDS: float = 300
# A job still "running" this long after it started was abandoned (see fail_stale_jobs).
STALE_AFTER_SECONDS = JOB_TIMEOUT_SECONDS + 120

# Shown for failures the user can do nothing about; the real error goes to the log.
GENERIC_ERROR_MESSAGE = "Something went wrong while processing this book. Please try again."
PARSER_CONTRACT_VIOLATION = "PARSER_CONTRACT_VIOLATION"
PARSER_INTERNAL_ERROR = "PARSER_INTERNAL_ERROR"
INTERNAL_ERROR = "INTERNAL_ERROR"

logger = logging.getLogger(__name__)


async def process_book(ctx: dict[str, Any], job_id: str) -> None:
    """Parse the book of a process_book job and store its chapters.

    ctx comes from ARQ: session_factory and storage_dir are set in WorkerSettings.on_startup,
    job_try is the number of this attempt (1 on the first one).
    """
    job_try: int = ctx["job_try"]
    if job_try > MAX_TRIES:
        # Earlier tries ended without an answer (the worker was stopped or killed while they ran).
        logger.error("Job %s: no tries left, marking it failed", job_id)
        await _fail_in_new_session(ctx, job_id)
        return
    try:
        async with ctx["session_factory"]() as session:
            await _parse_and_save(session, uuid.UUID(job_id), job_try, Path(ctx["storage_dir"]))
    except Exception as error:
        # Parser failures are handled inside _parse_and_save and never get here.
        logger.exception("Job %s: try %s of %s failed", job_id, job_try, MAX_TRIES)
        if job_try < MAX_TRIES:
            # The job keeps its current status; ARQ runs it again after the delay.
            raise Retry(defer=5 * job_try) from error
        await _fail_in_new_session(ctx, job_id)


async def _parse_and_save(
    session: AsyncSession, job_id: uuid.UUID, job_try: int, storage_dir: Path
) -> None:
    job = await session.get(Job, job_id)
    # A job that is already done was delivered twice by the queue; running it again would only
    # repeat finished work.
    if job is None or job.status == JobStatus.DONE:
        return
    book = await session.get_one(Book, job.book_id)

    # Committed before the slow part, so the UI sees "running" while the book is parsed.
    job.status = JobStatus.RUNNING
    job.stage = JobStage.PARSING
    job.progress = 0
    job.attempts = job_try
    job.started_at = datetime.now(UTC)
    job.finished_at = None
    job.error_code = None
    job.error_message = None
    book.status = BookStatus.PROCESSING
    book.error_code = None
    await session.commit()

    # books.file_path is the stored file's name relative to the storage directory.
    file_path = storage_dir / book.file_path
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
        await _fail(session, job, book, error.code.value, error.user_message)
        return
    except ContractViolation:
        logger.exception("Job %s: the parser broke the parse_book contract", job.id)
        await _fail(session, job, book, PARSER_CONTRACT_VIOLATION, GENERIC_ERROR_MESSAGE)
        return
    except Exception:
        logger.exception("Job %s: the parser raised an unexpected error", job.id)
        await _fail(session, job, book, PARSER_INTERNAL_ERROR, GENERIC_ERROR_MESSAGE)
        return

    await _save_parsed_book(session, book, parsed)
    job.status = JobStatus.DONE
    job.progress = 100
    job.finished_at = datetime.now(UTC)
    await session.commit()


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
    book.title = parsed.title
    book.author = parsed.author
    book.source_language = parsed.language
    book.word_count = parsed.word_count
    book.chapters_detected = parsed.chapters_detected
    # Week 1: parsing is the whole pipeline, so a parsed book is ready (contract section 5.3).
    book.status = BookStatus.READY


async def _fail(session: AsyncSession, job: Job, book: Book, code: str, message: str) -> None:
    """Mark the job and its book failed together, so a book never stays in "processing"
    (specification 4.5)."""
    job.status = JobStatus.FAILED
    job.error_code = code
    job.error_message = message
    job.finished_at = datetime.now(UTC)
    book.status = BookStatus.FAILED
    book.error_code = code
    await session.commit()


async def _fail_in_new_session(ctx: dict[str, Any], job_id: str) -> None:
    """Fail a job with INTERNAL_ERROR after its last try. A new session, because the one the try
    used may be broken. If this fails too (database still down), fail_stale_jobs cleans up later.
    """
    async with ctx["session_factory"]() as session:
        job = await session.get(Job, uuid.UUID(job_id))
        if job is None or job.status == JobStatus.DONE:
            return
        book = await session.get_one(Book, job.book_id)
        await _fail(session, job, book, INTERNAL_ERROR, GENERIC_ERROR_MESSAGE)


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
