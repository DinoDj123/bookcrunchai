"""Reading books (FR-012, FR-013, FR-015, TC-019): the library, one book, its chapters and its
cost. A user reads their own books and the demo books; another user's book does not exist."""

import uuid
from decimal import Decimal
from typing import Any

import pytest
from httpx import AsyncClient
from sqlalchemy.ext.asyncio import AsyncSession

from app.db.models import Book, Chapter, Job, LlmUsage, User
from tests.api.conftest import register

pytestmark = pytest.mark.anyio

BOOKS = "/api/v1/books"
# Every endpoint that reads one book; the same ownership rule must hold for all of them.
BOOK_PATHS = ["", "/chapters", "/usage"]


async def add_book(session: AsyncSession, owner_id: str | uuid.UUID, **fields: Any) -> Book:
    values: dict[str, Any] = {
        "id": uuid.uuid4(),
        "owner_id": uuid.UUID(str(owner_id)),
        "title": "A Book",
        "file_format": "epub",
        "file_sha256": uuid.uuid4().hex * 2,
        "file_path": "book.epub",
        "file_size_bytes": 10,
        "status": "ready",
    }
    book = Book(**(values | fields))
    session.add(book)
    await session.flush()
    return book


async def add_user(session: AsyncSession) -> uuid.UUID:
    user = User(id=uuid.uuid4(), email=f"{uuid.uuid4().hex}@example.com", password_hash="hash")
    session.add(user)
    await session.flush()
    return user.id


def add_usage(session: AsyncSession, book: Book, step: str, tokens: tuple[int, int], cost: str):
    session.add(
        LlmUsage(
            book_id=book.id,
            step=step,
            model="test-model",
            input_tokens=tokens[0],
            output_tokens=tokens[1],
            cost_usd=Decimal(cost),
        )
    )


# --- Library ---


async def test_library_lists_own_books_and_demo_books_but_not_other_users_books(
    client: AsyncClient, registered: str, session: AsyncSession
) -> None:
    other_user = await add_user(session)
    mine = await add_book(session, registered, title="Mine", author="Me", word_count=1200)
    demo = await add_book(session, other_user, title="Demo", is_demo=True)
    await add_book(session, other_user, title="Private")

    response = await client.get(BOOKS)

    assert response.status_code == 200
    books = {book["title"]: book for book in response.json()["books"]}
    assert set(books) == {"Mine", "Demo"}
    assert books["Demo"]["is_demo"] is True
    assert books["Mine"] == {
        "id": str(mine.id),
        "title": "Mine",
        "author": "Me",
        "file_format": "epub",
        "status": "ready",
        "error_code": None,
        "word_count": 1200,
        "is_demo": False,
        "created_at": books["Mine"]["created_at"],
    }
    assert books["Demo"]["id"] == str(demo.id)


async def test_library_of_a_new_user_is_empty(client: AsyncClient, registered: str) -> None:
    response = await client.get(BOOKS)

    assert response.json() == {"books": []}


async def test_library_needs_a_session(client: AsyncClient) -> None:
    response = await client.get(BOOKS)

    assert response.status_code == 401
    assert response.json()["error"]["code"] == "UNAUTHENTICATED"


# --- Ownership, for every endpoint that reads one book ---


@pytest.mark.parametrize("path", BOOK_PATHS)
async def test_book_of_another_user_looks_like_it_does_not_exist(
    client: AsyncClient, registered: str, session: AsyncSession, path: str
) -> None:
    private = await add_book(session, await add_user(session))

    response = await client.get(f"{BOOKS}/{private.id}{path}")

    assert response.status_code == 404
    assert response.json()["error"]["code"] == "BOOK_NOT_FOUND"


@pytest.mark.parametrize("path", BOOK_PATHS)
async def test_demo_book_of_another_user_can_be_read(
    client: AsyncClient, registered: str, session: AsyncSession, path: str
) -> None:
    demo = await add_book(session, await add_user(session), is_demo=True)

    response = await client.get(f"{BOOKS}/{demo.id}{path}")

    assert response.status_code == 200


@pytest.mark.parametrize("path", BOOK_PATHS)
async def test_unknown_book_is_not_found(client: AsyncClient, registered: str, path: str) -> None:
    response = await client.get(f"{BOOKS}/{uuid.uuid4()}{path}")

    assert response.status_code == 404
    assert response.json()["error"]["code"] == "BOOK_NOT_FOUND"


@pytest.mark.parametrize("path", BOOK_PATHS)
async def test_reading_a_book_needs_a_session(
    client: AsyncClient, session: AsyncSession, path: str
) -> None:
    demo = await add_book(session, await add_user(session), is_demo=True)

    response = await client.get(f"{BOOKS}/{demo.id}{path}")

    assert response.status_code == 401


async def test_demo_book_cannot_be_retried_by_a_reader(
    client: AsyncClient, registered: str, session: AsyncSession
) -> None:
    """Readable is not the same as changeable: demo books are read-only (specification 4.2)."""
    demo = await add_book(session, await add_user(session), is_demo=True, status="failed")

    response = await client.post(f"{BOOKS}/{demo.id}/retry")

    assert response.status_code == 404


async def test_user_sees_their_own_book_after_logging_in_again(
    client: AsyncClient, session: AsyncSession
) -> None:
    first = await register(client, "first@example.com")
    book = await add_book(session, first)
    await register(client, "second@example.com")  # the client is now the second user

    assert (await client.get(f"{BOOKS}/{book.id}")).status_code == 404


# --- One book ---


async def test_book_details_of_a_ready_book(
    client: AsyncClient, registered: str, session: AsyncSession
) -> None:
    book = await add_book(
        session, registered, title="Ready", author="Author", source_language="en", word_count=900
    )
    session.add_all(
        Chapter(book_id=book.id, position=n, title=f"Chapter {n}", kind="chapter", word_count=300)
        for n in (1, 2, 3)
    )
    session.add(Job(id=uuid.uuid4(), book_id=book.id, type="process_book", status="done"))
    add_usage(session, book, "map", (12_000, 800), "0.032000")
    add_usage(session, book, "reduce", (4_000, 1_500), "0.023000")
    await session.flush()

    response = await client.get(f"{BOOKS}/{book.id}")

    assert response.status_code == 200
    assert response.json() == {
        "id": str(book.id),
        "title": "Ready",
        "author": "Author",
        "source_language": "en",
        "file_format": "epub",
        "status": "ready",
        "error_code": None,
        "error_message": None,
        "chapters_detected": True,
        "chapter_count": 3,
        "word_count": 900,
        "is_demo": False,
        "active_job": None,  # the job is done
        "usage": {"input_tokens": 16_000, "output_tokens": 2_300, "cost_usd": "0.055000"},
    }


async def test_book_being_processed_has_an_active_job(
    client: AsyncClient, registered: str, session: AsyncSession
) -> None:
    book = await add_book(session, registered, status="processing")
    job = Job(
        id=uuid.uuid4(),
        book_id=book.id,
        type="process_book",
        status="running",
        stage="parsing",
        progress=5,
    )
    session.add(job)
    await session.flush()

    body = (await client.get(f"{BOOKS}/{book.id}")).json()

    assert body["active_job"] == {
        "id": str(job.id),
        "type": "process_book",
        "status": "running",
        "stage": "parsing",
        "progress": 5,
    }
    assert body["chapter_count"] == 0
    assert body["usage"] == {"input_tokens": 0, "output_tokens": 0, "cost_usd": "0.000000"}


async def test_failed_book_carries_the_message_of_its_failed_job(
    client: AsyncClient, registered: str, session: AsyncSession
) -> None:
    book = await add_book(session, registered, status="failed", error_code="NO_TEXT_LAYER")
    session.add(
        Job(
            id=uuid.uuid4(),
            book_id=book.id,
            type="process_book",
            status="failed",
            error_code="NO_TEXT_LAYER",
            error_message="This PDF has no text layer.",
        )
    )
    await session.flush()

    body = (await client.get(f"{BOOKS}/{book.id}")).json()

    assert (body["status"], body["error_code"]) == ("failed", "NO_TEXT_LAYER")
    assert body["error_message"] == "This PDF has no text layer."
    assert body["active_job"] is None


# --- Chapters ---


async def test_chapters_come_in_reading_order_with_has_notes(
    client: AsyncClient, registered: str, session: AsyncSession
) -> None:
    book = await add_book(session, registered)
    session.add_all(
        [
            Chapter(book_id=book.id, position=2, title="One", kind="chapter", word_count=500),
            Chapter(
                book_id=book.id,
                position=1,
                title="Preface",
                kind="front_matter",
                word_count=80,
            ),
            Chapter(
                book_id=book.id,
                position=3,
                title="Two",
                kind="chapter",
                word_count=700,
                notes="Notes of chapter two.",
            ),
        ]
    )
    await session.flush()

    response = await client.get(f"{BOOKS}/{book.id}/chapters")

    assert response.status_code == 200
    assert response.json() == {
        "chapters": [
            {
                "position": 1,
                "title": "Preface",
                "kind": "front_matter",
                "word_count": 80,
                "has_notes": False,
            },
            {
                "position": 2,
                "title": "One",
                "kind": "chapter",
                "word_count": 500,
                "has_notes": False,
            },
            {
                "position": 3,
                "title": "Two",
                "kind": "chapter",
                "word_count": 700,
                "has_notes": True,
            },
        ]
    }


async def test_chapters_of_another_book_are_not_mixed_in(
    client: AsyncClient, registered: str, session: AsyncSession
) -> None:
    book = await add_book(session, registered)
    other = await add_book(session, registered)
    session.add(Chapter(book_id=other.id, position=1, title="X", kind="chapter", word_count=1))
    await session.flush()

    assert (await client.get(f"{BOOKS}/{book.id}/chapters")).json() == {"chapters": []}


# --- Usage ---


async def test_usage_by_step_and_in_total(
    client: AsyncClient, registered: str, session: AsyncSession
) -> None:
    book = await add_book(session, registered)
    other = await add_book(session, registered)
    add_usage(session, book, "map", (12_000, 800), "0.032000")
    add_usage(session, book, "map", (8_000, 600), "0.022000")
    add_usage(session, book, "reduce", (4_000, 1_500), "0.023000")
    add_usage(session, other, "map", (999, 999), "9.000000")  # another book: not counted
    await session.flush()

    response = await client.get(f"{BOOKS}/{book.id}/usage")

    assert response.status_code == 200
    assert response.json() == {
        "steps": [
            {"step": "map", "input_tokens": 20_000, "output_tokens": 1_400, "cost_usd": "0.054000"},
            {
                "step": "reduce",
                "input_tokens": 4_000,
                "output_tokens": 1_500,
                "cost_usd": "0.023000",
            },
        ],
        "total": {"input_tokens": 24_000, "output_tokens": 2_900, "cost_usd": "0.077000"},
    }


async def test_usage_of_a_book_without_calls_is_zero(
    client: AsyncClient, registered: str, session: AsyncSession
) -> None:
    book = await add_book(session, registered)

    assert (await client.get(f"{BOOKS}/{book.id}/usage")).json() == {
        "steps": [],
        "total": {"input_tokens": 0, "output_tokens": 0, "cost_usd": "0.000000"},
    }
