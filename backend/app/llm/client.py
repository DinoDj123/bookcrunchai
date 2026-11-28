"""The real LLM client: open-weight models behind an OpenAI-compatible API (specification 5.3).

The mentors asked for open-source models instead of the Claude API (Appendix E). Almost every way
of serving them speaks the same Chat Completions API: Ollama on a developer's machine, vLLM, and
hosted providers such as Groq, OpenRouter or Together. So the client uses the OpenAI Python SDK
with a configurable base URL, and the provider and the models are configuration only.

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
import re
import uuid
from collections.abc import Awaitable, Callable
from decimal import Decimal
from typing import Any

import openai
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
# Reasoning models (Qwen 3, DeepSeek R1) served by some servers put their thinking in the answer
# text, before the answer, inside these tags.
THINKING = re.compile(r"^\s*<think>.*?</think>\s*", re.DOTALL)

logger = logging.getLogger(__name__)


class OpenAICompatibleLLMClient:
    def __init__(
        self,
        *,
        sdk: openai.AsyncOpenAI,
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
        # Checked before the call: a model without a price could not be accounted for. A model
        # that costs nothing (Ollama, a free tier) is listed with price 0.
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

        # Some servers leave out the usage; the call is then recorded with zero tokens.
        usage = response.usage
        input_tokens = usage.prompt_tokens if usage else 0
        output_tokens = usage.completion_tokens if usage else 0
        if usage is None:
            logger.warning("The LLM server returned no token usage for model %s", model)
        cost = cost_usd(price, input_tokens, output_tokens)
        await self._record_usage(step, model, input_tokens, output_tokens, cost)

        # Recorded first: a refused request may still have been billed.
        choice = response.choices[0] if response.choices else None
        if choice is None or choice.finish_reason == "content_filter" or choice.message.refusal:
            raise LLMError(
                LLMErrorCode.LLM_REQUEST_FAILED,
                "The model refused the request or returned no answer",
                retryable=False,
            )
        text = THINKING.sub("", choice.message.content or "", count=1)
        return LLMResult(
            text=text,
            model=model,
            input_tokens=input_tokens,
            output_tokens=output_tokens,
            cost_usd=cost,
            stop_reason=choice.finish_reason,
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
    ) -> Any:  # openai.types.chat.ChatCompletion
        for attempt in range(1, MAX_ATTEMPTS + 1):
            retry_after: float | None = None
            try:
                return await self._sdk.chat.completions.create(
                    model=model,
                    # max_tokens, not max_completion_tokens: it is the one every
                    # OpenAI-compatible server understands.
                    max_tokens=max_tokens,
                    messages=[
                        {"role": "system", "content": system},
                        {"role": "user", "content": user},
                    ],
                )
            except openai.RateLimitError as error:  # 429
                failure: Exception = error
                retry_after = _retry_after_seconds(error)
            except openai.APIStatusError as error:
                if error.status_code < 500:
                    # Bad key, unknown model, request too large: the same request fails again.
                    raise LLMError(
                        LLMErrorCode.LLM_REQUEST_FAILED,
                        f"The LLM server rejected the request ({error.status_code}): "
                        f"{error.message}",
                        retryable=False,
                    ) from error
                failure = error
            except openai.APIConnectionError as error:  # includes APITimeoutError
                failure = error

            if attempt == MAX_ATTEMPTS:
                raise LLMError(
                    LLMErrorCode.LLM_UNAVAILABLE,
                    f"The LLM server did not answer after {MAX_ATTEMPTS} attempts: {failure}",
                    retryable=True,
                ) from failure
            delay = BACKOFF_BASE_SECONDS * 2 ** (attempt - 1)
            if retry_after is not None:
                delay = max(delay, min(retry_after, MAX_RETRY_AFTER_SECONDS))
            logger.warning(
                "LLM call failed (attempt %s of %s), trying again in %s s: %s",
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


def _retry_after_seconds(error: openai.APIStatusError) -> float | None:
    try:
        return float(error.response.headers.get("retry-after", ""))
    except ValueError:
        return None
