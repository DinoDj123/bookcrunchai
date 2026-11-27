"""write_chapter_notes(): the real chapter notes (Track A). Skeleton until the implementation is
merged; set BOOKCRUNCH_NOTES=fake meanwhile. Rules: docs/contracts/pipeline.md, section 6."""

from __future__ import annotations

from typing import TYPE_CHECKING

from .contract import ChapterForNotes

if TYPE_CHECKING:
    from app.llm.base import LLMClient


async def write_chapter_notes(
    chapter: ChapterForNotes,
    *,
    language: str | None,
    llm: LLMClient,
    model: str,
    budget_tokens: int,
) -> str:
    raise NotImplementedError("the real notes are not merged yet; set BOOKCRUNCH_NOTES=fake")
