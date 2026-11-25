"""make_llm_client() and FakeLLMClient. No database, no network."""

import uuid
from decimal import Decimal
from typing import Any

import pytest

from app.core.config import get_settings
from app.db.models import UsageStep
from app.llm import FakeLLMClient, LLMError, LLMErrorCode, factory, make_llm_client
from app.llm.client import AnthropicLLMClient

pytestmark = pytest.mark.anyio


@pytest.fixture(autouse=True)
def fresh_settings(monkeypatch: pytest.MonkeyPatch) -> Any:
    """Settings, the SDK client and the semaphore are cached per process; start each test clean."""

    def clear() -> None:
        get_settings.cache_clear()
        factory._shared_sdk.cache_clear()
        factory._shared_semaphore.cache_clear()

    clear()
    yield
    clear()


def test_clients_of_all_jobs_share_one_semaphore(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("ANTHROPIC_API_KEY", "test-key")
    monkeypatch.setenv("LLM_MAX_CONCURRENCY", "3")
    monkeypatch.setenv("BOOK_COST_LIMIT_USD", "1.25")

    first = make_llm_client(None, uuid.uuid4(), uuid.uuid4())  # type: ignore[arg-type]
    second = make_llm_client(None, uuid.uuid4(), None)  # type: ignore[arg-type]

    assert isinstance(first, AnthropicLLMClient)
    assert isinstance(second, AnthropicLLMClient)
    assert first._semaphore is second._semaphore
    assert first._semaphore._value == 3
    assert first._cost_limit_usd == Decimal("1.25")
    assert first._sdk.max_retries == 0  # the client retries itself, not the SDK as well


def test_missing_api_key_is_a_clear_error(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("ANTHROPIC_API_KEY", "")

    with pytest.raises(LLMError) as raised:
        make_llm_client(None, uuid.uuid4(), None)  # type: ignore[arg-type]

    assert raised.value.code == LLMErrorCode.LLM_REQUEST_FAILED
    assert raised.value.retryable is False


async def test_fake_client_replays_its_script_and_remembers_calls() -> None:
    failure = LLMError(LLMErrorCode.LLM_UNAVAILABLE, "down", retryable=True)
    llm = FakeLLMClient(["first", failure])

    async def call(user: str) -> str:
        result = await llm.complete(
            step=UsageStep.MAP, model="m", system="s", user=user, max_tokens=10
        )
        return result.text

    assert await call("one") == "first"
    with pytest.raises(LLMError):
        await call("two")
    assert await call("three") == "fake answer"
    assert [made.user for made in llm.calls] == ["one", "two", "three"]
