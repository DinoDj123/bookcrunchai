"""generate_summary (FR-008, FR-009, FR-014): the reduce step on the chapter notes, its validation
with one more try, what is stored, and how failures end the job and the summary. Uses the fake
parser, chunker, notes and reduce step, never a paid API. Needs TEST_DATABASE_URL."""

import asyncio
import dataclasses
import uuid
from datetime import UTC, datetime, timedelta
from typing import Any

import arq
import pytest
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.db.models import Book, Chapter, ChapterSummary, Job, KeyIdea, Summary
from app.llm.base import LLMError, LLMErrorCode
from app.worker import tasks
from app.worker.jobs import GENERIC_ERROR_MESSAGE, MAX_TRIES
from app.worker.pipeline_draft import (
    KEY_IDEA_RANGE,
    TARGET_WORDS,
    ChapterForSummary,
    KeyIdeaDraft,
    SummaryDraft,
    fake_write_book_summary,
)
from app.worker.settings import WorkerSettings
from app.worker.tasks import (
    COST_LIMIT_MESSAGE,
    LLM_UNAVAILABLE_MESSAGE,
    STALE_AFTER_SECONDS,
    SUMMARY_INVALID_MESSAGE,
    SUMMARY_TIMEOUT_MESSAGE,
    fail_stale_jobs,
    generate_summary,
    process_book,
)

pytestmark = pytest.mark.anyio

# The fake parser's book: 1 Preface (front matter), 2-4 chapters, 5 Notes (back matter, no notes).
SUMMARY_POSITIONS = [1, 2, 3, 4]


@pytest.fixture
async def ready_book(
    ctx: dict[str, Any], session: AsyncSession, book: Book, queued_job: Job
) -> Book:
    """The fixture book after process_book: chapters with fake notes, status ready."""
    await process_book(ctx, str(queued_job.id))
    await session.refresh(book)
    assert book.status == "ready"
    return book


async def request_summary(
    session: AsyncSession, book: Book, length: str = "5min", language: str = "cnr"
) -> tuple[Summary, Job]:
    """What POST /books/{id}/summaries saves before it enqueues the job."""
    summary = Summary(
        id=uuid.uuid4(), book_id=book.id, length=length, language=language, status="queued"
    )
    session.add(summary)
    await session.flush()
    job = Job(
        id=uuid.uuid4(),
        book_id=book.id,
        type="generate_summary",
        status="queued",
        params={"summary_id": str(summary.id), "length": length, "language": language},
    )
    session.add(job)
    await session.flush()
    return summary, job


async def refresh(session: AsyncSession, *rows: Any) -> None:
    for row in rows:
        await session.refresh(row)


async def stored_chapter_positions(session: AsyncSession, summary: Summary) -> list[int]:
    rows = await session.scalars(
        select(Chapter.position)
        .join(ChapterSummary, ChapterSummary.chapter_id == Chapter.id)
        .where(ChapterSummary.summary_id == summary.id)
        .order_by(Chapter.position)
    )
    return list(rows)


async def stored_key_ideas(session: AsyncSession, summary: Summary) -> list[tuple[int, str, int]]:
    rows = await session.execute(
        select(KeyIdea.position, KeyIdea.text, Chapter.position)
        .join(Chapter, Chapter.id == KeyIdea.chapter_id)
        .where(KeyIdea.summary_id == summary.id)
        .order_by(KeyIdea.position)
    )
    return [tuple(row) for row in rows]  # type: ignore[misc]


def use_summary(monkeypatch: pytest.MonkeyPatch, write: Any) -> None:
    monkeypatch.setattr(tasks, "get_write_book_summary", lambda: write)


def returning(*results: SummaryDraft | Exception) -> tuple[Any, list[int]]:
    """A reduce step that returns (or raises) the given results in turn; also the call count."""
    calls: list[int] = []

    async def write(*_args: Any, **_kwargs: Any) -> SummaryDraft:
        calls.append(1)
        result = results[len(calls) - 1]
        if isinstance(result, Exception):
            raise result
        return result

    return write, calls


async def valid_draft(book: Book, length: str = "5min") -> SummaryDraft:
    chapters = tuple(
        ChapterForSummary(position, f"Chapter {position}", "notes words here")
        for position in SUMMARY_POSITIONS
    )
    return await fake_write_book_summary(
        book.title, chapters, length=length, language="en", llm=None, model="m"
    )  # type: ignore[arg-type]


# --------------------------------------------------------------------------- success


@pytest.mark.parametrize("length", ["5min", "15min"])
async def test_summary_is_stored(
    length: str, ctx: dict[str, Any], session: AsyncSession, ready_book: Book
) -> None:
    summary, job = await request_summary(session, ready_book, length=length)

    await generate_summary(ctx, str(job.id))
    await refresh(session, summary, job, ready_book)

    assert summary.status == "ready"
    assert summary.overview and "cnr" in summary.overview
    assert summary.word_count == TARGET_WORDS[length]  # type: ignore[index]
    assert summary.model == "fake"
    assert await stored_chapter_positions(session, summary) == SUMMARY_POSITIONS
    ideas = await stored_key_ideas(session, summary)
    assert [position for position, _, _ in ideas] == list(
        range(1, KEY_IDEA_RANGE[length][0] + 1)  # type: ignore[index]
    )
    assert {chapter for _, _, chapter in ideas} <= set(SUMMARY_POSITIONS)
    assert job.status == "done"
    assert job.stage == "reduce"
    assert job.progress == 100
    assert ready_book.status == "ready"


async def test_reduce_step_gets_the_chapter_notes_and_the_choices(
    ctx: dict[str, Any], session: AsyncSession, ready_book: Book, monkeypatch: pytest.MonkeyPatch
) -> None:
    seen: dict[str, Any] = {}

    async def write(title: str, chapters: tuple[ChapterForSummary, ...], **kwargs: Any):
        seen.update(kwargs, title=title, chapters=chapters)
        return await fake_write_book_summary(title, chapters, **kwargs)

    use_summary(monkeypatch, write)
    summary, job = await request_summary(session, ready_book, length="15min", language="en")
    await generate_summary(ctx, str(job.id))

    rows = await session.scalars(
        select(Chapter).where(Chapter.book_id == ready_book.id).order_by(Chapter.position)
    )
    with_notes = [chapter for chapter in rows if chapter.notes]
    assert seen["title"] == ready_book.title
    assert seen["chapters"] == tuple(
        ChapterForSummary(c.position, c.title, c.notes or "") for c in with_notes
    )
    assert seen["length"] == "15min"
    assert seen["language"] == "en"
    assert seen["model"] == tasks.get_settings().llm_model_strong


async def test_ready_summary_is_not_written_again(
    ctx: dict[str, Any], session: AsyncSession, ready_book: Book, monkeypatch: pytest.MonkeyPatch
) -> None:
    summary, job = await request_summary(session, ready_book)
    await generate_summary(ctx, str(job.id))
    await refresh(session, summary)
    first_overview = summary.overview

    # The same summary requested by a second job (a duplicate delivery, for example).
    write, calls = returning()
    use_summary(monkeypatch, write)
    second = Job(
        id=uuid.uuid4(),
        book_id=ready_book.id,
        type="generate_summary",
        status="queued",
        params=job.params,
    )
    session.add(second)
    await session.flush()
    await generate_summary(ctx, str(second.id))
    await refresh(session, summary, second)

    assert calls == []
    assert summary.overview == first_overview
    assert second.status == "done"


async def test_invalid_result_is_tried_once_more(
    ctx: dict[str, Any], session: AsyncSession, ready_book: Book, monkeypatch: pytest.MonkeyPatch
) -> None:
    good = await valid_draft(ready_book)
    write, calls = returning(dataclasses.replace(good, overview=""), good)
    use_summary(monkeypatch, write)
    summary, job = await request_summary(session, ready_book)

    await generate_summary(ctx, str(job.id))
    await refresh(session, summary, job)

    assert len(calls) == 2
    assert summary.status == "ready"
    assert job.status == "done"


async def test_invalid_result_twice_fails_the_summary_but_not_the_book(
    ctx: dict[str, Any], session: AsyncSession, ready_book: Book, monkeypatch: pytest.MonkeyPatch
) -> None:
    bad = dataclasses.replace(await valid_draft(ready_book), chapters=())
    write, calls = returning(bad, bad)
    use_summary(monkeypatch, write)
    summary, job = await request_summary(session, ready_book)

    await generate_summary(ctx, str(job.id))
    await refresh(session, summary, job, ready_book)

    assert len(calls) == 2
    assert job.status == "failed"
    assert job.error_code == "SUMMARY_INVALID"
    assert job.error_message == SUMMARY_INVALID_MESSAGE
    assert summary.status == "failed"
    assert summary.overview is None
    assert ready_book.status == "ready"


async def test_key_idea_of_an_unknown_chapter_is_dropped(
    ctx: dict[str, Any], session: AsyncSession, ready_book: Book, monkeypatch: pytest.MonkeyPatch
) -> None:
    good = await valid_draft(ready_book)
    # Chapter 5 exists but has no notes (back matter), chapter 99 does not exist.
    ideas = (KeyIdeaDraft("From chapter 5.", 5), KeyIdeaDraft("From nowhere.", 99))
    write, _ = returning(dataclasses.replace(good, key_ideas=ideas + good.key_ideas))
    use_summary(monkeypatch, write)
    summary, job = await request_summary(session, ready_book)

    await generate_summary(ctx, str(job.id))
    await refresh(session, summary)

    ideas_stored = await stored_key_ideas(session, summary)
    assert [text for _, text, _ in ideas_stored] == [idea.text for idea in good.key_ideas]
    assert [position for position, _, _ in ideas_stored] == list(range(1, len(good.key_ideas) + 1))


async def test_book_without_chapter_notes_fails(
    ctx: dict[str, Any], session: AsyncSession, ready_book: Book
) -> None:
    for chapter in await session.scalars(select(Chapter).where(Chapter.book_id == ready_book.id)):
        chapter.notes = None
    await session.flush()
    summary, job = await request_summary(session, ready_book)

    await generate_summary(ctx, str(job.id))
    await refresh(session, summary, job)

    assert job.status == "failed"
    assert job.error_code == "NO_CHAPTER_NOTES"
    assert summary.status == "failed"


# --------------------------------------------------------------------------- LLM failures


async def test_cost_limit_fails_at_once(
    ctx: dict[str, Any], session: AsyncSession, ready_book: Book, monkeypatch: pytest.MonkeyPatch
) -> None:
    write, calls = returning(LLMError(LLMErrorCode.COST_LIMIT_EXCEEDED, "limit", retryable=False))
    use_summary(monkeypatch, write)
    summary, job = await request_summary(session, ready_book)

    await generate_summary(ctx, str(job.id))
    await refresh(session, summary, job, ready_book)

    assert len(calls) == 1
    assert job.error_code == "COST_LIMIT_EXCEEDED"
    assert job.error_message == COST_LIMIT_MESSAGE
    assert summary.status == "failed"
    assert ready_book.status == "ready"


async def test_unavailable_llm_is_retried_by_the_queue(
    ctx: dict[str, Any], session: AsyncSession, ready_book: Book, monkeypatch: pytest.MonkeyPatch
) -> None:
    unavailable = LLMError(LLMErrorCode.LLM_UNAVAILABLE, "overloaded", retryable=True)
    write, _ = returning(unavailable)
    use_summary(monkeypatch, write)
    summary, job = await request_summary(session, ready_book)

    with pytest.raises(arq.Retry):
        await generate_summary(ctx, str(job.id))
    await refresh(session, summary, job)
    assert summary.status == "running"
    assert job.status == "running"

    # The next try succeeds.
    use_summary(monkeypatch, fake_write_book_summary)
    await generate_summary(ctx | {"job_try": 2}, str(job.id))
    await refresh(session, summary, job)
    assert summary.status == "ready"
    assert job.status == "done"


async def test_unavailable_llm_on_the_last_try_fails(
    ctx: dict[str, Any], session: AsyncSession, ready_book: Book, monkeypatch: pytest.MonkeyPatch
) -> None:
    write, _ = returning(LLMError(LLMErrorCode.LLM_UNAVAILABLE, "overloaded", retryable=True))
    use_summary(monkeypatch, write)
    summary, job = await request_summary(session, ready_book)

    await generate_summary(ctx | {"job_try": MAX_TRIES}, str(job.id))
    await refresh(session, summary, job)

    assert job.error_code == "LLM_UNAVAILABLE"
    assert job.error_message == LLM_UNAVAILABLE_MESSAGE
    assert summary.status == "failed"


async def test_bug_in_the_reduce_step_fails_at_once(
    ctx: dict[str, Any], session: AsyncSession, ready_book: Book, monkeypatch: pytest.MonkeyPatch
) -> None:
    write, calls = returning(RuntimeError("bug"))
    use_summary(monkeypatch, write)
    summary, job = await request_summary(session, ready_book)

    await generate_summary(ctx, str(job.id))
    await refresh(session, summary, job)

    assert len(calls) == 1
    assert job.error_code == "SUMMARY_INTERNAL_ERROR"
    assert job.error_message == GENERIC_ERROR_MESSAGE
    assert summary.status == "failed"


async def test_summary_that_takes_too_long_fails(
    ctx: dict[str, Any], session: AsyncSession, ready_book: Book, monkeypatch: pytest.MonkeyPatch
) -> None:
    async def slow(*_args: Any, **_kwargs: Any) -> SummaryDraft:
        await asyncio.sleep(5)
        raise AssertionError("not reached")

    monkeypatch.setattr(tasks, "SUMMARY_TIMEOUT_SECONDS", 0.05)
    use_summary(monkeypatch, slow)
    summary, job = await request_summary(session, ready_book)

    await generate_summary(ctx, str(job.id))
    await refresh(session, summary, job)

    assert job.error_code == "SUMMARY_TIMEOUT"
    assert job.error_message == SUMMARY_TIMEOUT_MESSAGE
    assert summary.status == "failed"


def test_summary_time_limit_is_below_the_job_timeout() -> None:
    assert tasks.SUMMARY_TIMEOUT_SECONDS < tasks.JOB_TIMEOUT_SECONDS


# --------------------------------------------------------------------------- old and new jobs


async def newer_job(session: AsyncSession, job: Job) -> Job:
    """A second job for the same summary, as a POST after the first job failed creates it."""
    await session.refresh(job)
    newer = Job(
        id=uuid.uuid4(),
        book_id=job.book_id,
        type="generate_summary",
        status="queued",
        params=job.params,
        created_at=job.created_at + timedelta(minutes=15),
    )
    session.add(newer)
    await session.flush()
    return newer


async def test_old_job_does_not_write_a_summary_a_newer_job_owns(
    ctx: dict[str, Any], session: AsyncSession, ready_book: Book, monkeypatch: pytest.MonkeyPatch
) -> None:
    summary, old = await request_summary(session, ready_book)
    await newer_job(session, old)
    write, calls = returning()
    use_summary(monkeypatch, write)

    await generate_summary(ctx, str(old.id))
    await refresh(session, summary, old)

    assert calls == []  # nothing paid
    assert summary.status == "queued"  # left to the newer job
    assert old.status == "done"


async def test_newer_job_arriving_during_the_call_wins(
    ctx: dict[str, Any], session: AsyncSession, ready_book: Book, monkeypatch: pytest.MonkeyPatch
) -> None:
    summary, old = await request_summary(session, ready_book)

    async def write(*args: Any, **kwargs: Any) -> SummaryDraft:
        await newer_job(session, old)  # the user asked again while the model was writing
        return await fake_write_book_summary(*args, **kwargs)

    use_summary(monkeypatch, write)
    await generate_summary(ctx, str(old.id))
    await refresh(session, summary)

    assert summary.status == "running"  # not stored by the old job
    assert await stored_chapter_positions(session, summary) == []


async def test_old_job_failing_leaves_the_newer_jobs_summary_alone(
    ctx: dict[str, Any], session: AsyncSession, ready_book: Book
) -> None:
    summary, old = await request_summary(session, ready_book)
    await newer_job(session, old)
    old.status = "running"
    old.started_at = datetime.now(UTC) - timedelta(seconds=STALE_AFTER_SECONDS + 60)
    await session.flush()

    assert await fail_stale_jobs(ctx) == 1
    await refresh(session, summary, old)

    assert old.status == "failed"
    assert summary.status == "queued"


# --------------------------------------------------------------------------- worker wiring


async def test_stale_summary_job_fails_its_summary_and_not_the_book(
    ctx: dict[str, Any], session: AsyncSession, ready_book: Book
) -> None:
    summary, job = await request_summary(session, ready_book)
    summary.status = "running"
    job.status = "running"
    job.started_at = datetime.now(UTC) - timedelta(seconds=STALE_AFTER_SECONDS + 60)
    await session.flush()

    assert await fail_stale_jobs(ctx) == 1
    await refresh(session, summary, job, ready_book)

    assert job.status == "failed"
    assert summary.status == "failed"
    assert ready_book.status == "ready"


def test_worker_runs_generate_summary() -> None:
    assert generate_summary in WorkerSettings.functions
