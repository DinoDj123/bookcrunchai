"""process_book after parsing: chunking and chapter notes, how a retried job continues, and how LLM
failures are mapped. Uses the fakes of app.worker.pipeline_draft and the fake parser, never a paid
API. Needs TEST_DATABASE_URL (see tests/conftest.py)."""

import asyncio
from pathlib import Path
from typing import Any

import arq
import pytest
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.db.models import Book, Chapter, Chunk, Job
from app.llm.base import LLMClient, LLMError, LLMErrorCode
from app.worker import tasks
from app.worker.jobs import GENERIC_ERROR_MESSAGE, MAX_TRIES
from app.worker.pipeline_draft import ChapterForNotes, ChunkDraft, fake_write_chapter_notes
from app.worker.tasks import (
    COST_LIMIT_MESSAGE,
    LLM_UNAVAILABLE_MESSAGE,
    PROGRESS_CHUNKED,
    _notes_progress,
    process_book,
)

pytestmark = pytest.mark.anyio

# The fake parser's book: 1 Preface (front matter), 2-4 chapters, 5 Notes (back matter).
NOTES_POSITIONS = [1, 2, 3, 4]


async def chapters_of(session: AsyncSession, book: Book) -> list[Chapter]:
    rows = await session.scalars(
        select(Chapter).where(Chapter.book_id == book.id).order_by(Chapter.position)
    )
    chapters = list(rows)
    for chapter in chapters:
        await session.refresh(chapter)
    return chapters


async def chunks_of(session: AsyncSession, chapter: Chapter) -> list[Chunk]:
    rows = await session.scalars(
        select(Chunk).where(Chunk.chapter_id == chapter.id).order_by(Chunk.position)
    )
    return list(rows)


async def chunk_count(session: AsyncSession, book: Book) -> int:
    count = await session.scalar(
        select(func.count()).select_from(Chunk).where(Chunk.book_id == book.id)
    )
    return count or 0


def use_notes(monkeypatch: pytest.MonkeyPatch, write: Any) -> None:
    monkeypatch.setattr(tasks, "get_write_chapter_notes", lambda: write)


def failing_at(position: int, error: Exception) -> Any:
    """Notes that fail for one chapter, a moment after the others have their notes."""

    async def write(chapter: ChapterForNotes, **kwargs: Any) -> str:
        if chapter.position == position:
            await asyncio.sleep(0.05)
            raise error
        return await fake_write_chapter_notes(chapter, **kwargs)

    return write


# --------------------------------------------------------------------------- success


async def test_chunks_are_stored_for_every_chapter(
    ctx: dict[str, Any], session: AsyncSession, book: Book, queued_job: Job
) -> None:
    await process_book(ctx, str(queued_job.id))

    for chapter in await chapters_of(session, book):
        chunks = await chunks_of(session, chapter)
        assert [chunk.position for chunk in chunks] == list(range(1, len(chunks) + 1))
        assert sum(len(chunk.text.split()) for chunk in chunks) == chapter.word_count
        assert all(chunk.book_id == book.id for chunk in chunks)
        assert all(chunk.token_count > 0 for chunk in chunks)
        assert all(chunk.page_start is None for chunk in chunks)  # EPUB
        assert all(chunk.embedding is None for chunk in chunks)  # week 3


async def test_pdf_chunks_have_pages(
    ctx: dict[str, Any],
    session: AsyncSession,
    book: Book,
    queued_job: Job,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("FAKE_PARSER_FORMAT", "pdf")

    await process_book(ctx, str(queued_job.id))

    for chapter in await chapters_of(session, book):
        pages = [chunk.page_start for chunk in await chunks_of(session, chapter)]
        assert all(isinstance(page, int) and page >= 1 for page in pages)
        assert pages == sorted(pages)


async def test_notes_are_written_for_front_matter_and_chapters_only(
    ctx: dict[str, Any], session: AsyncSession, book: Book, queued_job: Job
) -> None:
    await process_book(ctx, str(queued_job.id))
    await session.refresh(queued_job)
    await session.refresh(book)

    chapters = await chapters_of(session, book)
    assert [c.position for c in chapters if c.notes] == NOTES_POSITIONS
    assert chapters[0].notes is not None and chapters[0].notes.startswith("Fake notes for")
    assert chapters[4].kind == "back_matter" and chapters[4].notes is None
    assert book.status == "ready"
    assert queued_job.status == "done"
    assert queued_job.stage == "chapter_notes"
    assert queued_job.progress == 100


async def test_notes_get_the_chunks_and_the_book_language(
    ctx: dict[str, Any],
    session: AsyncSession,
    book: Book,
    queued_job: Job,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    seen: dict[int, tuple[ChapterForNotes, str | None, str]] = {}

    async def write(chapter: ChapterForNotes, *, language: str | None, llm: LLMClient, model: str):
        seen[chapter.position] = (chapter, language, model)
        return "Notes."

    use_notes(monkeypatch, write)
    await process_book(ctx, str(queued_job.id))

    assert sorted(seen) == NOTES_POSITIONS
    chapter_2 = (await chapters_of(session, book))[1]
    item, language, model = seen[2]
    assert item.title == chapter_2.title
    assert item.chunks == tuple(chunk.text for chunk in await chunks_of(session, chapter_2))
    assert language == "en"
    assert model == tasks.get_settings().llm_model_fast


def test_progress_fills_after_chunking() -> None:
    assert _notes_progress(0, 4) == PROGRESS_CHUNKED
    assert _notes_progress(2, 4) == 60
    assert _notes_progress(4, 4) == 100
    assert _notes_progress(0, 0) == 100


# --------------------------------------------------------------------------- continuing a job


async def test_retried_job_continues_with_the_missing_notes(
    ctx: dict[str, Any],
    session: AsyncSession,
    book: Book,
    queued_job: Job,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    unavailable = LLMError(LLMErrorCode.LLM_UNAVAILABLE, "overloaded", retryable=True)
    use_notes(monkeypatch, failing_at(3, unavailable))

    with pytest.raises(arq.Retry):
        await process_book(ctx, str(queued_job.id))
    await session.refresh(queued_job)
    await session.refresh(book)

    assert queued_job.status == "running"
    assert book.status == "processing"
    assert [c.position for c in await chapters_of(session, book) if c.notes] == [1, 2, 4]
    chunk_ids = set(await session.scalars(select(Chunk.id).where(Chunk.book_id == book.id)))

    # The second try: no parsing, no chunking, only the chapter that has no notes.
    def no_parsing() -> Any:
        raise AssertionError("an already chunked book must not be parsed again")

    monkeypatch.setattr(tasks, "get_parse_book", no_parsing)
    written: list[int] = []

    async def write(chapter: ChapterForNotes, **_kwargs: Any) -> str:
        written.append(chapter.position)
        return "Notes of the second try."

    use_notes(monkeypatch, write)
    await process_book(ctx | {"job_try": 2}, str(queued_job.id))
    await session.refresh(queued_job)
    await session.refresh(book)

    assert written == [3]
    assert queued_job.status == "done"
    assert book.status == "ready"
    assert set(await session.scalars(select(Chunk.id).where(Chunk.book_id == book.id))) == (
        chunk_ids
    )
    assert [c.position for c in await chapters_of(session, book) if c.notes] == NOTES_POSITIONS


# --------------------------------------------------------------------------- failures


async def test_cost_limit_fails_the_book_and_keeps_its_chunks(
    ctx: dict[str, Any],
    session: AsyncSession,
    book: Book,
    queued_job: Job,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    limit = LLMError(LLMErrorCode.COST_LIMIT_EXCEEDED, "limit", retryable=False)
    use_notes(monkeypatch, failing_at(2, limit))

    await process_book(ctx, str(queued_job.id))
    await session.refresh(queued_job)
    await session.refresh(book)

    assert queued_job.status == "failed"
    assert queued_job.error_code == "COST_LIMIT_EXCEEDED"
    assert queued_job.error_message == COST_LIMIT_MESSAGE
    assert book.status == "failed"
    assert book.error_code == "COST_LIMIT_EXCEEDED"
    assert await chunk_count(session, book) > 0  # a retry continues from the notes


async def test_request_that_cannot_succeed_fails_at_once(
    ctx: dict[str, Any],
    session: AsyncSession,
    book: Book,
    queued_job: Job,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    refused = LLMError(LLMErrorCode.LLM_REQUEST_FAILED, "bad request", retryable=False)
    use_notes(monkeypatch, failing_at(2, refused))

    await process_book(ctx, str(queued_job.id))
    await session.refresh(queued_job)

    assert queued_job.status == "failed"
    assert queued_job.error_code == "LLM_REQUEST_FAILED"
    assert queued_job.error_message == GENERIC_ERROR_MESSAGE
    assert queued_job.attempts == 1


async def test_unavailable_llm_on_the_last_try_fails_with_its_own_message(
    ctx: dict[str, Any],
    session: AsyncSession,
    book: Book,
    queued_job: Job,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    unavailable = LLMError(LLMErrorCode.LLM_UNAVAILABLE, "overloaded", retryable=True)
    use_notes(monkeypatch, failing_at(2, unavailable))

    await process_book(ctx | {"job_try": MAX_TRIES}, str(queued_job.id))
    await session.refresh(queued_job)
    await session.refresh(book)

    assert queued_job.status == "failed"
    assert queued_job.error_code == "LLM_UNAVAILABLE"
    assert queued_job.error_message == LLM_UNAVAILABLE_MESSAGE
    assert book.status == "failed"


@pytest.mark.parametrize(
    ("notes", "code"),
    [("", "NOTES_CONTRACT_VIOLATION"), (RuntimeError("bug"), "NOTES_INTERNAL_ERROR")],
)
async def test_broken_notes_fail_at_once(
    notes: str | Exception,
    code: str,
    ctx: dict[str, Any],
    session: AsyncSession,
    book: Book,
    queued_job: Job,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    async def write(_chapter: ChapterForNotes, **_kwargs: Any) -> str:
        if isinstance(notes, Exception):
            raise notes
        return notes

    use_notes(monkeypatch, write)
    await process_book(ctx, str(queued_job.id))
    await session.refresh(queued_job)
    await session.refresh(book)

    assert queued_job.status == "failed"
    assert queued_job.error_code == code
    assert queued_job.error_message == GENERIC_ERROR_MESSAGE
    assert queued_job.attempts == 1
    assert book.status == "failed"


@pytest.mark.parametrize(
    ("chunker", "code"),
    [
        # Drops the last word of the chapter: text would be lost.
        (
            lambda chapter: (ChunkDraft(1, chapter.paragraphs[0].text, 1, 1, None),),
            "CHUNKER_CONTRACT_VIOLATION",
        ),
        (lambda _chapter: 1 / 0, "CHUNKER_INTERNAL_ERROR"),
    ],
)
async def test_broken_chunker_fails_at_once_and_stores_nothing(
    chunker: Any,
    code: str,
    ctx: dict[str, Any],
    session: AsyncSession,
    book: Book,
    queued_job: Job,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(tasks, "get_chunk_chapter", lambda: chunker)

    await process_book(ctx, str(queued_job.id))
    await session.refresh(queued_job)
    await session.refresh(book)

    assert queued_job.status == "failed"
    assert queued_job.error_code == code
    assert queued_job.error_message == GENERIC_ERROR_MESSAGE
    assert book.status == "failed"
    assert await chapters_of(session, book) == []
    assert await chunk_count(session, book) == 0


async def test_real_implementations_are_not_available_yet(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    monkeypatch.setenv("BOOKCRUNCH_CHUNKER", "real")
    with pytest.raises(ValueError, match="BOOKCRUNCH_CHUNKER must be 'fake'"):
        tasks.get_chunk_chapter()
