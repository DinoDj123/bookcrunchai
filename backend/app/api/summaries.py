"""Book summaries (FR-008, FR-009, FR-014): get a summary for a length and language, or have one
made.

A summary is a row of `summaries`, one per book, length and language (the cache key, unique in the
database). POST answers 200 with a ready summary and never makes it again; otherwise it queues a
generate_summary job (app.worker.tasks) and answers 202 with the summary and the job to poll
(GET /jobs/{id}). GET reads the summary in any state, with its latest job while it is not ready.

The book comes through get_readable_book: the user's own book or a demo book. A summary of a demo
book is shared by everyone who reads it, like the book.
"""

import logging
import uuid

from fastapi import APIRouter, Response
from pydantic import BaseModel, ConfigDict
from sqlalchemy import delete, select
from sqlalchemy.dialects.postgresql import insert
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.books import JobBrief, queue_unavailable
from app.api.deps import QueueDep, ReadableBook, SessionDep
from app.core.errors import AppError
from app.db.models import (
    BookStatus,
    Chapter,
    ChapterSummary,
    Job,
    JobStatus,
    JobType,
    KeyIdea,
    Language,
    Summary,
    SummaryLength,
    SummaryStatus,
)
from app.worker.queue import enqueue_job

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/books", tags=["summaries"])


class SummaryRequest(BaseModel):
    length: SummaryLength
    language: Language


class ChapterSummaryOut(BaseModel):
    chapter_position: int
    title: str
    content: str


class KeyIdeaOut(BaseModel):
    text: str
    chapter_position: int  # the chapter to open in the reader (FR-009)


class SummaryJob(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: uuid.UUID
    status: str
    stage: str | None
    progress: int
    error_code: str | None
    error_message: str | None


class SummaryOut(BaseModel):
    id: uuid.UUID
    status: str  # queued | running | ready | failed
    length: str
    language: str
    overview: str | None  # None until ready
    chapters: list[ChapterSummaryOut]  # in reading order; empty until ready
    key_ideas: list[KeyIdeaOut]  # in the order shown; empty until ready
    word_count: int | None
    # The latest job of a summary that is not ready: what to poll, or why it failed. None when
    # ready.
    job: SummaryJob | None


class SummaryBrief(BaseModel):
    id: uuid.UUID
    status: str


class SummaryQueued(BaseModel):
    summary: SummaryBrief
    job: JobBrief


def book_not_ready() -> AppError:
    return AppError(409, "BOOK_NOT_READY", "The book is still being processed.")


async def latest_job(session: AsyncSession, summary: Summary) -> Job | None:
    """The newest generate_summary job of this summary (a failed summary may have older ones)."""
    return await session.scalar(
        select(Job)
        .where(
            Job.book_id == summary.book_id,
            Job.type == JobType.GENERATE_SUMMARY,
            Job.params["summary_id"].astext == str(summary.id),
        )
        .order_by(Job.created_at.desc())
        .limit(1)
    )


async def summary_out(session: AsyncSession, summary: Summary) -> SummaryOut:
    chapters = await session.execute(
        select(Chapter.position, Chapter.title, ChapterSummary.content)
        .join(ChapterSummary, ChapterSummary.chapter_id == Chapter.id)
        .where(ChapterSummary.summary_id == summary.id)
        .order_by(Chapter.position)
    )
    key_ideas = await session.execute(
        select(KeyIdea.text, Chapter.position)
        .join(Chapter, Chapter.id == KeyIdea.chapter_id)
        .where(KeyIdea.summary_id == summary.id)
        .order_by(KeyIdea.position)
    )
    job = None if summary.status == SummaryStatus.READY else await latest_job(session, summary)
    return SummaryOut(
        id=summary.id,
        status=summary.status,
        length=summary.length,
        language=summary.language,
        overview=summary.overview,
        chapters=[
            ChapterSummaryOut(chapter_position=position, title=title, content=content)
            for position, title, content in chapters
        ],
        key_ideas=[
            KeyIdeaOut(text=text, chapter_position=position) for text, position in key_ideas
        ],
        word_count=summary.word_count,
        job=SummaryJob.model_validate(job) if job else None,
    )


def summary_job(summary: Summary) -> Job:
    return Job(
        id=uuid.uuid4(),
        book_id=summary.book_id,
        type=JobType.GENERATE_SUMMARY,
        status=JobStatus.QUEUED,
        params={
            "summary_id": str(summary.id),
            "length": summary.length,
            "language": summary.language,
        },
    )


def queued_answer(summary: Summary, job: Job) -> SummaryQueued:
    return SummaryQueued(
        summary=SummaryBrief(id=summary.id, status=summary.status),
        job=JobBrief(id=job.id, status=job.status),
    )


@router.post(
    "/{book_id}/summaries",
    status_code=202,
    responses={200: {"model": SummaryOut, "description": "The summary is ready (from the cache)"}},
)
async def request_summary(
    body: SummaryRequest,
    book: ReadableBook,
    response: Response,
    session: SessionDep,
    queue: QueueDep,
) -> SummaryOut | SummaryQueued:
    """Get the summary for a length and language, or have it made.

    200 with the summary when it is ready. 202 with the summary and its job when it is queued or
    being made; a failed summary is queued again with a new job. 409 BOOK_NOT_READY until the book
    has been processed; 503 QUEUE_UNAVAILABLE if the job cannot be queued now.
    """
    if book.status != BookStatus.READY:
        raise book_not_ready()

    # Created if missing, then locked. ON CONFLICT DO NOTHING makes a second request for the same
    # new summary wait for the first one's commit instead of failing on the unique key, so two
    # requests at once end with one summary and one job.
    created_id = await session.scalar(
        insert(Summary)
        .values(
            id=uuid.uuid4(),
            book_id=book.id,
            length=body.length,
            language=body.language,
            status=SummaryStatus.QUEUED,
        )
        .on_conflict_do_nothing(index_elements=["book_id", "length", "language"])
        .returning(Summary.id)
    )
    summary = await session.scalar(
        select(Summary)
        .where(
            Summary.book_id == book.id,
            Summary.length == body.length,
            Summary.language == body.language,
        )
        .with_for_update()
        .execution_options(populate_existing=True)
    )
    assert summary is not None  # inserted above or already there

    if summary.status == SummaryStatus.READY:
        await session.commit()  # only releases the lock
        response.status_code = 200
        return await summary_out(session, summary)

    job = None if created_id else await latest_job(session, summary)
    if job is not None and job.status in (JobStatus.QUEUED, JobStatus.RUNNING):
        await session.commit()
        if job.status == JobStatus.QUEUED:
            # Normally already in the queue, and this does nothing (same job ID). But if the API
            # stopped between saving the job and enqueuing it, nobody else would ever start it.
            try:
                await enqueue_job(queue, job)
            except Exception as error:
                logger.exception("Job %s could not be queued", job.id)
                raise queue_unavailable() from error
        return queued_answer(summary, job)

    # New, failed, or (after a lost enqueue) waiting without a job: a new job. A failed job is not
    # queued again under its own ID; it stays as the record of what went wrong.
    summary.status = SummaryStatus.QUEUED
    job = summary_job(summary)
    session.add(job)
    # Committed before enqueuing: the worker must find the job when it looks for it.
    await session.commit()
    try:
        await enqueue_job(queue, job)
    except Exception as error:
        logger.exception("Job %s could not be queued", job.id)
        # Nothing is left waiting for a job nobody will run: a new summary is removed, an old one
        # goes back to failed, so the same request can simply be repeated.
        await session.execute(delete(Job).where(Job.id == job.id))
        if created_id:
            await session.execute(delete(Summary).where(Summary.id == summary.id))
        else:
            summary.status = SummaryStatus.FAILED
        await session.commit()
        raise queue_unavailable() from error
    return queued_answer(summary, job)


@router.get("/{book_id}/summaries")
async def get_summary(
    book: ReadableBook, session: SessionDep, length: SummaryLength, language: Language
) -> SummaryOut:
    """The summary for a length and language, in whatever state it is. While it is not ready,
    `job` is its latest job: poll GET /jobs/{id}, or show `job.error_message` if it failed.
    404 SUMMARY_NOT_FOUND if it was never requested."""
    summary = await session.scalar(
        select(Summary).where(
            Summary.book_id == book.id, Summary.length == length, Summary.language == language
        )
    )
    if summary is None:
        raise AppError(404, "SUMMARY_NOT_FOUND", "No summary of this length and language yet.")
    return await summary_out(session, summary)
