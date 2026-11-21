"""Worker tasks. PostgreSQL is the single source of truth for job status (specification 5.4):
a task gets only the job ID from the queue, reads its input from the database and writes status,
progress and results back to it.

Week 1: process_book runs the parsing stage only. Chunking, embedding and chapter notes are added
to it in later tasks.
"""

import asyncio
import uuid
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from sqlalchemy import delete
from sqlalchemy.ext.asyncio import AsyncSession

from app.db.models import Book, BookStatus, Chapter, Job, JobStage, JobStatus
from app.parsing import ParsedBook, get_parse_book, validate_parsed_book

MAX_TRIES = 3


async def process_book(ctx: dict[str, Any], job_id: str) -> None:
    """Parse the book of a process_book job and store its chapters.

    ctx comes from ARQ: session_factory and storage_dir are set in WorkerSettings.on_startup,
    job_try is the number of this attempt (1 on the first one).
    """
    async with ctx["session_factory"]() as session:
        job = await session.get(Job, uuid.UUID(job_id))
        # A job that is already done was delivered twice by the queue; running it again would only
        # repeat finished work.
        if job is None or job.status == JobStatus.DONE:
            return
        book = await session.get_one(Book, job.book_id)

        # Committed before the slow part, so the UI sees "running" while the book is parsed.
        job.status = JobStatus.RUNNING
        job.stage = JobStage.PARSING
        job.progress = 0
        job.attempts = ctx["job_try"]
        job.started_at = datetime.now(UTC)
        job.finished_at = None
        job.error_code = None
        job.error_message = None
        book.status = BookStatus.PROCESSING
        book.error_code = None
        await session.commit()

        # books.file_path is the stored file's name relative to the storage directory.
        file_path = Path(ctx["storage_dir"]) / book.file_path
        # The parser is synchronous and may take up to a minute (contract rule 16); in a thread it
        # does not block the worker's event loop.
        parsed = await asyncio.to_thread(get_parse_book(), file_path)
        validate_parsed_book(parsed)

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
