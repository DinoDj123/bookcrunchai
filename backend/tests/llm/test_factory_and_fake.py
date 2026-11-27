"""make_llm_client() and FakeLLMClient. No database, no network."""

import uuid
from decimal import Decimal
from typing import Any

import pytest

from app.core.config import get_settings
from app.db.models import UsageStep
from app.llm import FakeLLMClient, LLMError, LLMErrorCode, factory, make_llm_client
from app.llm.client import OpenAICompatibleLLMClient

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
    monkeypatch.setenv("LLM_BASE_URL", "http://llm.test/v1")
    monkeypatch.setenv("LLM_MAX_CONCURRENCY", "3")
    monkeypatch.setenv("BOOK_COST_LIMIT_USD", "1.25")

    first = make_llm_client(None, uuid.uuid4(), uuid.uuid4())  # type: ignore[arg-type]
    second = make_llm_client(None, uuid.uuid4(), None)  # type: ignore[arg-type]

    assert isinstance(first, OpenAICompatibleLLMClient)
    assert isinstance(second, OpenAICompatibleLLMClient)
    assert first._semaphore is second._semaphore
    assert first._semaphore._value == 3
    assert first._cost_limit_usd == Decimal("1.25")
    assert first._sdk.max_retries == 0  # the client retries itself, not the SDK as well
    assert str(first._sdk.base_url).rstrip("/") == "http://llm.test/v1"


def test_server_without_a_key_gets_a_placeholder(monkeypatch: pytest.MonkeyPatch) -> None:
    """Ollama needs no key, but the SDK will not start without one."""
    monkeypatch.setenv("LLM_API_KEY", "")

    client = make_llm_client(None, uuid.uuid4(), None)  # type: ignore[arg-type]

    assert client._sdk.api_key == factory.NO_KEY  # type: ignore[attr-defined]


def test_empty_server_address_means_ollama_on_this_machine(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """.env.example leaves LLM_BASE_URL empty."""
    monkeypatch.setenv("LLM_BASE_URL", "")

    assert get_settings().llm_base_url == "http://localhost:11434/v1"


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
