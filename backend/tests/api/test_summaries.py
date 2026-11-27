"""Book summaries through the API (FR-008, FR-009, FR-014, TC-012): a ready summary comes from the
database, anything else is queued as a generate_summary job. Needs TEST_DATABASE_URL."""

import uuid
from datetime import UTC, datetime, timedelta
from typing import Any

import pytest
from httpx import AsyncClient
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.db.models import Book, Chapter, ChapterSummary, Job, KeyIdea, Summary
from tests.api.conftest import FakeQueue, register
from tests.api.test_library import add_book, add_user
from tests.api.test_upload import EPUB, upload

pytestmark = pytest.mark.anyio

BOOKS = "/api/v1/books"
FIVE_CNR = {"length": "5min", "language": "cnr"}


def summaries_path(book: Book) -> str:
    return f"{BOOKS}/{book.id}/summaries"


async def count(session: AsyncSession, model: type[Any]) -> int:
    return await session.scalar(select(func.count()).select_from(model)) or 0


async def add_chapters(session: AsyncSession, book: Book, count_: int = 3) -> list[Chapter]:
    chapters = [
        Chapter(
            id=uuid.uuid4(),
            book_id=book.id,
            position=n,
            title=f"Chapter {n}",
            kind="chapter",
            word_count=100,
            notes=f"Notes {n}.",
        )
        for n in range(1, count_ + 1)
    ]
    session.add_all(chapters)
    await session.flush()
    return chapters


async def add_summary(
    session: AsyncSession, book: Book, status: str, length: str = "5min", language: str = "cnr"
) -> Summary:
    summary = Summary(
        id=uuid.uuid4(), book_id=book.id, length=length, language=language, status=status
    )
    session.add(summary)
    await session.flush()
    return summary


async def add_summary_job(
    session: AsyncSession, summary: Summary, status: str = "queued", **fields: Any
) -> Job:
    job = Job(
        id=uuid.uuid4(),
        book_id=summary.book_id,
        type="generate_summary",
        status=status,
        params={
            "summary_id": str(summary.id),
            "length": summary.length,
            "language": summary.language,
        },
        **fields,
    )
    session.add(job)
    await session.flush()
    return job


async def add_ready_summary(session: AsyncSession, book: Book) -> Summary:
    """A stored summary: chapters 3 and 1 summarised (stored out of order), two key ideas."""
    chapters = await add_chapters(session, book)
    summary = await add_summary(session, book, "ready")
    summary.overview = "The overview."
    summary.word_count = 1100
    summary.model = "gemma3:12b"
    for chapter in (chapters[2], chapters[0]):
        session.add(
            ChapterSummary(
                book_id=book.id,
                summary_id=summary.id,
                chapter_id=chapter.id,
                content=f"About {chapter.title}.",
            )
        )
    for position, chapter in ((2, chapters[0]), (1, chapters[2])):
        session.add(
            KeyIdea(
                book_id=book.id,
                summary_id=summary.id,
                chapter_id=chapter.id,
                position=position,
                text=f"Idea {position}.",
            )
        )
    await session.flush()
    return summary


async def summary_row(session: AsyncSession, book: Book) -> Summary | None:
    summary = await session.scalar(select(Summary).where(Summary.book_id == book.id))
    if summary is not None:
        await session.refresh(summary)
    return summary


async def summary_jobs(session: AsyncSession, book: Book) -> list[Job]:
    rows = await session.scalars(
        select(Job).where(Job.book_id == book.id, Job.type == "generate_summary")
    )
    return list(rows)


# --------------------------------------------------------------------------- POST


async def test_new_summary_is_queued(
    client: AsyncClient, registered: str, session: AsyncSession, queue: FakeQueue
) -> None:
    book = await add_book(session, registered)

    response = await client.post(summaries_path(book), json=FIVE_CNR)

    assert response.status_code == 202
    summary = await summary_row(session, book)
    jobs = await summary_jobs(session, book)
    assert summary is not None and len(jobs) == 1
    assert response.json() == {
        "summary": {"id": str(summary.id), "status": "queued"},
        "job": {"id": str(jobs[0].id), "status": "queued"},
    }
    assert (summary.length, summary.language) == ("5min", "cnr")
    assert jobs[0].params == {"summary_id": str(summary.id), "length": "5min", "language": "cnr"}
    assert queue.calls == [("generate_summary", (str(jobs[0].id),), {"_job_id": str(jobs[0].id)})]


async def test_summary_being_made_is_not_queued_twice(
    client: AsyncClient, registered: str, session: AsyncSession, queue: FakeQueue
) -> None:
    book = await add_book(session, registered)
    first = await client.post(summaries_path(book), json=FIVE_CNR)

    second = await client.post(summaries_path(book), json=FIVE_CNR)

    assert second.status_code == 202
    assert second.json() == first.json()
    assert await count(session, Summary) == 1
    assert len(await summary_jobs(session, book)) == 1
    # The waiting job is handed to the queue again, which ignores a job it already has.
    assert [call[1] for call in queue.calls] == [call[1] for call in queue.calls[:1]] * 2


async def test_running_summary_answers_with_its_job(
    client: AsyncClient, registered: str, session: AsyncSession, queue: FakeQueue
) -> None:
    book = await add_book(session, registered)
    summary = await add_summary(session, book, "running")
    job = await add_summary_job(session, summary, "running")

    response = await client.post(summaries_path(book), json=FIVE_CNR)

    assert response.status_code == 202
    assert response.json() == {
        "summary": {"id": str(summary.id), "status": "running"},
        "job": {"id": str(job.id), "status": "running"},
    }
    assert queue.calls == []


async def test_ready_summary_comes_from_the_cache(
    client: AsyncClient, registered: str, session: AsyncSession, queue: FakeQueue
) -> None:
    book = await add_book(session, registered)
    summary = await add_ready_summary(session, book)

    response = await client.post(summaries_path(book), json=FIVE_CNR)

    assert response.status_code == 200
    assert response.json() == {
        "id": str(summary.id),
        "status": "ready",
        "length": "5min",
        "language": "cnr",
        "overview": "The overview.",
        "chapters": [
            {"chapter_position": 1, "title": "Chapter 1", "content": "About Chapter 1."},
            {"chapter_position": 3, "title": "Chapter 3", "content": "About Chapter 3."},
        ],
        "key_ideas": [
            {"text": "Idea 1.", "chapter_position": 3},
            {"text": "Idea 2.", "chapter_position": 1},
        ],
        "word_count": 1100,
        "job": None,
    }
    assert queue.calls == []
    assert len(await summary_jobs(session, book)) == 0


async def test_other_length_or_language_is_a_separate_summary(
    client: AsyncClient, registered: str, session: AsyncSession
) -> None:
    book = await add_book(session, registered)
    await add_ready_summary(session, book)

    response = await client.post(summaries_path(book), json={"length": "15min", "language": "cnr"})
    assert response.status_code == 202
    response = await client.post(summaries_path(book), json={"length": "5min", "language": "en"})
    assert response.status_code == 202
    assert await count(session, Summary) == 3


async def test_failed_summary_is_queued_again_with_a_new_job(
    client: AsyncClient, registered: str, session: AsyncSession, queue: FakeQueue
) -> None:
    book = await add_book(session, registered)
    summary = await add_summary(session, book, "failed")
    failed_job = await add_summary_job(session, summary, "failed", error_code="SUMMARY_INVALID")

    response = await client.post(summaries_path(book), json=FIVE_CNR)

    assert response.status_code == 202
    new_job_id = response.json()["job"]["id"]
    assert new_job_id != str(failed_job.id)
    assert response.json()["summary"] == {"id": str(summary.id), "status": "queued"}
    assert (await summary_row(session, book)).status == "queued"  # type: ignore[union-attr]
    assert len(await summary_jobs(session, book)) == 2
    assert [call[1] for call in queue.calls] == [(new_job_id,)]


@pytest.mark.parametrize("status", ["uploaded", "processing", "failed"])
async def test_book_that_is_not_ready_cannot_be_summarised(
    status: str, client: AsyncClient, registered: str, session: AsyncSession
) -> None:
    book = await add_book(session, registered, status=status)

    response = await client.post(summaries_path(book), json=FIVE_CNR)

    assert response.status_code == 409
    assert response.json()["error"]["code"] == "BOOK_NOT_READY"
    assert await count(session, Summary) == 0


async def test_another_users_book_is_not_found(
    client: AsyncClient, registered: str, session: AsyncSession
) -> None:
    book = await add_book(session, await add_user(session))

    for response in (
        await client.post(summaries_path(book), json=FIVE_CNR),
        await client.get(summaries_path(book), params=FIVE_CNR),
    ):
        assert response.status_code == 404
        assert response.json()["error"]["code"] == "BOOK_NOT_FOUND"
    assert await count(session, Summary) == 0


async def test_demo_book_can_be_summarised_by_any_user(
    client: AsyncClient, registered: str, session: AsyncSession
) -> None:
    book = await add_book(session, await add_user(session), is_demo=True)

    response = await client.post(summaries_path(book), json=FIVE_CNR)

    assert response.status_code == 202
    # Every user polls the same job of the shared summary.
    job = await client.get(f"/api/v1/jobs/{response.json()['job']['id']}")
    assert job.status_code == 200


@pytest.mark.parametrize(
    "body",
    [{"length": "10min", "language": "cnr"}, {"length": "5min", "language": "de"}, {}],
)
async def test_invalid_request_is_refused(
    body: dict[str, str], client: AsyncClient, registered: str, session: AsyncSession
) -> None:
    book = await add_book(session, registered)

    response = await client.post(summaries_path(book), json=body)

    assert response.status_code == 422
    assert response.json()["error"]["code"] == "VALIDATION_ERROR"


async def test_queue_down_leaves_no_new_summary_behind(
    client: AsyncClient, registered: str, session: AsyncSession, queue: FakeQueue
) -> None:
    book = await add_book(session, registered)
    queue.fail = True

    response = await client.post(summaries_path(book), json=FIVE_CNR)

    assert response.status_code == 503
    assert response.json()["error"]["code"] == "QUEUE_UNAVAILABLE"
    assert await count(session, Summary) == 0
    assert await summary_jobs(session, book) == []

    # Once the queue is back, the same request works.
    queue.fail = False
    assert (await client.post(summaries_path(book), json=FIVE_CNR)).status_code == 202


async def test_queue_down_leaves_a_failed_summary_failed(
    client: AsyncClient, registered: str, session: AsyncSession, queue: FakeQueue
) -> None:
    book = await add_book(session, registered)
    summary = await add_summary(session, book, "failed")
    await add_summary_job(session, summary, "failed")
    queue.fail = True

    response = await client.post(summaries_path(book), json=FIVE_CNR)

    assert response.status_code == 503
    assert (await summary_row(session, book)).status == "failed"  # type: ignore[union-attr]
    assert len(await summary_jobs(session, book)) == 1


async def test_summaries_need_a_session(client: AsyncClient, session: AsyncSession) -> None:
    book = await add_book(session, await add_user(session))

    assert (await client.post(summaries_path(book), json=FIVE_CNR)).status_code == 401
    assert (await client.get(summaries_path(book), params=FIVE_CNR)).status_code == 401


# --------------------------------------------------------------------------- GET


async def test_get_ready_summary(
    client: AsyncClient, registered: str, session: AsyncSession
) -> None:
    book = await add_book(session, registered)
    await add_ready_summary(session, book)
    posted = await client.post(summaries_path(book), json=FIVE_CNR)

    response = await client.get(summaries_path(book), params=FIVE_CNR)

    assert response.status_code == 200
    assert response.json() == posted.json()


async def test_get_summary_being_made_shows_its_job(
    client: AsyncClient, registered: str, session: AsyncSession
) -> None:
    book = await add_book(session, registered)
    summary = await add_summary(session, book, "running")
    job = await add_summary_job(session, summary, "running", stage="reduce", progress=0)

    response = await client.get(summaries_path(book), params=FIVE_CNR)

    assert response.status_code == 200
    body = response.json()
    assert (body["status"], body["overview"], body["chapters"], body["key_ideas"]) == (
        "running",
        None,
        [],
        [],
    )
    assert body["job"] == {
        "id": str(job.id),
        "status": "running",
        "stage": "reduce",
        "progress": 0,
        "error_code": None,
        "error_message": None,
    }


async def test_get_failed_summary_shows_why(
    client: AsyncClient, registered: str, session: AsyncSession
) -> None:
    book = await add_book(session, registered)
    summary = await add_summary(session, book, "failed")
    # One transaction gives both rows the same created_at; the first try was an hour earlier.
    an_hour_ago = datetime.now(UTC) - timedelta(hours=1)
    await add_summary_job(
        session, summary, "failed", error_code="OLD", error_message="Old.", created_at=an_hour_ago
    )
    await add_summary_job(
        session, summary, "failed", error_code="SUMMARY_INVALID", error_message="Try again."
    )

    response = await client.get(summaries_path(book), params=FIVE_CNR)

    job = response.json()["job"]
    assert (job["error_code"], job["error_message"]) == ("SUMMARY_INVALID", "Try again.")


async def test_get_summary_never_requested_is_not_found(
    client: AsyncClient, registered: str, session: AsyncSession
) -> None:
    book = await add_book(session, registered)
    await add_ready_summary(session, book)  # 5min cnr

    response = await client.get(summaries_path(book), params={"length": "5min", "language": "en"})

    assert response.status_code == 404
    assert response.json()["error"]["code"] == "SUMMARY_NOT_FOUND"


@pytest.mark.parametrize("params", [{}, {"length": "5min"}, {"length": "1min", "language": "en"}])
async def test_get_needs_a_valid_length_and_language(
    params: dict[str, str], client: AsyncClient, registered: str, session: AsyncSession
) -> None:
    book = await add_book(session, registered)

    response = await client.get(summaries_path(book), params=params)

    assert response.status_code == 422


# --------------------------------------------------------------------------- other endpoints


async def test_summary_job_is_not_the_books_active_job(
    client: AsyncClient, registered: str, session: AsyncSession
) -> None:
    book = await add_book(session, registered)
    await client.post(summaries_path(book), json=FIVE_CNR)

    response = await client.get(f"{BOOKS}/{book.id}")

    assert response.json()["active_job"] is None


async def test_repeated_upload_answers_with_the_processing_job(
    client: AsyncClient, session: AsyncSession
) -> None:
    await register(client)
    first = await upload(client, "book.epub", EPUB)
    book = await session.get_one(Book, uuid.UUID(first.json()["book"]["id"]))
    book.status = "ready"
    await session.flush()
    await client.post(summaries_path(book), json=FIVE_CNR)

    second = await upload(client, "book.epub", EPUB)

    assert second.json()["job"]["id"] == first.json()["job"]["id"]
