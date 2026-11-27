"""Fake chunker and fake chapter notes for Track B (worker, database, API, UI).

They follow every checked rule of the contract, so the worker can be built and tested before the
real implementations exist. The fake notes make no LLM call and cost nothing.

Environment variables for testing the worker:
    FAKE_NOTES_DELAY=1   -> waits 1 second per chapter, to see the chapter_notes stage in the UI
"""

from __future__ import annotations

import asyncio
import os
from typing import TYPE_CHECKING

from app.parsing import Chapter, count_words

from .contract import MAX_CHUNK_WORDS, ChapterForNotes, ChunkDraft, estimate_tokens

if TYPE_CHECKING:
    from app.llm.base import LLMClient


def _chunk(position: int, text: str, page: int | None) -> ChunkDraft:
    return ChunkDraft(
        position=position,
        text=text,
        word_count=count_words(text),
        token_count=estimate_tokens(text),
        page_start=page,
    )


def fake_chunk_chapter(chapter: Chapter) -> tuple[ChunkDraft, ...]:
    """One chunk per paragraph; a paragraph over the maximum is cut every 500 words. Follows the
    checked rules, so it works on real books too, but its chunks are smaller than the target of
    about 350 words and it does not look for sentence boundaries."""
    chunks: list[ChunkDraft] = []
    for paragraph in chapter.paragraphs:
        words = paragraph.text.split()  # normalised text: single spaces, so nothing is lost
        for start in range(0, len(words), MAX_CHUNK_WORDS):
            text = " ".join(words[start : start + MAX_CHUNK_WORDS])
            chunks.append(_chunk(len(chunks) + 1, text, paragraph.page))
    return tuple(chunks)


async def fake_write_chapter_notes(
    chapter: ChapterForNotes,
    *,
    language: str | None,
    llm: LLMClient,
    model: str,
    budget_tokens: int,
) -> str:
    """Fixed notes from the first words of the chapter. Makes no LLM call and costs nothing."""
    delay = float(os.getenv("FAKE_NOTES_DELAY", "0"))
    if delay > 0:
        await asyncio.sleep(delay)
    first_words = " ".join(" ".join(chapter.chunks).split()[:40])
    return f"Fake notes for “{chapter.title}”: {first_words} …"
