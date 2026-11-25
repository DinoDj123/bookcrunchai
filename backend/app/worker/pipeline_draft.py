"""TEMPORARY: chunking and chapter notes as proposed in the draft contract (chunk_chapter contract
0.1, not agreed with Track A yet), with fake implementations, so that the worker stages can be
built and tested before Track A's code exists.

When the contract is agreed, its shared part (ChunkDraft, ChapterForNotes, estimate_tokens, the
validators) moves to app.pipeline, owned by both tracks, and the real implementations come from
Track A. tasks.py then imports from app.pipeline instead of this module, and this file is
deleted. Nothing outside app.worker may import it.

Implementations are picked like the parser's (BOOKCRUNCH_PARSER):
    BOOKCRUNCH_CHUNKER=fake   one chunk per paragraph (default; "real" does not exist yet)
    BOOKCRUNCH_NOTES=fake     fixed notes without any LLM call (default; "real" does not exist yet)
    FAKE_NOTES_DELAY=1        waits 1 second per chapter, to see the stage in the UI
"""

import asyncio
import math
import os
from collections.abc import Awaitable, Callable
from dataclasses import dataclass

from app.llm.base import LLMClient
from app.parsing import Chapter, ChapterKind, count_words

MAX_CHUNK_WORDS = 500  # FR-006
MAX_NOTES_WORDS = 500  # the notes should be about 150-300 words (FR-007)
PARAGRAPH_SEPARATOR = "\n\n"


def estimate_tokens(text: str) -> int:
    """About four characters per token. Used for budgets and display, never for billing (the
    cost comes from the token counts the API returns)."""
    return math.ceil(len(text) / 4)


@dataclass(frozen=True, slots=True)
class ChunkDraft:
    position: int  # 1, 2, 3 ... inside the chapter
    text: str  # the chapter's paragraphs joined with PARAGRAPH_SEPARATOR
    word_count: int
    token_count: int
    page_start: int | None  # PDF page where the chunk starts; None for EPUB


@dataclass(frozen=True, slots=True)
class ChapterForNotes:
    position: int
    title: str
    kind: ChapterKind
    chunks: tuple[str, ...]  # the stored chunk texts, in order


class PipelineContractViolation(AssertionError):
    def __init__(self, problems: list[str]) -> None:
        self.problems = problems
        super().__init__("Output breaks the pipeline contract:\n  - " + "\n  - ".join(problems))


ChunkChapterFn = Callable[[Chapter], tuple[ChunkDraft, ...]]
WriteChapterNotesFn = Callable[..., Awaitable[str]]


# --------------------------------------------------------------------------- validation


def validate_chunks(chapter: Chapter, chunks: tuple[ChunkDraft, ...]) -> None:
    """Checks the rules of the draft contract that can be checked; raises
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
        raise PipelineContractViolation([f"chapter notes have {words} words"])


# --------------------------------------------------------------------------- fakes


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
    checked rules, so it works on real books too, but its chunks are smaller than the contract's
    target of about 350 words and it does not look for sentence boundaries."""
    chunks: list[ChunkDraft] = []
    for paragraph in chapter.paragraphs:
        words = paragraph.text.split()  # normalised text: single spaces, so nothing is lost
        for start in range(0, len(words), MAX_CHUNK_WORDS):
            text = " ".join(words[start : start + MAX_CHUNK_WORDS])
            chunks.append(_chunk(len(chunks) + 1, text, paragraph.page))
    return tuple(chunks)


async def fake_write_chapter_notes(
    chapter: ChapterForNotes, *, language: str | None, llm: LLMClient, model: str
) -> str:
    """Fixed notes from the first words of the chapter. Makes no LLM call and costs nothing."""
    delay = float(os.getenv("FAKE_NOTES_DELAY", "0"))
    if delay > 0:
        await asyncio.sleep(delay)
    first_words = " ".join(" ".join(chapter.chunks).split()[:40])
    return f"Fake notes for “{chapter.title}”: {first_words} …"


# --------------------------------------------------------------------------- choosing


def _implementation(variable: str) -> str:
    name = os.getenv(variable, "fake").strip().lower()
    if name == "fake":
        return name
    raise ValueError(
        f"{variable} must be 'fake' until Track A's implementation is merged, got {name!r}"
    )


def notes_implementation() -> str:
    """'fake' or 'real'; the fake needs no LLM client (and no API key)."""
    return _implementation("BOOKCRUNCH_NOTES")


def get_chunk_chapter() -> ChunkChapterFn:
    _implementation("BOOKCRUNCH_CHUNKER")
    return fake_chunk_chapter


def get_write_chapter_notes() -> WriteChapterNotesFn:
    notes_implementation()
    return fake_write_chapter_notes
