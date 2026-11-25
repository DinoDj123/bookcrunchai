"""Fixtures for LLM client tests: a book to charge the calls to, and a stand-in for the Anthropic
SDK, so no test ever reaches the network or spends money."""

import uuid
from types import SimpleNamespace
from typing import Any

import anthropic
import httpx2
import pytest
from sqlalchemy.ext.asyncio import AsyncSession

from app.db.models import Book, User

REQUEST = httpx2.Request("POST", "https://api.anthropic.com/v1/messages")


def message(
    text: str = "notes", *, input_tokens: int = 1000, output_tokens: int = 200, **extra: Any
) -> SimpleNamespace:
    """The parts of the SDK's Message that the client reads."""
    return SimpleNamespace(
        content=[
            SimpleNamespace(type="thinking", thinking=""),
            SimpleNamespace(type="text", text=text),
        ],
        usage=SimpleNamespace(input_tokens=input_tokens, output_tokens=output_tokens),
        stop_reason=extra.get("stop_reason", "end_turn"),
    )


def status_error(status: int, *, retry_after: str | None = None) -> anthropic.APIStatusError:
    headers = {"retry-after": retry_after} if retry_after else {}
    response = httpx2.Response(status, headers=headers, request=REQUEST)
    error_class: type[anthropic.APIStatusError] = {
        400: anthropic.BadRequestError,
        401: anthropic.AuthenticationError,
        429: anthropic.RateLimitError,
    }.get(status, anthropic.InternalServerError)
    return error_class(f"status {status}", response=response, body=None)


def timeout_error() -> anthropic.APITimeoutError:
    return anthropic.APITimeoutError(request=REQUEST)


class FakeSdk:
    """Stands in for anthropic.AsyncAnthropic. Each messages.create() call takes the next item of
    `outcomes`: an exception is raised, anything else is returned."""

    def __init__(self, outcomes: list[Any]) -> None:
        self._outcomes = list(outcomes)
        self.requests: list[dict[str, Any]] = []
        self.messages = SimpleNamespace(create=self._create)

    async def _create(self, **request: Any) -> Any:
        self.requests.append(request)
        outcome = self._outcomes.pop(0)
        if isinstance(outcome, Exception):
            raise outcome
        return outcome


@pytest.fixture
async def book(session: AsyncSession) -> Book:
    user = User(id=uuid.uuid4(), email=f"{uuid.uuid4().hex}@example.com", password_hash="hash")
    uploaded = Book(
        id=uuid.uuid4(),
        owner_id=user.id,
        title="book",
        file_format="epub",
        file_sha256=uuid.uuid4().hex * 2,
        file_path="book.epub",
        file_size_bytes=1,
        status="processing",
    )
    session.add(user)
    await session.flush()
    session.add(uploaded)
    await session.flush()
    return uploaded
