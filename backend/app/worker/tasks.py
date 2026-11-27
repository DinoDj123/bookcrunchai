"""Worker tasks. PostgreSQL is the single source of truth for job status (specification 5.4):
a task gets only the job ID from the queue, reads its input from the database and writes status,
progress and results back to it.

process_book runs the stages parsing -> chunking -> chapter notes (Appendix C) and then marks the
book ready. Embedding is added in week 3. Chunking and chapter notes come from Track A; until
their contract is agreed, the worker uses the fakes in app.worker.pipeline_draft.

Every stage is stored as soon as it is finished, and a retried job continues from there
(specification 4.5): a book that already has chunks is not parsed or chunked again, and only the
chapters without notes get notes. So a retry never pays twice for the same chapter notes.

What every task shares (skipping a finished job, "running", retries, failing with a code) is
run_job in app.worker.jobs. How failures are handled, so that a book never stays in "processing"
(specification 4.5):
- The parser rejects the file, breaks the contract, crashes or hangs: the job fails at once
  (JobFailed). The same file would fail the same way again. The same for the chunker and the
  chapter notes code.
- An LLM call that cannot succeed (cost limit, refused request): the job fails at once.
- Anything else (database, network, the LLM service unavailable): run_job has ARQ run the job
  again until MAX_TRIES is reached, then fails it. A worker that died in the middle is covered
  there too.
- ARQ gives up without calling us (job timeout): fail_stale_jobs, run every few minutes, fails
  jobs that have been "running" longer than a job is allowed to run.
"""

import asyncio
import logging
import uuid
from collections import defaultdict
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any

from sqlalchemy import delete, exists, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.config import get_settings
from app.db.models import Book, BookStatus, Chapter, Chunk, Job, JobStage, JobStatus, JobType
from app.llm.base import LLMClient, LLMError, LLMErrorCode
from app.llm.factory import make_llm_client
from app.llm.fake import FakeLLMClient
from app.parsing import (
    ChapterKind,
    ContractViolation,
    ParsedBook,
    ParseError,
    get_parse_book,
    validate_parsed_book,
)
from app.worker.jobs import GENERIC_ERROR_MESSAGE, INTERNAL_ERROR, MAX_TRIES, JobFailed, run_job

# TEMPORARY: from app.pipeline once the chunking contract is agreed with Track A (see the module).
from app.worker.pipeline_draft import (
    ChapterForNotes,
    ChunkDraft,
    PipelineContractViolation,
    WriteChapterNotesFn,
    get_chunk_chapter,
    get_write_chapter_notes,
    notes_implementation,
    validate_chapter_notes,
    validate_chunks,
)

__all__ = ["GENERIC_ERROR_MESSAGE", "MAX_TRIES", "fail_stale_jobs", "process_book"]

# ARQ stops a task that runs longer than this (WorkerSettings.job_timeout).
JOB_TIMEOUT_SECONDS = 600
# The contract promises a 300-page book in under 60 seconds (rule 16). A parser that runs this long
# is stuck; without a limit of our own, ARQ's job timeout would stop the task before it could mark
# the book failed. Must stay below JOB_TIMEOUT_SECONDS.
PARSE_TIMEOUT_SECONDS: float = 300
# Chunking is quick (the draft contract asks for under 2 seconds for a long novel); this only
# catches a chunker that hangs.
CHUNK_TIMEOUT_SECONDS: float = 60
# A job still "running" this long after it started was abandoned (see fail_stale_jobs).
STALE_AFTER_SECONDS = JOB_TIMEOUT_SECONDS + 120

# jobs.progress is one percentage for the whole job, so the bar in the UI never goes back:
# parsing ends at 10 %, chunking at 20 %, and chapter notes fill the rest, chapter by chapter.
PROGRESS_PARSED = 10
PROGRESS_CHUNKED = 20

# Chapters that get chapter notes. PROPOSAL in the draft contract (section 8), to be confirmed by
# Track A: back matter (notes, index, other works printed after the book) adds cost and little to
# a summary of the book.
NOTES_KINDS = (ChapterKind.FRONT_MATTER.value, ChapterKind.CHAPTER.value)

PARSER_CONTRACT_VIOLATION = "PARSER_CONTRACT_VIOLATION"
PARSER_INTERNAL_ERROR = "PARSER_INTERNAL_ERROR"
CHUNKER_CONTRACT_VIOLATION = "CHUNKER_CONTRACT_VIOLATION"
CHUNKER_INTERNAL_ERROR = "CHUNKER_INTERNAL_ERROR"
NOTES_CONTRACT_VIOLATION = "NOTES_CONTRACT_VIOLATION"
NOTES_INTERNAL_ERROR = "NOTES_INTERNAL_ERROR"

COST_LIMIT_MESSAGE = "This book has reached its cost limit for AI processing."
LLM_UNAVAILABLE_MESSAGE = "The AI service is not available at the moment. Please try again later."

logger = logging.getLogger(__name__)


async def process_book(ctx: dict[str, Any], job_id: str) -> None:
    """Parse, chunk and write chapter notes for the book of a process_book job.

    ctx comes from ARQ: session_factory and storage_dir are set in WorkerSettings.on_startup.
    """
    await run_job(ctx, job_id, _process, on_failure=_fail)


async def _process(ctx: dict[str, Any], session: AsyncSession, job: Job) -> None:
    book = await session.get_one(Book, job.book_id)
    book.status = BookStatus.PROCESSING
    book.error_code = None

    # Chapters and chunks are stored together, so chunks mean both stages are done.
    if await session.scalar(select(exists().where(Chunk.book_id == book.id))):
        logger.info("Job %s: book %s is already chunked, continuing with notes", job.id, book.id)
    else:
        await _parse_and_chunk(ctx, session, job, book)

    await _write_notes(ctx, session, job, book)
    # Not committed here: run_job commits it together with the job becoming "done".
    book.status = BookStatus.READY


# --------------------------------------------------------------------------- parsing and chunking


async def _parse_and_chunk(
    ctx: dict[str, Any], session: AsyncSession, job: Job, book: Book
) -> None:
    # Committed before the slow part, so the UI sees the stage while the book is parsed.
    job.stage = JobStage.PARSING
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

    job.stage = JobStage.CHUNKING
    job.progress = PROGRESS_PARSED
    await session.commit()

    # Chunking cannot fail for a valid book (draft contract rule 14): any error is a bug in the
    # chunker, and the same book would hit it again, so the job fails at once.
    try:
        async with asyncio.timeout(CHUNK_TIMEOUT_SECONDS):
            chunks = await asyncio.to_thread(_chunk_book, parsed)
    except PipelineContractViolation as error:
        logger.exception("Job %s: the chunker broke the contract", job.id)
        raise JobFailed(CHUNKER_CONTRACT_VIOLATION) from error
    except Exception as error:
        logger.exception("Job %s: the chunker raised an unexpected error", job.id)
        raise JobFailed(CHUNKER_INTERNAL_ERROR) from error

    await _save_parsed_book(session, book, parsed, chunks)
    job.progress = PROGRESS_CHUNKED
    # Committed now, not at the end of the job: a retry after a failure in the notes stage starts
    # from here instead of parsing again (specification 4.5).
    await session.commit()


def _chunk_book(parsed: ParsedBook) -> dict[int, tuple[ChunkDraft, ...]]:
    """The chunks of every chapter, by chapter position, each checked against the contract."""
    chunk_chapter = get_chunk_chapter()
    result: dict[int, tuple[ChunkDraft, ...]] = {}
    for chapter in parsed.chapters:
        chunks = chunk_chapter(chapter)
        validate_chunks(chapter, chunks)
        result[chapter.position] = chunks
    return result


async def _save_parsed_book(
    session: AsyncSession,
    book: Book,
    parsed: ParsedBook,
    chunks: dict[int, tuple[ChunkDraft, ...]],
) -> None:
    """Store the parser's result (contract section 5.4) and the chunks. validate_parsed_book() has
    already checked that titles, author and language fit their columns. The caller commits, so the
    old chapters and their chunks are replaced by the new ones in one transaction and
    re-processing never duplicates them."""
    # Chunks, and later embeddings and citations, go with their chapters (ON DELETE CASCADE).
    await session.execute(delete(Chapter).where(Chapter.book_id == book.id))
    for chapter in parsed.chapters:
        chapter_id = uuid.uuid4()
        session.add(
            Chapter(
                id=chapter_id,
                book_id=book.id,
                position=chapter.position,
                title=chapter.title,
                kind=chapter.kind.value,
                word_count=chapter.word_count,
            )
        )
        session.add_all(
            Chunk(
                book_id=book.id,
                chapter_id=chapter_id,
                position=chunk.position,
                text=chunk.text,
                token_count=chunk.token_count,
                page_start=chunk.page_start,
            )
            for chunk in chunks[chapter.position]
        )
    # Without a metadata title the parser only has the stored file name, which is a UUID; the title
    # set at upload (the cleaned original file name) is better, so it stays (contract 1.1, 5.4).
    if parsed.title_from_metadata:
        book.title = parsed.title
    book.author = parsed.author
    book.source_language = parsed.language
    book.word_count = parsed.word_count
    book.chapters_detected = parsed.chapters_detected


# --------------------------------------------------------------------------- chapter notes


def _notes_progress(done: int, total: int) -> int:
    if total == 0:
        return 100
    return PROGRESS_CHUNKED + (100 - PROGRESS_CHUNKED) * done // total


def _make_notes_llm(ctx: dict[str, Any], book: Book, job: Job) -> LLMClient:
    """The fake notes make no calls, so they need no client and no API key."""
    if notes_implementation() == "fake":
        return FakeLLMClient()
    return make_llm_client(ctx["session_factory"], book.id, job.id)


async def _write_notes(ctx: dict[str, Any], session: AsyncSession, job: Job, book: Book) -> None:
    """Chapter notes (FR-007) for every chapter that should have them and has none yet. Chapters
    run in parallel; the LLM client limits how many requests are open at once. Each chapter's notes
    are committed as soon as they arrive, so a failure later keeps them."""
    rows = await session.scalars(
        select(Chapter)
        .where(Chapter.book_id == book.id, Chapter.kind.in_(NOTES_KINDS))
        .order_by(Chapter.position)
    )
    chapters = list(rows)
    todo = [chapter for chapter in chapters if chapter.notes is None]
    done = len(chapters) - len(todo)

    job.stage = JobStage.CHAPTER_NOTES
    job.progress = _notes_progress(done, len(chapters))
    await session.commit()
    if not todo:
        return

    texts: dict[uuid.UUID, list[str]] = defaultdict(list)
    chunk_rows = await session.execute(
        select(Chunk.chapter_id, Chunk.text)
        .where(Chunk.chapter_id.in_([chapter.id for chapter in todo]))
        .order_by(Chunk.chapter_id, Chunk.position)
    )
    for chapter_id, text in chunk_rows:
        texts[chapter_id].append(text)

    write = get_write_chapter_notes()
    llm = _make_notes_llm(ctx, book, job)
    model = get_settings().claude_model_fast

    async def notes_for(chapter: Chapter) -> tuple[Chapter, str]:
        item = ChapterForNotes(
            position=chapter.position,
            title=chapter.title,
            kind=ChapterKind(chapter.kind),
            chunks=tuple(texts[chapter.id]),
        )
        notes = await _call_notes(ctx, job, write, item, book.source_language, llm, model)
        return chapter, notes

    tasks = [asyncio.create_task(notes_for(chapter)) for chapter in todo]
    try:
        for finished in asyncio.as_completed(tasks):
            chapter, notes = await finished
            chapter.notes = notes
            done += 1
            job.progress = _notes_progress(done, len(chapters))
            await session.commit()
    finally:
        # One chapter failed: the others are stopped, so the job ends now. Notes already
        # committed stay for the next try.
        for task in tasks:
            task.cancel()
        await asyncio.gather(*tasks, return_exceptions=True)


async def _call_notes(
    ctx: dict[str, Any],
    job: Job,
    write: WriteChapterNotesFn,
    chapter: ChapterForNotes,
    language: str | None,
    llm: LLMClient,
    model: str,
) -> str:
    """One chapter's notes, with every failure turned into what the job should do."""
    try:
        notes = await write(chapter, language=language, llm=llm, model=model)
        validate_chapter_notes(notes)
    except LLMError as error:
        if error.code == LLMErrorCode.COST_LIMIT_EXCEEDED:
            raise JobFailed(error.code.value, COST_LIMIT_MESSAGE) from error
        if not error.retryable:
            logger.error("Job %s, chapter %s: %s", job.id, chapter.position, error.message)
            raise JobFailed(error.code.value) from error
        if ctx["job_try"] >= MAX_TRIES:
            # The client has already tried every call several times, in every try of the job.
            raise JobFailed(error.code.value, LLM_UNAVAILABLE_MESSAGE) from error
        raise  # run_job tries the job again later; the notes written so far stay
    except PipelineContractViolation as error:
        logger.exception("Job %s, chapter %s: notes break the contract", job.id, chapter.position)
        raise JobFailed(NOTES_CONTRACT_VIOLATION) from error
    except Exception as error:
        logger.exception("Job %s, chapter %s: chapter notes failed", job.id, chapter.position)
        raise JobFailed(NOTES_INTERNAL_ERROR) from error
    return notes


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
