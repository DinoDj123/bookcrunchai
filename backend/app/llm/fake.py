"""An LLMClient for tests: no network, no database, no cost.

    llm = FakeLLMClient(["notes for chapter 1", LLMError(...), "notes for chapter 3"])

Each call takes the next item: a string is returned as the model's text, an exception is raised.
When the list is empty or used up, default_text is returned. Every call is kept in llm.calls.
"""

from collections.abc import Iterable
from dataclasses import dataclass
from decimal import Decimal

from app.db.models import UsageStep
from app.llm.base import LLMResult


@dataclass(frozen=True)
class FakeLLMCall:
    step: UsageStep
    model: str
    system: str
    user: str
    max_tokens: int


class FakeLLMClient:
    def __init__(
        self, responses: Iterable[str | Exception] = (), *, default_text: str = "fake answer"
    ) -> None:
        self._responses = list(responses)
        self._default_text = default_text
        self.calls: list[FakeLLMCall] = []

    async def complete(
        self, *, step: UsageStep, model: str, system: str, user: str, max_tokens: int
    ) -> LLMResult:
        self.calls.append(FakeLLMCall(step, model, system, user, max_tokens))
        response = self._responses.pop(0) if self._responses else self._default_text
        if isinstance(response, Exception):
            raise response
        return LLMResult(
            text=response,
            model=model,
            input_tokens=0,
            output_tokens=0,
            cost_usd=Decimal(0),
            stop_reason="end_turn",
        )
