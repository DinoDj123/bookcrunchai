"""The real LLM client: Claude through the async Anthropic SDK (specification 5.2).

One client object belongs to one book and one job. For every call it:
1. checks that the book has not reached its cost limit (FR-017),
2. calls the API, up to MAX_ATTEMPTS times on rate limits, server errors and timeouts
   (specification 4.5),
3. writes one llm_usage row in its own short transaction (FR-013), so the cost stays recorded even
   if the job fails later.

The SDK can retry by itself, but its retries are switched off (see factory.py) and done here, so
the number of attempts and the waiting are visible in one place and testable without a network.
"""

import asyncio
import logging
import uuid
from collections.abc import Awaitable, Callable
from decimal import Decimal
from typing import Any

import anthropic
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from app.db.models import LlmUsage, UsageStep
from app.llm.base import LLMError, LLMErrorCode, LLMResult
from app.llm.pricing import ModelPrice, cost_usd

MAX_ATTEMPTS = 3
# Wait 2 s after the first failed attempt and 4 s after the second.
BACKOFF_BASE_SECONDS: float = 2
# A 429 response may say how long to wait (Retry-After). Followed up to this many seconds.
MAX_RETRY_AFTER_SECONDS: float = 30

logger = logging.getLogger(__name__)


class AnthropicLLMClient:
    def __init__(
        self,
        *,
        sdk: anthropic.AsyncAnthropic,
        session_factory: async_sessionmaker[AsyncSession],
        book_id: uuid.UUID,
        job_id: uuid.UUID | None,
        prices: dict[str, ModelPrice],
        cost_limit_usd: Decimal,
        semaphore: asyncio.Semaphore,
        sleep: Callable[[float], Awaitable[None]] = asyncio.sleep,
    ) -> None:
        self._sdk = sdk
        self._session_factory = session_factory
        self._book_id = book_id
        self._job_id = job_id
        self._prices = prices
        self._cost_limit_usd = cost_limit_usd
        # Shared by every client in the process: limits parallel requests (LLM_MAX_CONCURRENCY).
        self._semaphore = semaphore
        self._sleep = sleep  # replaced in tests, so they do not really wait

    async def complete(
        self, *, step: UsageStep, model: str, system: str, user: str, max_tokens: int
    ) -> LLMResult:
        # Checked before any money is spent: a model without a price could not be accounted for.
        price = self._prices.get(model)
        if price is None:
            raise LLMError(
                LLMErrorCode.LLM_REQUEST_FAILED,
                f"Model {model!r} has no price in config/prices.json",
                retryable=False,
            )

        async with self._semaphore:
            # Inside the semaphore, so the check sees the cost of calls that finished while this
            # one waited. Calls already in flight are not counted yet: a book can pass the limit
            # by at most LLM_MAX_CONCURRENCY calls.
            await self._check_cost_limit()
            response = await self._call_with_retries(model, system, user, max_tokens)

        # Cache tokens are zero today (no prompt caching); counted as input if that changes.
        usage = response.usage
        input_tokens = (
            usage.input_tokens
            + (getattr(usage, "cache_creation_input_tokens", None) or 0)
            + (getattr(usage, "cache_read_input_tokens", None) or 0)
        )
        output_tokens = usage.output_tokens
        cost = cost_usd(price, input_tokens, output_tokens)
        await self._record_usage(step, model, input_tokens, output_tokens, cost)

        # Recorded first: a refused request may still have been billed.
        if response.stop_reason == "refusal":
            raise LLMError(
                LLMErrorCode.LLM_REQUEST_FAILED,
                "The model refused the request",
                retryable=False,
            )
        # Thinking blocks have no text; the answer is in the text blocks.
        text = "".join(block.text for block in response.content if block.type == "text")
        return LLMResult(
            text=text,
            model=model,
            input_tokens=input_tokens,
            output_tokens=output_tokens,
            cost_usd=cost,
            stop_reason=response.stop_reason,
        )

    async def _check_cost_limit(self) -> None:
        async with self._session_factory() as session:
            spent = await session.scalar(
                select(func.coalesce(func.sum(LlmUsage.cost_usd), 0)).where(
                    LlmUsage.book_id == self._book_id
                )
            )
        if spent >= self._cost_limit_usd:
            raise LLMError(
                LLMErrorCode.COST_LIMIT_EXCEEDED,
                f"Book {self._book_id} has cost {spent} USD, the limit is {self._cost_limit_usd}",
                retryable=False,
            )

    async def _call_with_retries(
        self, model: str, system: str, user: str, max_tokens: int
    ) -> Any:  # anthropic.types.Message
        for attempt in range(1, MAX_ATTEMPTS + 1):
            retry_after: float | None = None
            try:
                return await self._sdk.messages.create(
                    model=model,
                    max_tokens=max_tokens,
                    system=system,
                    messages=[{"role": "user", "content": user}],
                )
            except anthropic.RateLimitError as error:  # 429
                failure: Exception = error
                retry_after = _retry_after_seconds(error)
            except anthropic.APIStatusError as error:
                if error.status_code < 500:
                    # Bad key, unknown model, invalid request: the same request fails again.
                    raise LLMError(
                        LLMErrorCode.LLM_REQUEST_FAILED,
                        f"The Claude API rejected the request ({error.status_code}): "
                        f"{error.message}",
                        retryable=False,
                    ) from error
                failure = error
            except anthropic.APIConnectionError as error:  # includes APITimeoutError
                failure = error

            if attempt == MAX_ATTEMPTS:
                raise LLMError(
                    LLMErrorCode.LLM_UNAVAILABLE,
                    f"The Claude API did not answer after {MAX_ATTEMPTS} attempts: {failure}",
                    retryable=True,
                ) from failure
            delay = BACKOFF_BASE_SECONDS * 2 ** (attempt - 1)
            if retry_after is not None:
                delay = max(delay, min(retry_after, MAX_RETRY_AFTER_SECONDS))
            logger.warning(
                "Claude API call failed (attempt %s of %s), trying again in %s s: %s",
                attempt,
                MAX_ATTEMPTS,
                delay,
                failure,
            )
            await self._sleep(delay)
        raise AssertionError("unreachable")  # the loop always returns or raises

    async def _record_usage(
        self, step: UsageStep, model: str, input_tokens: int, output_tokens: int, cost: Decimal
    ) -> None:
        async with self._session_factory() as session:
            session.add(
                LlmUsage(
                    book_id=self._book_id,
                    job_id=self._job_id,
                    step=step.value,
                    model=model,
                    input_tokens=input_tokens,
                    output_tokens=output_tokens,
                    cost_usd=cost,
                )
            )
            await session.commit()


def _retry_after_seconds(error: anthropic.APIStatusError) -> float | None:
    try:
        return float(error.response.headers.get("retry-after", ""))
    except ValueError:
        return None
