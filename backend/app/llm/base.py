"""What the rest of the application sees of the LLM: one call, its result and one error type.

The pipeline (chapter notes, summaries, questions) depends only on this file. The real client is
app.llm.client.OpenAICompatibleLLMClient, built by app.llm.factory.make_llm_client(); tests use
app.llm.fake.FakeLLMClient.
"""

from dataclasses import dataclass
from decimal import Decimal
from enum import StrEnum
from typing import Protocol

from app.db.models import UsageStep


class LLMErrorCode(StrEnum):
    # The book has already cost as much as BOOK_COST_LIMIT_USD allows (FR-017).
    COST_LIMIT_EXCEEDED = "COST_LIMIT_EXCEEDED"
    # Rate limit, server error or timeout on every attempt. Worth trying again later.
    LLM_UNAVAILABLE = "LLM_UNAVAILABLE"
    # The request can never succeed as it is: rejected by the API (bad key, unknown model, invalid
    # request), refused by the model, or the application is not configured for it.
    LLM_REQUEST_FAILED = "LLM_REQUEST_FAILED"


class LLMError(Exception):
    """The only exception an LLMClient raises for a failed call.

    retryable tells the worker what to do: True means running the job again later may work,
    False means it will fail the same way and the job should fail now.
    """

    def __init__(self, code: LLMErrorCode, message: str, *, retryable: bool) -> None:
        super().__init__(message)
        self.code = code
        self.message = message
        self.retryable = retryable


@dataclass(frozen=True)
class LLMResult:
    text: str
    model: str
    input_tokens: int
    output_tokens: int
    cost_usd: Decimal
    # "stop" for a finished answer; "length" means the text was cut off at max_tokens.
    stop_reason: str | None


class LLMClient(Protocol):
    async def complete(
        self, *, step: UsageStep, model: str, system: str, user: str, max_tokens: int
    ) -> LLMResult:
        """Send one system prompt and one user message, return the model's text.

        step says what the call is for and is stored with its cost (llm_usage.step).
        Raises LLMError and nothing else for a failed call.
        """
        ...
