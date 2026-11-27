"""Pipeline contract (chunk_chapter and write_chapter_notes), version 1.0.

This file is shared between the two tracks:
  - Track A (LLM pipeline, Dino) produces the chunks of a chapter and the chapter notes.
  - Track B (worker, API, database, Daris) stores them.

Do not change this file without the agreement of both developers.
Every change bumps CONTRACT_VERSION and is described in docs/contracts/pipeline.md.
"""

from __future__ import annotations

import math
from dataclasses import dataclass
from typing import TYPE_CHECKING, Protocol

from app.parsing import Chapter, ChapterKind, count_words

if TYPE_CHECKING:
    # Only for type hints: app.llm comes with the LLM client (pull requests #13 and #19).
    from app.llm.base import LLMClient

CONTRACT_VERSION = "1.0"

PARAGRAPH_SEPARATOR = "\n\n"  # between the paragraphs of a chunk
TARGET_CHUNK_WORDS = 350  # FR-006
MAX_CHUNK_WORDS = 500  # FR-006
MAX_NOTES_WORDS = 500  # the notes should be about 150-300 words (FR-007)

# Input budget of one notes request, counted with estimate_tokens(). The worker passes the real
# value from its settings (NOTES_BUDGET_TOKENS); this is the default for those settings.
DEFAULT_NOTES_BUDGET_TOKENS = 6000

# Kinds of chapter that get notes. Back matter (notes, index, other works) does not: it adds cost
# and little to a summary of the book.
NOTES_KINDS = frozenset({ChapterKind.CHAPTER, ChapterKind.FRONT_MATTER})


def needs_notes(kind: ChapterKind) -> bool:
    return kind in NOTES_KINDS


def estimate_tokens(text: str) -> int:
    """About four characters per token. Used for budgets and display, never for billing (the
    cost comes from the token counts the API returns)."""
    return math.ceil(len(text) / 4)


# --------------------------------------------------------------------------- data


@dataclass(frozen=True, slots=True)
class ChunkDraft:
    position: int  # 1, 2, 3 ... inside the chapter
    text: str  # the chapter's paragraphs joined with PARAGRAPH_SEPARATOR
    word_count: int  # count_words(text)
    token_count: int  # estimate_tokens(text)
    page_start: int | None  # PDF page where the chunk starts; None for EPUB


@dataclass(frozen=True, slots=True)
class ChapterForNotes:
    position: int
    title: str
    kind: ChapterKind
    chunks: tuple[str, ...]  # the stored chunk texts, in order


class PipelineContractViolation(AssertionError):
    """Output of chunk_chapter() or write_chapter_notes() that breaks this contract (a bug)."""

    def __init__(self, problems: list[str]) -> None:
        self.problems = problems
        super().__init__("Output breaks the pipeline contract:\n  - " + "\n  - ".join(problems))


# --------------------------------------------------------------------------- functions


class ChunkChapterFn(Protocol):
    def __call__(self, chapter: Chapter) -> tuple[ChunkDraft, ...]: ...


class WriteChapterNotesFn(Protocol):
    """language is books.source_language; None means the language of the text. budget_tokens is
    the input budget of one LLM request (estimate_tokens); a longer chapter is split into sections
    of whole chunks."""

    async def __call__(
        self,
        chapter: ChapterForNotes,
        *,
        language: str | None,
        llm: LLMClient,
        model: str,
        budget_tokens: int,
    ) -> str: ...


# --------------------------------------------------------------------------- validation


def validate_chunks(chapter: Chapter, chunks: tuple[ChunkDraft, ...]) -> None:
    """Checks every rule of section 3 of the contract that can be checked; raises
    PipelineContractViolation with every problem found."""
    where = f"chapter {chapter.position}"
    problems: list[str] = []
    if not isinstance(chunks, tuple):
        problems.append(f"{where}: chunks must be a tuple")
    if not chunks:
        raise PipelineContractViolation([f"{where}: no chunks"])

    if [chunk.position for chunk in chunks] != list(range(1, len(chunks) + 1)):
        problems.append(f"{where}: positions must be 1..{len(chunks)}")

    for chunk in chunks:
        label = f"{where}, chunk {chunk.position}"
        words = count_words(chunk.text)
        if words == 0:
            problems.append(f"{label}: has no words")
        if words > MAX_CHUNK_WORDS:
            problems.append(f"{label}: {words} words, more than {MAX_CHUNK_WORDS}")
        if chunk.word_count != words:
            problems.append(f"{label}: word_count {chunk.word_count}, counted {words}")
        if chunk.token_count != estimate_tokens(chunk.text):
            problems.append(f"{label}: token_count is not estimate_tokens(text)")

    # Nothing lost, nothing changed: the same words in the same order.
    chunk_words = " ".join(chunk.text for chunk in chunks).split()
    chapter_words = " ".join(paragraph.text for paragraph in chapter.paragraphs).split()
    if chunk_words != chapter_words:
        problems.append(f"{where}: the words of the chunks differ from the chapter's paragraphs")

    # Paragraphs stay whole, except a paragraph longer than the maximum.
    whole = {paragraph.text for paragraph in chapter.paragraphs}
    too_long = [p.text for p in chapter.paragraphs if count_words(p.text) > MAX_CHUNK_WORDS]
    for chunk in chunks:
        for piece in chunk.text.split(PARAGRAPH_SEPARATOR):
            if piece not in whole and not any(piece in paragraph for paragraph in too_long):
                problems.append(f"{where}, chunk {chunk.position}: cuts a paragraph")
                break

    pages = [paragraph.page for paragraph in chapter.paragraphs]
    if all(page is None for page in pages):  # EPUB
        if any(chunk.page_start is not None for chunk in chunks):
            problems.append(f"{where}: page_start must be None for EPUB")
    else:  # PDF
        starts = [chunk.page_start for chunk in chunks]
        if any(start not in pages for start in starts):
            problems.append(f"{where}: every page_start must be the page of a paragraph")
        elif starts != sorted(starts):  # type: ignore[type-var]  # all ints here
            problems.append(f"{where}: page_start must never decrease")

    if problems:
        raise PipelineContractViolation(problems)


def validate_chapter_notes(notes: str) -> None:
    words = count_words(notes) if isinstance(notes, str) else 0
    if words == 0:
        raise PipelineContractViolation(["chapter notes are empty"])
    if words > MAX_NOTES_WORDS:
        raise PipelineContractViolation(
            [f"chapter notes have {words} words, more than {MAX_NOTES_WORDS}"]
        )
