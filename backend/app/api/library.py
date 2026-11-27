"""Reading books: the library, one book, its table of contents and its cost (FR-012, FR-013,
FR-015). Every endpoint for one book gets it through get_readable_book, so a user reads only
their own books and the demo books. Uploading and retrying are in app.api.books.
"""

import uuid
from datetime import datetime
from decimal import Decimal

from fastapi import APIRouter
from pydantic import BaseModel, ConfigDict
from sqlalchemy import func, or_, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.deps import CurrentUser, ReadableBook, SessionDep
from app.db.models import Book, BookStatus, Chapter, Job, JobStatus, JobType, LlmUsage

router = APIRouter(prefix="/books", tags=["books"])

ZERO_USD = Decimal("0.000000")


class BookListItem(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: uuid.UUID
    title: str
    author: str | None
    file_format: str
    status: str
    error_code: str | None
    word_count: int | None
    is_demo: bool
    created_at: datetime


class BookList(BaseModel):
    books: list[BookListItem]


class ActiveJob(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: uuid.UUID
    type: str
    status: str
    stage: str | None
    progress: int


class Usage(BaseModel):
    input_tokens: int
    output_tokens: int
    # Money is a decimal string in JSON ("0.032000"), never a float.
    cost_usd: Decimal


class BookOut(BaseModel):
    id: uuid.UUID
    title: str
    author: str | None
    source_language: str | None
    file_format: str
    status: str
    error_code: str | None
    # Why processing failed, in words for the user; None unless the status is "failed".
    error_message: str | None
    chapters_detected: bool
    chapter_count: int
    word_count: int | None
    is_demo: bool
    # The job to poll (GET /jobs/{id}): queued or running. None when nothing is being done.
    active_job: ActiveJob | None
    usage: Usage


class ChapterOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    position: int
    title: str
    kind: str
    word_count: int
    has_notes: bool


class ChapterList(BaseModel):
    chapters: list[ChapterOut]


class StepUsage(Usage):
    step: str


class UsageOut(BaseModel):
    steps: list[StepUsage]
    total: Usage


@router.get("")
async def list_books(user: CurrentUser, session: SessionDep) -> BookList:
    """The user's own books and the demo books, newest first. `is_demo` tells them apart."""
    books = await session.scalars(
        select(Book)
        .where(or_(Book.owner_id == user.id, Book.is_demo))
        .order_by(Book.created_at.desc(), Book.id)
    )
    return BookList(books=[BookListItem.model_validate(book) for book in books])


async def usage_by_step(session: AsyncSession, book_id: uuid.UUID) -> list[StepUsage]:
    rows = await session.execute(
        select(
            LlmUsage.step,
            func.sum(LlmUsage.input_tokens),
            func.sum(LlmUsage.output_tokens),
            func.sum(LlmUsage.cost_usd),
        )
        .where(LlmUsage.book_id == book_id)
        .group_by(LlmUsage.step)
        .order_by(LlmUsage.step)
    )
    return [
        StepUsage(step=step, input_tokens=input_tokens, output_tokens=output_tokens, cost_usd=cost)
        for step, input_tokens, output_tokens, cost in rows
    ]


def total_usage(steps: list[StepUsage]) -> Usage:
    return Usage(
        input_tokens=sum(step.input_tokens for step in steps),
        output_tokens=sum(step.output_tokens for step in steps),
        cost_usd=sum((step.cost_usd for step in steps), ZERO_USD),
    )


@router.get("/{book_id}")
async def get_book(book: ReadableBook, session: SessionDep) -> BookOut:
    chapter_count = await session.scalar(
        select(func.count()).select_from(Chapter).where(Chapter.book_id == book.id)
    )
    active_job = await session.scalar(
        select(Job)
        .where(Job.book_id == book.id, Job.status.in_([JobStatus.QUEUED, JobStatus.RUNNING]))
        .order_by(Job.created_at.desc())
        .limit(1)
    )
    error_message = None
    if book.status == BookStatus.FAILED:
        # The message lives on the job that failed; the book keeps only the code.
        error_message = await session.scalar(
            select(Job.error_message)
            .where(Job.book_id == book.id, Job.type == JobType.PROCESS_BOOK)
            .order_by(Job.created_at.desc())
            .limit(1)
        )
    return BookOut(
        id=book.id,
        title=book.title,
        author=book.author,
        source_language=book.source_language,
        file_format=book.file_format,
        status=book.status,
        error_code=book.error_code,
        error_message=error_message,
        chapters_detected=book.chapters_detected,
        chapter_count=chapter_count or 0,
        word_count=book.word_count,
        is_demo=book.is_demo,
        active_job=ActiveJob.model_validate(active_job) if active_job else None,
        usage=total_usage(await usage_by_step(session, book.id)),
    )


@router.get("/{book_id}/chapters")
async def list_chapters(book: ReadableBook, session: SessionDep) -> ChapterList:
    """The table of contents, in reading order. Empty until the book has been parsed."""
    rows = await session.execute(
        select(
            Chapter.position,
            Chapter.title,
            Chapter.kind,
            Chapter.word_count,
            Chapter.notes.is_not(None).label("has_notes"),  # the notes themselves are not sent
        )
        .where(Chapter.book_id == book.id)
        .order_by(Chapter.position)
    )
    return ChapterList(chapters=[ChapterOut.model_validate(row) for row in rows])


@router.get("/{book_id}/usage")
async def get_usage(book: ReadableBook, session: SessionDep) -> UsageOut:
    """Tokens and estimated cost of the book, by pipeline step and in total (FR-013). Only steps
    that made at least one call are listed."""
    steps = await usage_by_step(session, book.id)
    return UsageOut(steps=steps, total=total_usage(steps))
