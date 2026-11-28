"""Chapter notes on a real model, by hand: to read the notes and see tokens and time.

    cd backend
    python scripts/pipeline_notes_try.py                                  # P&P, chapters 3-5
    python scripts/pipeline_notes_try.py the-prince.pdf --chapters 2 7 --language cnr
    python scripts/pipeline_notes_try.py meditations.epub --chunker fake --budget 3000

The book is one of tests/fixtures/books/. The server and the model come from the settings
(LLM_BASE_URL, LLM_API_KEY, LLM_MODEL_FAST in .env; Ollama on this machine by default), the
same as the worker. Free with Ollama; a paid provider bills the calls. Never run by the tests or CI.

No database: the calls go through ScriptLLMClient below, which records the usage only in memory.
The worker's client (app.llm.client) writes llm_usage rows, which need a book in the database.
"""

from __future__ import annotations

import argparse
import asyncio
import sys
import time
from decimal import Decimal
from pathlib import Path

import openai

from app.core.config import get_settings
from app.db.models import UsageStep
from app.llm.base import LLMError, LLMErrorCode, LLMResult
from app.llm.factory import NO_KEY
from app.llm.pricing import cost_usd, get_prices
from app.parsing import count_words
from app.parsing.parser import parse_book
from app.pipeline import DEFAULT_NOTES_BUDGET_TOKENS, ChapterForNotes, needs_notes
from app.pipeline.fake import fake_chunk_chapter
from app.pipeline.notes import write_chapter_notes

BOOKS = Path(__file__).resolve().parents[1] / "tests" / "fixtures" / "books"


class ScriptLLMClient:
    """An LLMClient for this script only: one SDK call per request, no retries, no database.
    Each call is kept in self.calls as (step, input tokens, output tokens, seconds)."""

    def __init__(self, sdk: openai.AsyncOpenAI, concurrency: int) -> None:
        self._sdk = sdk
        self._semaphore = asyncio.Semaphore(concurrency)
        self.calls: list[tuple[UsageStep, int, int, float]] = []

    async def complete(
        self, *, step: UsageStep, model: str, system: str, user: str, max_tokens: int
    ) -> LLMResult:
        async with self._semaphore:
            started = time.perf_counter()
            try:
                response = await self._sdk.chat.completions.create(
                    model=model,
                    max_tokens=max_tokens,
                    messages=[
                        {"role": "system", "content": system},
                        {"role": "user", "content": user},
                    ],
                )
            except openai.APIError as error:
                raise LLMError(LLMErrorCode.LLM_UNAVAILABLE, str(error), retryable=True) from error
            seconds = time.perf_counter() - started

        choice = response.choices[0] if response.choices else None
        usage = response.usage
        input_tokens = usage.prompt_tokens if usage else 0
        output_tokens = usage.completion_tokens if usage else 0
        self.calls.append((step, input_tokens, output_tokens, seconds))
        price = get_prices().get(model)
        return LLMResult(
            text=(choice.message.content or "") if choice else "",
            model=model,
            input_tokens=input_tokens,
            output_tokens=output_tokens,
            cost_usd=cost_usd(price, input_tokens, output_tokens) if price else Decimal(0),
            stop_reason=choice.finish_reason if choice else None,
        )


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    parser.add_argument("book", nargs="?", default="pride-and-prejudice.epub")
    parser.add_argument("--chapters", type=int, nargs="+", default=[3, 4, 5], help="positions")
    parser.add_argument("--chunker", choices=["real", "fake"], default="real")
    parser.add_argument("--language", default=None, help="en, cnr; default: the book's language")
    parser.add_argument("--budget", type=int, default=DEFAULT_NOTES_BUDGET_TOKENS)
    parser.add_argument("--model", default=None, help="default: LLM_MODEL_FAST")
    return parser.parse_args()


async def main() -> int:
    args = parse_args()
    settings = get_settings()
    model = args.model or settings.llm_model_fast

    if args.chunker == "real":
        from app.pipeline.chunking import chunk_chapter
    else:
        chunk_chapter = fake_chunk_chapter

    book = parse_book(BOOKS / args.book)
    language = args.language or book.language
    by_position = {chapter.position: chapter for chapter in book.chapters}
    missing = [position for position in args.chapters if position not in by_position]
    if missing:
        print(f"{args.book} has chapters 1..{len(book.chapters)}, not {missing}")
        return 1

    sdk = openai.AsyncOpenAI(
        base_url=settings.llm_base_url,
        api_key=settings.llm_api_key or NO_KEY,
        timeout=settings.llm_timeout_seconds,
        max_retries=0,
    )
    llm = ScriptLLMClient(sdk, settings.llm_max_concurrency)
    print(f"server: {settings.llm_base_url}   model: {model}   chunker: {args.chunker}")
    print(f"book:   {book.title} ({args.book}), language {language}, budget {args.budget}")

    total_started = time.perf_counter()
    for position in args.chapters:
        chapter = by_position[position]
        chunks = tuple(chunk.text for chunk in chunk_chapter(chapter))
        words = sum(count_words(chunk) for chunk in chunks)
        print(
            f"\n=== {position}. {chapter.title} [{chapter.kind}], {words} words, "
            f"{len(chunks)} chunks"
        )
        if not needs_notes(chapter.kind):
            print("(no notes for this kind of chapter; written anyway for the try)")
        item = ChapterForNotes(position, chapter.title, chapter.kind, chunks)
        calls_before = len(llm.calls)
        started = time.perf_counter()
        try:
            notes = await write_chapter_notes(
                item, language=language, llm=llm, model=model, budget_tokens=args.budget
            )
        except LLMError as error:
            print(f"LLMError {error.code} (retryable={error.retryable}): {error.message}")
            continue
        seconds = time.perf_counter() - started
        calls = llm.calls[calls_before:]
        tokens_in = sum(call[1] for call in calls)
        tokens_out = sum(call[2] for call in calls)
        print(notes)
        print(
            f"--- {count_words(notes)} words; {len(calls)} calls; "
            f"{tokens_in} tokens in, {tokens_out} out; {seconds:.1f} s"
        )

    total_in = sum(call[1] for call in llm.calls)
    total_out = sum(call[2] for call in llm.calls)
    print(
        f"\nTotal: {len(llm.calls)} calls, {total_in} tokens in, {total_out} out, "
        f"{time.perf_counter() - total_started:.1f} s"
    )
    return 0


if __name__ == "__main__":
    sys.exit(asyncio.run(main()))
