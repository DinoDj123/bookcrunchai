"""OpenAICompatibleLLMClient with the SDK replaced by a stand-in: usage and cost records (FR-013,
TC-018), the cost limit (FR-017) and retries (specification 4.5)."""

import asyncio
import uuid
from decimal import Decimal
from typing import Any

import pytest
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from app.db.models import Book, Job, LlmUsage, UsageStep
from app.llm import LLMError, LLMErrorCode
from app.llm.client import OpenAICompatibleLLMClient
from app.llm.pricing import ModelPrice
from tests.llm.conftest import FakeSdk, completion, status_error, timeout_error

pytestmark = pytest.mark.anyio

MODEL = "test-model"
PRICES = {MODEL: ModelPrice(Decimal("2.00"), Decimal("10.00"))}
# 1,000 input and 200 output tokens (the default of completion()) at the prices above.
COST_OF_DEFAULT_CALL = Decimal("0.004000")


class Sleeps:
    """Replaces asyncio.sleep: remembers how long the client wanted to wait, without waiting."""

    def __init__(self) -> None:
        self.delays: list[float] = []

    async def __call__(self, delay: float) -> None:
        self.delays.append(delay)


def make_client(
    sdk: FakeSdk,
    session_factory: async_sessionmaker[AsyncSession],
    book: Book,
    *,
    job_id: uuid.UUID | None = None,
    cost_limit: str = "2.00",
    sleep: Sleeps | None = None,
) -> OpenAICompatibleLLMClient:
    return OpenAICompatibleLLMClient(
        sdk=sdk,  # type: ignore[arg-type]
        session_factory=session_factory,
        book_id=book.id,
        job_id=job_id,
        prices=PRICES,
        cost_limit_usd=Decimal(cost_limit),
        semaphore=asyncio.Semaphore(5),
        sleep=sleep or Sleeps(),
    )


async def complete(client: OpenAICompatibleLLMClient, **overrides: Any) -> Any:
    arguments: dict[str, Any] = {
        "step": UsageStep.MAP,
        "model": MODEL,
        "system": "Summarise the chapter.",
        "user": "Chapter text",
        "max_tokens": 500,
    }
    return await client.complete(**{**arguments, **overrides})


async def usage_rows(session: AsyncSession, book: Book) -> list[LlmUsage]:
    rows = await session.scalars(select(LlmUsage).where(LlmUsage.book_id == book.id))
    return list(rows)


# --- Usage and cost ---


async def test_successful_call_returns_the_text_and_records_one_usage_row(
    session: AsyncSession, session_factory: async_sessionmaker[AsyncSession], book: Book
) -> None:
    job = Job(id=uuid.uuid4(), book_id=book.id, type="process_book", status="running")
    session.add(job)
    await session.flush()
    sdk = FakeSdk([completion("chapter notes", input_tokens=12_000, output_tokens=800)])

    result = await complete(make_client(sdk, session_factory, book, job_id=job.id))

    assert result.text == "chapter notes"
    assert (result.input_tokens, result.output_tokens) == (12_000, 800)
    assert result.cost_usd == Decimal("0.032000")
    assert result.stop_reason == "stop"
    [row] = await usage_rows(session, book)
    assert (row.job_id, row.step, row.model) == (job.id, "map", MODEL)
    assert (row.input_tokens, row.output_tokens) == (12_000, 800)
    assert row.cost_usd == Decimal("0.032000")


async def test_request_carries_the_prompts_model_and_max_tokens(
    session_factory: async_sessionmaker[AsyncSession], book: Book
) -> None:
    sdk = FakeSdk([completion()])

    await complete(make_client(sdk, session_factory, book))

    assert sdk.requests == [
        {
            "model": MODEL,
            "max_tokens": 500,
            "messages": [
                {"role": "system", "content": "Summarise the chapter."},
                {"role": "user", "content": "Chapter text"},
            ],
        }
    ]


async def test_thinking_before_the_answer_is_removed(
    session_factory: async_sessionmaker[AsyncSession], book: Book
) -> None:
    """Reasoning models on some servers put their thinking in the text; only the answer counts."""
    sdk = FakeSdk([completion("<think>\nThe chapter is about...\n</think>\n\nChapter notes.")])

    result = await complete(make_client(sdk, session_factory, book))

    assert result.text == "Chapter notes."


async def test_missing_usage_is_recorded_as_zero_tokens(
    session: AsyncSession, session_factory: async_sessionmaker[AsyncSession], book: Book
) -> None:
    sdk = FakeSdk([completion("notes", with_usage=False)])

    result = await complete(make_client(sdk, session_factory, book))

    assert (result.input_tokens, result.output_tokens, result.cost_usd) == (0, 0, Decimal(0))
    [row] = await usage_rows(session, book)
    assert (row.input_tokens, row.output_tokens) == (0, 0)


async def test_answer_cut_off_at_max_tokens_is_returned_with_its_reason(
    session_factory: async_sessionmaker[AsyncSession], book: Book
) -> None:
    sdk = FakeSdk([completion("half of the no", finish_reason="length")])

    result = await complete(make_client(sdk, session_factory, book))

    assert (result.text, result.stop_reason) == ("half of the no", "length")


async def test_book_total_is_the_sum_of_its_calls(
    session: AsyncSession, session_factory: async_sessionmaker[AsyncSession], book: Book
) -> None:
    """TC-018: correct cost per call and per book."""
    sdk = FakeSdk(
        [
            completion(input_tokens=12_000, output_tokens=800),  # 0.032
            completion(input_tokens=500_000, output_tokens=0),  # 1.000
            completion(input_tokens=0, output_tokens=3_300),  # 0.033
        ]
    )
    client = make_client(sdk, session_factory, book, cost_limit="100")

    for step in (UsageStep.MAP, UsageStep.MAP, UsageStep.REDUCE):
        await complete(client, step=step)

    rows = await usage_rows(session, book)
    assert sorted(row.cost_usd for row in rows) == [
        Decimal("0.032000"),
        Decimal("0.033000"),
        Decimal("1.000000"),
    ]
    assert sum(row.cost_usd for row in rows) == Decimal("1.065000")
    assert sorted(row.step for row in rows) == ["map", "map", "reduce"]


async def test_usage_stays_recorded_when_the_caller_fails_afterwards(
    session: AsyncSession, session_factory: async_sessionmaker[AsyncSession], book: Book
) -> None:
    """The usage row is committed by the client itself, not by the job's transaction."""
    sdk = FakeSdk([completion()])
    client = make_client(sdk, session_factory, book)

    async with session_factory() as job_session:
        await complete(client)
        await job_session.rollback()  # the job fails and gives up its own work

    assert len(await usage_rows(session, book)) == 1


async def test_model_without_a_price_is_refused_before_any_call(
    session: AsyncSession, session_factory: async_sessionmaker[AsyncSession], book: Book
) -> None:
    sdk = FakeSdk([completion()])

    with pytest.raises(LLMError) as raised:
        await complete(make_client(sdk, session_factory, book), model="unknown-model")

    assert raised.value.code == LLMErrorCode.LLM_REQUEST_FAILED
    assert raised.value.retryable is False
    assert sdk.requests == []
    assert await usage_rows(session, book) == []


@pytest.mark.parametrize(
    "refused",
    [
        completion(None, refusal="I can't help with that."),
        completion("", finish_reason="content_filter"),
    ],
)
async def test_refusal_is_recorded_and_then_reported_as_an_error(
    session: AsyncSession,
    session_factory: async_sessionmaker[AsyncSession],
    book: Book,
    refused: Any,
) -> None:
    sdk = FakeSdk([refused])

    with pytest.raises(LLMError) as raised:
        await complete(make_client(sdk, session_factory, book))

    assert raised.value.code == LLMErrorCode.LLM_REQUEST_FAILED
    assert raised.value.retryable is False
    assert len(await usage_rows(session, book)) == 1


# --- Cost limit ---


async def test_call_is_refused_when_the_book_has_reached_its_cost_limit(
    session: AsyncSession, session_factory: async_sessionmaker[AsyncSession], book: Book
) -> None:
    sdk = FakeSdk([completion(), completion()])
    # The limit equals the cost of one call: the first call is allowed, the second is not.
    client = make_client(sdk, session_factory, book, cost_limit=str(COST_OF_DEFAULT_CALL))

    await complete(client)
    with pytest.raises(LLMError) as raised:
        await complete(client)

    assert raised.value.code == LLMErrorCode.COST_LIMIT_EXCEEDED
    assert raised.value.retryable is False
    assert len(sdk.requests) == 1  # the second request was never sent
    assert len(await usage_rows(session, book)) == 1


async def test_cost_limit_counts_calls_of_earlier_jobs_of_the_same_book(
    session: AsyncSession, session_factory: async_sessionmaker[AsyncSession], book: Book
) -> None:
    session.add(
        LlmUsage(
            book_id=book.id,
            job_id=None,
            step="map",
            model=MODEL,
            input_tokens=1,
            output_tokens=1,
            cost_usd=Decimal("2.50"),
        )
    )
    await session.flush()
    sdk = FakeSdk([completion()])

    with pytest.raises(LLMError) as raised:
        await complete(make_client(sdk, session_factory, book, cost_limit="2.00"))

    assert raised.value.code == LLMErrorCode.COST_LIMIT_EXCEEDED
    assert sdk.requests == []


async def test_cost_of_another_book_does_not_count(
    session: AsyncSession, session_factory: async_sessionmaker[AsyncSession], book: Book
) -> None:
    other = Book(
        id=uuid.uuid4(),
        owner_id=book.owner_id,
        title="other",
        file_format="epub",
        file_sha256=uuid.uuid4().hex * 2,
        file_path="other.epub",
        file_size_bytes=1,
        status="processing",
    )
    session.add(other)
    await session.flush()
    session.add(
        LlmUsage(
            book_id=other.id,
            step="map",
            model=MODEL,
            input_tokens=1,
            output_tokens=1,
            cost_usd=Decimal("50"),
        )
    )
    await session.flush()
    sdk = FakeSdk([completion()])

    result = await complete(make_client(sdk, session_factory, book, cost_limit="2.00"))

    assert result.text == "notes"


# --- Retries ---


@pytest.mark.parametrize(
    "failure", [status_error(429), status_error(500), status_error(529), timeout_error()]
)
async def test_temporary_failure_is_retried_and_the_call_succeeds(
    session: AsyncSession,
    session_factory: async_sessionmaker[AsyncSession],
    book: Book,
    failure: Exception,
) -> None:
    sdk = FakeSdk([failure, failure, completion("third time lucky")])
    sleeps = Sleeps()

    result = await complete(make_client(sdk, session_factory, book, sleep=sleeps))

    assert result.text == "third time lucky"
    assert len(sdk.requests) == 3
    assert sleeps.delays == [2, 4]  # exponential back-off
    # Only the successful attempt has usage to record.
    assert len(await usage_rows(session, book)) == 1


async def test_gives_up_after_three_attempts_with_a_retryable_error(
    session: AsyncSession, session_factory: async_sessionmaker[AsyncSession], book: Book
) -> None:
    """TC-017, the client's part: three failures, then the job decides what to do."""
    sdk = FakeSdk([status_error(500), timeout_error(), status_error(429), completion("too late")])
    sleeps = Sleeps()

    with pytest.raises(LLMError) as raised:
        await complete(make_client(sdk, session_factory, book, sleep=sleeps))

    assert raised.value.code == LLMErrorCode.LLM_UNAVAILABLE
    assert raised.value.retryable is True
    assert len(sdk.requests) == 3
    assert sleeps.delays == [2, 4]  # no waiting after the last attempt
    assert await usage_rows(session, book) == []


@pytest.mark.parametrize("status", [400, 401, 404])
async def test_rejected_request_is_not_retried(
    session_factory: async_sessionmaker[AsyncSession], book: Book, status: int
) -> None:
    sdk = FakeSdk([status_error(status), completion()])
    sleeps = Sleeps()

    with pytest.raises(LLMError) as raised:
        await complete(make_client(sdk, session_factory, book, sleep=sleeps))

    assert raised.value.code == LLMErrorCode.LLM_REQUEST_FAILED
    assert raised.value.retryable is False
    assert len(sdk.requests) == 1
    assert sleeps.delays == []


async def test_rate_limit_waits_as_long_as_the_api_asks_up_to_a_cap(
    session_factory: async_sessionmaker[AsyncSession], book: Book
) -> None:
    sdk = FakeSdk(
        [status_error(429, retry_after="7"), status_error(429, retry_after="600"), completion()]
    )
    sleeps = Sleeps()

    await complete(make_client(sdk, session_factory, book, sleep=sleeps))

    assert sleeps.delays == [7, 30]


# --- Concurrency ---


class NoDatabase:
    """A session factory whose sessions do nothing. The tests share one database connection, which
    cannot run the sessions of overlapping calls; in the application each session has its own."""

    def __call__(self) -> "NoDatabase":
        return self

    async def __aenter__(self) -> "NoDatabase":
        return self

    async def __aexit__(self, *_error: object) -> None:
        return None

    async def scalar(self, _statement: object) -> Decimal:
        return Decimal(0)

    def add(self, _row: object) -> None:
        return None

    async def commit(self) -> None:
        return None


async def test_semaphore_limits_parallel_requests() -> None:
    running = 0
    most_at_once = 0

    class SlowSdk(FakeSdk):
        async def _create(self, **request: Any) -> Any:
            nonlocal running, most_at_once
            running += 1
            most_at_once = max(most_at_once, running)
            await asyncio.sleep(0.01)
            running -= 1
            return completion()

    client = OpenAICompatibleLLMClient(
        sdk=SlowSdk([]),  # type: ignore[arg-type]
        session_factory=NoDatabase(),  # type: ignore[arg-type]
        book_id=uuid.uuid4(),
        job_id=None,
        prices=PRICES,
        cost_limit_usd=Decimal("100"),
        semaphore=asyncio.Semaphore(2),
    )

    await asyncio.gather(*(complete(client) for _ in range(6)))

    assert most_at_once == 2
