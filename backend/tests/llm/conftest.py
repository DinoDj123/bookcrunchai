"""Fixtures for LLM client tests: a book to charge the calls to, and a stand-in for the OpenAI SDK,
so no test ever reaches the network or spends money."""

import uuid
from types import SimpleNamespace
from typing import Any

import httpx2
import openai
import pytest
from sqlalchemy.ext.asyncio import AsyncSession

from app.db.models import Book, User

REQUEST = httpx2.Request("POST", "http://llm.test/v1/chat/completions")


def completion(
    text: str | None = "notes",
    *,
    input_tokens: int = 1000,
    output_tokens: int = 200,
    finish_reason: str = "stop",
    refusal: str | None = None,
    with_usage: bool = True,
) -> SimpleNamespace:
    """The parts of the SDK's ChatCompletion that the client reads."""
    usage = SimpleNamespace(prompt_tokens=input_tokens, completion_tokens=output_tokens)
    return SimpleNamespace(
        choices=[
            SimpleNamespace(
                message=SimpleNamespace(content=text, refusal=refusal),
                finish_reason=finish_reason,
            )
        ],
        usage=usage if with_usage else None,
    )


def status_error(status: int, *, retry_after: str | None = None) -> openai.APIStatusError:
    headers = {"retry-after": retry_after} if retry_after else {}
    response = httpx2.Response(status, headers=headers, request=REQUEST)
    error_class: type[openai.APIStatusError] = {
        400: openai.BadRequestError,
        401: openai.AuthenticationError,
        404: openai.NotFoundError,
        429: openai.RateLimitError,
    }.get(status, openai.InternalServerError)
    return error_class(f"status {status}", response=response, body=None)


def timeout_error() -> openai.APITimeoutError:
    return openai.APITimeoutError(request=REQUEST)


class FakeSdk:
    """Stands in for openai.AsyncOpenAI. Each chat.completions.create() call takes the next item
    of `outcomes`: an exception is raised, anything else is returned."""

    def __init__(self, outcomes: list[Any]) -> None:
        self._outcomes = list(outcomes)
        self.requests: list[dict[str, Any]] = []
        self.chat = SimpleNamespace(completions=SimpleNamespace(create=self._create))

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
