"""TEMPORARY: chunking, chapter notes and the book summary (reduce step) as proposed in the draft
contract (chunk_chapter contract 0.1, not agreed with Track A yet), with fake implementations, so
that the worker stages can be built and tested before Track A's code exists.

When the contract is agreed, its shared part (ChunkDraft, ChapterForNotes, ChapterForSummary,
SummaryDraft, estimate_tokens, the validators) moves to app.pipeline, owned by both tracks, and the
real implementations come from Track A. tasks.py then imports from app.pipeline instead of this
module, and this file is deleted. Nothing outside app.worker may import it.

Implementations are picked like the parser's (BOOKCRUNCH_PARSER):
    BOOKCRUNCH_CHUNKER=fake   one chunk per paragraph (default; "real" does not exist yet)
    BOOKCRUNCH_NOTES=fake     fixed notes without any LLM call (default; "real" does not exist yet)
    BOOKCRUNCH_SUMMARY=fake   a summary of the target length without any LLM call (default; "real"
                              does not exist yet)
    FAKE_NOTES_DELAY=1        waits 1 second per chapter, to see the stage in the UI
    FAKE_SUMMARY_DELAY=5      waits 5 seconds before the summary, to see its job in the UI
"""

import asyncio
import math
import os
from collections.abc import Awaitable, Callable
from dataclasses import dataclass

from app.db.models import Language, SummaryLength
from app.llm.base import LLMClient
from app.parsing import Chapter, ChapterKind, count_words

MAX_CHUNK_WORDS = 500  # FR-006
MAX_NOTES_WORDS = 500  # the notes should be about 150-300 words (FR-007)
PARAGRAPH_SEPARATOR = "\n\n"

# The book summary (FR-008, FR-009): 220 words per minute, accepted within 15 %, and how many key
# ideas each length has. The word count covers everything the reader reads: the overview, the
# chapter summaries and the key ideas.
TARGET_WORDS = {SummaryLength.FIVE_MIN: 1100, SummaryLength.FIFTEEN_MIN: 3300}
LENGTH_TOLERANCE = 0.15
KEY_IDEA_RANGE = {SummaryLength.FIVE_MIN: (5, 10), SummaryLength.FIFTEEN_MIN: (10, 15)}


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


@dataclass(frozen=True, slots=True)
class ChapterForSummary:
    """One chapter the summary is built from: only chapters with notes, in reading order."""

    position: int
    title: str
    notes: str


@dataclass(frozen=True, slots=True)
class ChapterSummaryDraft:
    chapter_position: int  # one of the positions of the ChapterForSummary items
    content: str  # in the output language


@dataclass(frozen=True, slots=True)
class KeyIdeaDraft:
    text: str
    chapter_position: int  # the chapter the idea comes from (FR-009)


@dataclass(frozen=True, slots=True)
class SummaryDraft:
    """What the reduce step returns (FR-008); the real one parses it from the model's JSON."""

    overview: str  # one paragraph
    chapters: tuple[ChapterSummaryDraft, ...]  # in reading order
    key_ideas: tuple[KeyIdeaDraft, ...]  # in the order shown


class PipelineContractViolation(AssertionError):
    def __init__(self, problems: list[str]) -> None:
        self.problems = problems
        super().__init__("Output breaks the pipeline contract:\n  - " + "\n  - ".join(problems))


ChunkChapterFn = Callable[[Chapter], tuple[ChunkDraft, ...]]
WriteChapterNotesFn = Callable[..., Awaitable[str]]
# write_book_summary(title, chapters, *, length, language, llm, model) -> SummaryDraft. The real one
# calls the LLM with UsageStep.REDUCE and raises LLMError for a failed call.
WriteBookSummaryFn = Callable[..., Awaitable[SummaryDraft]]


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


def summary_word_count(draft: SummaryDraft) -> int:
    """Words the reader reads: overview, chapter summaries and key ideas (summaries.word_count)."""
    texts = [draft.overview]
    texts += [chapter.content for chapter in draft.chapters]
    texts += [idea.text for idea in draft.key_ideas]
    return sum(count_words(text) for text in texts)


def drop_invalid_key_ideas(draft: SummaryDraft, positions: set[int]) -> SummaryDraft:
    """FR-009: a key idea that points to a chapter the summary was not built from is discarded, not
    a reason to reject the whole summary. So is an empty one."""
    kept = tuple(
        idea
        for idea in draft.key_ideas
        if idea.chapter_position in positions and count_words(idea.text) > 0
    )
    return SummaryDraft(overview=draft.overview, chapters=draft.chapters, key_ideas=kept)


def validate_summary(draft: SummaryDraft, positions: set[int], length: SummaryLength) -> None:
    """FR-008: valid structure, existing chapter references, length within 15 % of the target, and
    the number of key ideas of FR-009. Call drop_invalid_key_ideas() first. A chapter the model
    left out is accepted; an unknown or repeated one is not. Raises PipelineContractViolation with
    every problem found."""
    problems: list[str] = []
    if count_words(draft.overview) == 0:
        problems.append("the overview is empty")
    if not draft.chapters:
        problems.append("there are no chapter summaries")
    seen: set[int] = set()
    for chapter in draft.chapters:
        position = chapter.chapter_position
        if position not in positions:
            problems.append(f"a summary of unknown chapter {position}")
        elif position in seen:
            problems.append(f"a summary of chapter {position} more than once")
        seen.add(position)
        if count_words(chapter.content) == 0:
            problems.append(f"the summary of chapter {position} is empty")

    target = TARGET_WORDS[length]
    low = round(target * (1 - LENGTH_TOLERANCE))
    high = round(target * (1 + LENGTH_TOLERANCE))
    words = summary_word_count(draft)
    if not low <= words <= high:
        problems.append(f"{words} words, outside {low}-{high}")
    fewest, most = KEY_IDEA_RANGE[length]
    if not fewest <= len(draft.key_ideas) <= most:
        problems.append(f"{len(draft.key_ideas)} key ideas, not {fewest}-{most}")

    if problems:
        raise PipelineContractViolation(problems)


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


def _filler(source: str, count: int) -> str:
    """count words taken in turn from source."""
    words = source.split() or ["fake"]
    return " ".join(words[n % len(words)] for n in range(count))


async def fake_write_book_summary(
    title: str,
    chapters: tuple[ChapterForSummary, ...],
    *,
    length: SummaryLength,
    language: Language,
    llm: LLMClient,
    model: str,
) -> SummaryDraft:
    """A summary of exactly the target length, made of the chapter notes' own words, with the
    fewest key ideas allowed. Makes no LLM call and costs nothing."""
    delay = float(os.getenv("FAKE_SUMMARY_DELAY", "0"))
    if delay > 0:
        await asyncio.sleep(delay)
    fewest, _ = KEY_IDEA_RANGE[length]
    key_ideas = tuple(
        KeyIdeaDraft(f"Fake key idea {n}.", chapters[(n - 1) % len(chapters)].position)
        for n in range(1, fewest + 1)
    )
    overview = f"Fake {language.value} overview of “{title}” from {len(chapters)} chapters."
    used = count_words(overview) + sum(count_words(idea.text) for idea in key_ideas)
    share, extra = divmod(TARGET_WORDS[length] - used, len(chapters))
    summaries = tuple(
        ChapterSummaryDraft(
            chapter.position, _filler(chapter.notes, max(1, share + (1 if i < extra else 0)))
        )
        for i, chapter in enumerate(chapters)
    )
    return SummaryDraft(overview=overview, chapters=summaries, key_ideas=key_ideas)


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


def summary_implementation() -> str:
    """'fake' or 'real'; the fake needs no LLM client (and no API key)."""
    return _implementation("BOOKCRUNCH_SUMMARY")


def get_write_book_summary() -> WriteBookSummaryFn:
    summary_implementation()
    return fake_write_book_summary
