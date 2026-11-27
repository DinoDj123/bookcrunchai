"""Worker tasks. PostgreSQL is the single source of truth for job status (specification 5.4):
a task gets only the job ID from the queue, reads its input from the database and writes status,
progress and results back to it.

process_book runs the stages parsing -> chunking -> chapter notes (Appendix C) and then marks the
book ready. Embedding is added in week 3. generate_summary runs the reduce step for one summary
(FR-008): one length and one language, from the chapter notes. Chunking, chapter notes and the
reduce step come from Track A; until their contract is agreed, the worker uses the fakes in
app.worker.pipeline_draft.

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
from app.db.models import (
    Book,
    BookStatus,
    Chapter,
    ChapterSummary,
    Chunk,
    Job,
    JobStage,
    JobStatus,
    JobType,
    KeyIdea,
    Language,
    Summary,
    SummaryLength,
    SummaryStatus,
)
from app.db.queries import latest_summary_job
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
    ChapterForSummary,
    ChunkDraft,
    PipelineContractViolation,
    SummaryDraft,
    WriteBookSummaryFn,
    WriteChapterNotesFn,
    drop_invalid_key_ideas,
    get_chunk_chapter,
    get_write_book_summary,
    get_write_chapter_notes,
    notes_implementation,
    summary_implementation,
    summary_word_count,
    validate_chapter_notes,
    validate_chunks,
    validate_summary,
)

__all__ = [
    "GENERIC_ERROR_MESSAGE",
    "MAX_TRIES",
    "fail_stale_jobs",
    "generate_summary",
    "process_book",
]

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
NO_CHAPTER_NOTES = "NO_CHAPTER_NOTES"
SUMMARY_INVALID = "SUMMARY_INVALID"
SUMMARY_INTERNAL_ERROR = "SUMMARY_INTERNAL_ERROR"
SUMMARY_TIMEOUT = "SUMMARY_TIMEOUT"

COST_LIMIT_MESSAGE = "This book has reached its cost limit for AI processing."
LLM_UNAVAILABLE_MESSAGE = "The AI service is not available at the moment. Please try again later."
SUMMARY_INVALID_MESSAGE = "The AI did not produce a usable summary. Please try again."
SUMMARY_TIMEOUT_MESSAGE = "The AI took too long to write the summary. Please try again later."

# FR-008: a result that fails validation gets one more call, then the summary fails.
SUMMARY_ATTEMPTS = 2
# Both calls together, each with the LLM client's own retries. Without a limit of our own, a slow
# model would run into ARQ's job timeout, which stops the task without failing the job, and the
# summary would stay "running" until fail_stale_jobs. Must stay below JOB_TIMEOUT_SECONDS.
SUMMARY_TIMEOUT_SECONDS: float = 480
# summaries.model of a summary written by the fake reduce step, which calls no model.
FAKE_MODEL = "fake"

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
    model = get_settings().llm_model_fast

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
        _fail_on_llm_error(ctx, job, error, f"chapter {chapter.position}")
        raise  # run_job tries the job again later; the notes written so far stay
    except PipelineContractViolation as error:
        logger.exception("Job %s, chapter %s: notes break the contract", job.id, chapter.position)
        raise JobFailed(NOTES_CONTRACT_VIOLATION) from error
    except Exception as error:
        logger.exception("Job %s, chapter %s: chapter notes failed", job.id, chapter.position)
        raise JobFailed(NOTES_INTERNAL_ERROR) from error
    return notes


def _fail_on_llm_error(ctx: dict[str, Any], job: Job, error: LLMError, where: str) -> None:
    """Raise JobFailed for an LLM error the job should fail on now. Returns for an error worth
    another try of the job; the caller then re-raises it and run_job has ARQ try again."""
    if error.code == LLMErrorCode.COST_LIMIT_EXCEEDED:
        raise JobFailed(error.code.value, COST_LIMIT_MESSAGE) from error
    if not error.retryable:
        logger.error("Job %s, %s: %s", job.id, where, error.message)
        raise JobFailed(error.code.value) from error
    if ctx["job_try"] >= MAX_TRIES:
        # The client has already tried every call several times, in every try of the job.
        raise JobFailed(error.code.value, LLM_UNAVAILABLE_MESSAGE) from error


async def _fail(session: AsyncSession, job: Job) -> None:
    """run_job's on_failure: the book of a failed process_book job fails with it, in the same
    transaction, so a book never stays in "processing" (specification 4.5)."""
    book = await session.get_one(Book, job.book_id)
    book.status = BookStatus.FAILED
    book.error_code = job.error_code


# --------------------------------------------------------------------------- book summary


async def generate_summary(ctx: dict[str, Any], job_id: str) -> None:
    """Write the summary of a generate_summary job (FR-008) from the book's chapter notes.

    job.params holds summary_id, length and language; the API has saved the summary as "queued".
    A summary that is already ready is left as it is (FR-014). A failed job fails its summary;
    the book stays ready, because the book itself is fine.
    """
    await run_job(ctx, job_id, _summarise, on_failure=_fail_summary)


def _summary_id(job: Job) -> uuid.UUID | None:
    value = (job.params or {}).get("summary_id")
    return uuid.UUID(value) if value else None


async def _is_superseded(session: AsyncSession, summary: Summary, job: Job) -> bool:
    """True if the summary is ready, or a newer job has taken it over. An old job can come back
    after fail_stale_jobs failed it (ARQ still holds a try of it) while the user has already asked
    again; then only the newest job may write the summary, or it would be paid for and stored
    twice."""
    if summary.status == SummaryStatus.READY:
        return True
    latest = await latest_summary_job(session, summary)
    return latest is None or latest.id != job.id


async def _summarise(ctx: dict[str, Any], session: AsyncSession, job: Job) -> None:
    summary_id = _summary_id(job)
    summary = await session.get(Summary, summary_id) if summary_id else None
    if summary is None:
        raise JobFailed(INTERNAL_ERROR)  # the API always saves the summary first
    if await _is_superseded(session, summary, job):
        logger.info("Job %s: summary %s is ready or has a newer job", job.id, summary.id)
        return
    book = await session.get_one(Book, job.book_id)
    summary.status = SummaryStatus.RUNNING
    job.stage = JobStage.REDUCE
    await session.commit()

    rows = await session.scalars(
        select(Chapter)
        .where(
            Chapter.book_id == book.id,
            Chapter.kind.in_(NOTES_KINDS),
            Chapter.notes.is_not(None),
        )
        .order_by(Chapter.position)
    )
    chapters = tuple(ChapterForSummary(c.position, c.title, c.notes or "") for c in rows)
    if not chapters:
        raise JobFailed(NO_CHAPTER_NOTES)

    fake = summary_implementation() == "fake"
    llm: LLMClient = (
        FakeLLMClient() if fake else make_llm_client(ctx["session_factory"], book.id, job.id)
    )
    model = get_settings().llm_model_strong
    try:
        async with asyncio.timeout(SUMMARY_TIMEOUT_SECONDS):
            draft = await _call_summary(
                ctx,
                job,
                get_write_book_summary(),
                book.title,
                chapters,
                SummaryLength(summary.length),
                Language(summary.language),
                llm,
                model,
            )
    except TimeoutError as error:
        logger.error("Job %s: the summary took longer than %ss", job.id, SUMMARY_TIMEOUT_SECONDS)
        raise JobFailed(SUMMARY_TIMEOUT, SUMMARY_TIMEOUT_MESSAGE) from error

    # Locked and read again: a newer job may have finished or taken over while the model wrote.
    # The lock keeps two jobs from storing the summary at the same moment.
    await session.refresh(summary, with_for_update=True)
    if await _is_superseded(session, summary, job):
        logger.info("Job %s: summary %s was finished by a newer job", job.id, summary.id)
        return
    await _save_summary(session, summary, draft, FAKE_MODEL if fake else model)


async def _call_summary(
    ctx: dict[str, Any],
    job: Job,
    write: WriteBookSummaryFn,
    title: str,
    chapters: tuple[ChapterForSummary, ...],
    length: SummaryLength,
    language: Language,
    llm: LLMClient,
    model: str,
) -> SummaryDraft:
    """A valid summary, with at most SUMMARY_ATTEMPTS calls, and every failure turned into what
    the job should do."""
    positions = {chapter.position for chapter in chapters}
    for attempt in range(1, SUMMARY_ATTEMPTS + 1):
        try:
            draft = await write(
                title, chapters, length=length, language=language, llm=llm, model=model
            )
            draft = drop_invalid_key_ideas(draft, positions)
            validate_summary(draft, positions, length)
            return draft
        except LLMError as error:
            _fail_on_llm_error(ctx, job, error, "summary")
            raise
        except PipelineContractViolation as error:
            logger.warning("Job %s: summary attempt %s is invalid: %s", job.id, attempt, error)
            if attempt == SUMMARY_ATTEMPTS:
                raise JobFailed(SUMMARY_INVALID, SUMMARY_INVALID_MESSAGE) from error
        except Exception as error:
            logger.exception("Job %s: the reduce step raised an unexpected error", job.id)
            raise JobFailed(SUMMARY_INTERNAL_ERROR) from error
    raise AssertionError("unreachable")  # the last attempt returns or raises


async def _save_summary(
    session: AsyncSession, summary: Summary, draft: SummaryDraft, model: str
) -> None:
    """Store the summary with its chapter summaries and key ideas. Not committed here: run_job
    commits it together with the job becoming "done", so a summary is never half stored."""
    # Nothing should be there, but a summary must never show two sets of rows.
    await session.execute(delete(ChapterSummary).where(ChapterSummary.summary_id == summary.id))
    await session.execute(delete(KeyIdea).where(KeyIdea.summary_id == summary.id))
    rows = await session.execute(
        select(Chapter.position, Chapter.id).where(Chapter.book_id == summary.book_id)
    )
    chapter_ids = {position: chapter_id for position, chapter_id in rows}
    session.add_all(
        ChapterSummary(
            book_id=summary.book_id,
            summary_id=summary.id,
            chapter_id=chapter_ids[chapter.chapter_position],
            content=chapter.content.strip(),
        )
        for chapter in draft.chapters
    )
    session.add_all(
        KeyIdea(
            book_id=summary.book_id,
            summary_id=summary.id,
            chapter_id=chapter_ids[idea.chapter_position],
            position=position,
            text=idea.text.strip(),
        )
        for position, idea in enumerate(draft.key_ideas, start=1)
    )
    summary.overview = draft.overview.strip()
    summary.word_count = summary_word_count(draft)
    summary.model = model
    summary.status = SummaryStatus.READY


async def _fail_summary(session: AsyncSession, job: Job) -> None:
    """run_job's on_failure for generate_summary: the summary fails with its job, in the same
    transaction. A new POST for the same length and language starts it again. An old job that
    fails leaves alone a summary that a newer job has taken over."""
    summary_id = _summary_id(job)
    summary = await session.get(Summary, summary_id) if summary_id else None
    if summary is not None and not await _is_superseded(session, summary, job):
        summary.status = SummaryStatus.FAILED


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
            if job.type == JobType.GENERATE_SUMMARY:
                await _fail_summary(session, job)
            failed += 1
        await session.commit()
        return failed
