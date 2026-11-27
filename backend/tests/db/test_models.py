"""The database schema enforces the rules of specification section 6.3, and the migrations match
the ORM models. Needs TEST_DATABASE_URL (see conftest.py)."""

import uuid
from decimal import Decimal
from hashlib import sha256
from typing import Any

import pytest
from alembic import command
from alembic.config import Config
from sqlalchemy import func, select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from app.db.models import (
    EMBEDDING_DIMENSIONS,
    Book,
    Chapter,
    ChapterSummary,
    Chunk,
    Citation,
    Job,
    KeyIdea,
    LlmUsage,
    Question,
    Summary,
    User,
)

pytestmark = pytest.mark.anyio


# Factories set the id themselves: the column default (uuid4) is applied only at flush time, and
# child rows need the parent's id before that.


def make_user() -> User:
    return User(
        id=uuid.uuid4(), email=f"{uuid.uuid4().hex}@example.com", password_hash="argon2id-hash"
    )


def make_book(owner: User, content: bytes = b"book") -> Book:
    return Book(
        id=uuid.uuid4(),
        owner_id=owner.id,
        title="Meditations",
        file_format="epub",
        file_sha256=sha256(content).hexdigest(),
        file_path="storage/meditations.epub",
        file_size_bytes=len(content),
        status="uploaded",
    )


def make_chapter(book: Book, position: int = 1) -> Chapter:
    return Chapter(
        id=uuid.uuid4(),
        book_id=book.id,
        position=position,
        title=f"Book {position}",
        kind="chapter",
        word_count=10,
    )


def make_summary(book: Book, length: str = "5min", language: str = "en") -> Summary:
    return Summary(
        id=uuid.uuid4(), book_id=book.id, length=length, language=language, status="queued"
    )


def make_job(book: Book) -> Job:
    return Job(id=uuid.uuid4(), book_id=book.id, type="process_book", status="queued")


def make_usage(book: Book) -> LlmUsage:
    return LlmUsage(
        book_id=book.id,
        step="map",
        model="gemma3:12b",
        input_tokens=1000,
        output_tokens=200,
        cost_usd=Decimal("0.006"),
    )


async def save(session: AsyncSession, *rows: Any) -> None:
    """Insert rows level by level: the models have no relationship(), so the flush order of
    different tables is not derived from foreign keys."""
    for row in rows:
        session.add(row)
        await session.flush()


async def save_book(session: AsyncSession) -> tuple[User, Book]:
    user = make_user()
    book = make_book(user)
    await save(session, user, book)
    return user, book


async def count(session: AsyncSession, model: type[Any]) -> int:
    return await session.scalar(select(func.count()).select_from(model)) or 0


# --------------------------------------------------------------------------- migrations


def test_migrations_match_the_models(migrated_database: str, alembic_config: Config) -> None:
    # Fails when a model was changed without a migration (or the other way round).
    command.check(alembic_config)


def test_migrations_can_be_rolled_back_and_reapplied(
    migrated_database: str, alembic_config: Config
) -> None:
    command.downgrade(alembic_config, "base")
    command.upgrade(alembic_config, "head")


# --------------------------------------------------------------------------- happy path


async def test_a_processed_book_with_all_derived_rows_can_be_saved_and_deleted(
    session: AsyncSession,
) -> None:
    user, book = await save_book(session)
    chapter = make_chapter(book)
    await save(session, chapter)
    chunk = Chunk(
        book_id=book.id,
        chapter_id=chapter.id,
        position=1,
        text="You have power over your mind, not outside events.",
        token_count=12,
        embedding=[0.1] * EMBEDDING_DIMENSIONS,
    )
    summary = make_summary(book)
    question = Question(
        book_id=book.id,
        user_id=user.id,
        question="What do we control?",
        answer="Our mind [1].",
        language="en",
        answerable=True,
    )
    job = make_job(book)
    await save(session, chunk, summary, question, job)
    usage = make_usage(book)
    usage.job_id = job.id
    await save(
        session,
        ChapterSummary(
            book_id=book.id, summary_id=summary.id, chapter_id=chapter.id, content="Stoic notes."
        ),
        KeyIdea(
            book_id=book.id, summary_id=summary.id, chapter_id=chapter.id, position=1, text="Mind."
        ),
        Citation(
            book_id=book.id,
            question_id=question.id,
            chunk_id=chunk.id,
            position=1,
            quote="power over",
        ),
        usage,
    )

    # Server defaults from section 6.2.
    await session.refresh(book)
    await session.refresh(job)
    assert book.created_at is not None
    assert book.chapters_detected is True
    assert book.is_demo is False
    assert (job.progress, job.attempts) == (0, 0)

    # Deleting a book deletes every derived row (section 6.3), but not its owner.
    await session.delete(book)
    await session.flush()
    derived = (Chapter, Chunk, Summary, ChapterSummary, KeyIdea, Question, Citation, Job, LlmUsage)
    for model in derived:
        assert await count(session, model) == 0, model.__name__
    assert await count(session, User) == 1


# --------------------------------------------------------------------------- CHECK constraints


@pytest.mark.parametrize(
    ("row", "column", "value", "constraint"),
    [
        ("book", "status", "done", "ck_books_status_valid"),
        ("book", "file_format", "mobi", "ck_books_file_format_valid"),
        ("book", "file_size_bytes", 0, "ck_books_file_size_positive"),
        ("chapter", "kind", "appendix", "ck_chapters_kind_valid"),
        ("summary", "length", "10min", "ck_summaries_length_valid"),
        ("summary", "language", "de", "ck_summaries_language_valid"),
        ("summary", "status", "done", "ck_summaries_status_valid"),
        ("job", "type", "delete_book", "ck_jobs_type_valid"),
        ("job", "status", "ready", "ck_jobs_status_valid"),
        ("job", "stage", "uploading", "ck_jobs_stage_valid"),
        ("job", "progress", 101, "ck_jobs_progress_range"),
        ("job", "progress", -1, "ck_jobs_progress_range"),
        ("usage", "step", "rerank", "ck_llm_usage_step_valid"),
        ("usage", "input_tokens", -1, "ck_llm_usage_input_tokens_non_negative"),
        ("usage", "output_tokens", -1, "ck_llm_usage_output_tokens_non_negative"),
        ("usage", "cost_usd", Decimal("-0.01"), "ck_llm_usage_cost_usd_non_negative"),
    ],
)
async def test_invalid_values_are_rejected_by_the_database(
    session: AsyncSession, row: str, column: str, value: Any, constraint: str
) -> None:
    user = make_user()
    book = make_book(user)
    rows = {
        "book": book,
        "chapter": make_chapter(book),
        "summary": make_summary(book),
        "job": make_job(book),
        "usage": make_usage(book),
    }
    setattr(rows[row], column, value)

    with pytest.raises(IntegrityError, match=constraint):
        await save(session, user, *rows.values())


async def test_a_job_without_a_stage_is_valid(session: AsyncSession) -> None:
    _, book = await save_book(session)
    await save(session, make_job(book))  # stage is NULL until the worker starts


# --------------------------------------------------------------------------- uniqueness


async def test_email_must_be_stored_in_lower_case(session: AsyncSession) -> None:
    with pytest.raises(IntegrityError, match="ck_users_email_lower_case"):
        await save(session, User(email="Ana@Example.com", password_hash="hash"))


async def test_email_is_unique(session: AsyncSession) -> None:
    user = make_user()
    await save(session, user)

    with pytest.raises(IntegrityError, match="uq_users_email"):
        await save(session, User(email=user.email, password_hash="other"))


async def test_a_user_cannot_upload_the_same_file_twice(session: AsyncSession) -> None:
    user, _ = await save_book(session)

    with pytest.raises(IntegrityError, match="uq_books_owner_id_file_sha256"):
        await save(session, make_book(user))


async def test_two_users_can_upload_the_same_file(session: AsyncSession) -> None:
    await save_book(session)
    await save_book(session)


async def test_chapter_position_is_unique_within_a_book(session: AsyncSession) -> None:
    _, book = await save_book(session)
    await save(session, make_chapter(book, position=1))

    with pytest.raises(IntegrityError, match="uq_chapters_book_id_position"):
        await save(session, make_chapter(book, position=1))


async def test_chunk_position_is_unique_within_a_chapter(session: AsyncSession) -> None:
    _, book = await save_book(session)
    chapter = make_chapter(book)
    await save(session, chapter)

    def chunk() -> Chunk:
        return Chunk(book_id=book.id, chapter_id=chapter.id, position=1, text="x", token_count=1)

    await save(session, chunk())
    with pytest.raises(IntegrityError, match="uq_chunks_chapter_id_position"):
        await save(session, chunk())


async def test_only_one_summary_per_book_length_and_language(session: AsyncSession) -> None:
    _, book = await save_book(session)
    await save(session, make_summary(book, "5min", "en"), make_summary(book, "5min", "cnr"))

    with pytest.raises(IntegrityError, match="uq_summaries_book_id_length_language"):
        await save(session, make_summary(book, "5min", "en"))


# --------------------------------------------------------------------------- relations


async def save_book_with_content(session: AsyncSession) -> dict[str, Any]:
    """A book with one chapter, chunk, summary and question, for the same-book tests."""
    user, book = await save_book(session)
    chapter = make_chapter(book)
    await save(session, chapter)
    rows: dict[str, Any] = {
        "book": book,
        "chapter": chapter,
        "chunk": Chunk(
            id=uuid.uuid4(),
            book_id=book.id,
            chapter_id=chapter.id,
            position=1,
            text="x",
            token_count=1,
        ),
        "summary": make_summary(book),
        "question": Question(
            id=uuid.uuid4(),
            book_id=book.id,
            user_id=user.id,
            question="Why?",
            answer="Because.",
            language="en",
            answerable=True,
        ),
    }
    await save(session, rows["chunk"], rows["summary"], rows["question"])
    return rows


def chapter_summary(own: dict[str, Any], other: dict[str, Any], mixed: str) -> ChapterSummary:
    refs = {"summary": own["summary"], "chapter": own["chapter"]} | {mixed: other[mixed]}
    return ChapterSummary(
        book_id=own["book"].id,
        summary_id=refs["summary"].id,
        chapter_id=refs["chapter"].id,
        content="x",
    )


def key_idea(own: dict[str, Any], other: dict[str, Any], mixed: str) -> KeyIdea:
    refs = {"summary": own["summary"], "chapter": own["chapter"]} | {mixed: other[mixed]}
    return KeyIdea(
        book_id=own["book"].id,
        summary_id=refs["summary"].id,
        chapter_id=refs["chapter"].id,
        position=1,
        text="x",
    )


def citation(own: dict[str, Any], other: dict[str, Any], mixed: str) -> Citation:
    refs = {"question": own["question"], "chunk": own["chunk"]} | {mixed: other[mixed]}
    return Citation(
        book_id=own["book"].id,
        question_id=refs["question"].id,
        chunk_id=refs["chunk"].id,
        position=1,
        quote="x",
    )


@pytest.mark.parametrize(
    ("make_row", "mixed", "constraint"),
    [
        (chapter_summary, "chapter", "fk_chapter_summaries_chapter_id_book_id_chapters"),
        (chapter_summary, "summary", "fk_chapter_summaries_summary_id_book_id_summaries"),
        (key_idea, "chapter", "fk_key_ideas_chapter_id_book_id_chapters"),
        (key_idea, "summary", "fk_key_ideas_summary_id_book_id_summaries"),
        (citation, "chunk", "fk_citations_chunk_id_book_id_chunks"),
        (citation, "question", "fk_citations_question_id_book_id_questions"),
    ],
)
async def test_rows_cannot_link_two_different_books(
    session: AsyncSession, make_row: Any, mixed: str, constraint: str
) -> None:
    own = await save_book_with_content(session)
    other = await save_book_with_content(session)

    # Linking only to its own book works ...
    await save(session, make_row(own, own, mixed))
    # ... but taking one reference from another book is refused.
    with pytest.raises(IntegrityError, match=constraint):
        await save(session, make_row(own, other, mixed))


async def test_a_chunk_cannot_belong_to_a_chapter_of_another_book(session: AsyncSession) -> None:
    _, book = await save_book(session)
    _, other_book = await save_book(session)
    other_chapter = make_chapter(other_book)
    await save(session, other_chapter)

    with pytest.raises(IntegrityError, match="fk_chunks_chapter_id_book_id_chapters"):
        await save(
            session,
            Chunk(
                book_id=book.id, chapter_id=other_chapter.id, position=1, text="x", token_count=1
            ),
        )


async def test_usage_records_outlive_their_job(session: AsyncSession) -> None:
    _, book = await save_book(session)
    job = make_job(book)
    await save(session, job)
    usage = make_usage(book)
    usage.job_id = job.id
    await save(session, usage)

    await session.delete(job)
    await session.flush()
    await session.refresh(usage)

    assert usage.job_id is None
