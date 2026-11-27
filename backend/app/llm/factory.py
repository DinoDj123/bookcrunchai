"""Builds the LLM client for one book and one job.

The SDK client (one HTTP connection pool) and the semaphore are created once per process and
shared, so LLM_MAX_CONCURRENCY limits the parallel requests of all jobs in a worker together.
"""

import asyncio
import uuid
from functools import lru_cache

import openai
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from app.core.config import get_settings
from app.llm.base import LLMClient
from app.llm.client import OpenAICompatibleLLMClient
from app.llm.pricing import get_prices

# Ollama and vLLM accept any key, but the SDK refuses to start without one.
NO_KEY = "not-needed"


@lru_cache
def _shared_semaphore() -> asyncio.Semaphore:
    return asyncio.Semaphore(get_settings().llm_max_concurrency)


@lru_cache
def _shared_sdk() -> openai.AsyncOpenAI:
    settings = get_settings()
    return openai.AsyncOpenAI(
        base_url=settings.llm_base_url,
        api_key=settings.llm_api_key or NO_KEY,
        timeout=settings.llm_timeout_seconds,
        max_retries=0,  # OpenAICompatibleLLMClient retries itself
    )


def make_llm_client(
    session_factory: async_sessionmaker[AsyncSession],
    book_id: uuid.UUID,
    job_id: uuid.UUID | None,
) -> LLMClient:
    """job_id is None for calls made outside the worker (questions, asked through the API)."""
    settings = get_settings()
    return OpenAICompatibleLLMClient(
        sdk=_shared_sdk(),
        session_factory=session_factory,
        book_id=book_id,
        job_id=job_id,
        prices=get_prices(),
        cost_limit_usd=settings.book_cost_limit_usd,
        semaphore=_shared_semaphore(),
    )
