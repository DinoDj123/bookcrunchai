"""write_chapter_notes(): notes on one chapter, the map step (FR-007).

Rules: docs/contracts/pipeline.md, section 6.

A chapter whose prompt fits budget_tokens is one request. A longer chapter is split into sections
of consecutive whole chunks, each section fits the budget, the sections are summarised in parallel
(the client's semaphore limits how many run at once) and one more request merges their notes.

Every answer is cleaned (code fences, markdown headings and an introduction like "Here are the
notes:" are removed) and checked with validate_chapter_notes(). An answer that was cut off, is
empty or is too long is asked for once more with a shorter length; a second bad answer raises
LLMError(LLM_REQUEST_FAILED, retryable=True). LLMError from the client is passed on unchanged.
"""

from __future__ import annotations

import asyncio
import re
from collections.abc import Coroutine, Iterable
from typing import TYPE_CHECKING

from app.db.models import UsageStep
from app.llm.base import LLMError, LLMErrorCode

from .contract import (
    PARAGRAPH_SEPARATOR,
    ChapterForNotes,
    PipelineContractViolation,
    estimate_tokens,
    validate_chapter_notes,
)
from .prompts import Prompt, load_prompt

if TYPE_CHECKING:
    from app.llm.base import LLMClient

# 300 words are about 400 tokens; 700 leave room for a longer answer, which is then caught by
# validate_chapter_notes() (more than 500 words) rather than cut off in the middle.
MAX_TOKENS = 700

# The word ranges asked for: (first answer, the second try after an invalid answer).
CHAPTER_LENGTH = ("150–300", "100–150")
SECTION_LENGTH = ("100–200", "80–120")

LANGUAGE_NAMES = {"en": "English", "cnr": "Montenegrin, Latin script, ijekavian"}
SAME_LANGUAGE = "Write in the same language as the text."

CODE_FENCE = re.compile(r"^\s*```.*$", re.MULTILINE)
HEADING = re.compile(r"^\s{0,3}#{1,6}(\s.*)?$", re.MULTILINE)
INTRODUCTION = re.compile(
    r"^\s*(?:(?:sure|okay|ok|certainly)\b[^\n:]{0,20}?[.,!]\s*)?"
    r"here (?:are|is) [^\n:]{0,80}?\bnotes?\b[^\n:]{0,80}:\s*",
    re.IGNORECASE,
)
EXTRA_BLANK_LINES = re.compile(r"\n{3,}")


async def write_chapter_notes(
    chapter: ChapterForNotes,
    *,
    language: str | None,
    llm: LLMClient,
    model: str,
    budget_tokens: int,
) -> str:
    notes_prompt = load_prompt("chapter_notes")
    language_instruction = _language_instruction(language)
    text = PARAGRAPH_SEPARATOR.join(chapter.chunks)

    whole = _render(notes_prompt, chapter.title, language_instruction, CHAPTER_LENGTH[0], text)
    if _request_tokens(*whole) <= budget_tokens:
        return await _ask(
            llm, model, notes_prompt, chapter.title, language_instruction, CHAPTER_LENGTH, text
        )

    sections = split_into_sections(
        chapter.chunks,
        budget_tokens=budget_tokens,
        overhead_tokens=_overhead_tokens(notes_prompt, language_instruction, chapter.title),
    )
    count = len(sections)
    section_notes = await _gather_or_cancel(
        _ask(
            llm,
            model,
            notes_prompt,
            f"{chapter.title} (part {number} of {count})",
            language_instruction,
            SECTION_LENGTH,
            PARAGRAPH_SEPARATOR.join(section),
        )
        for number, section in enumerate(sections, start=1)
    )
    return await _merge(llm, model, chapter.title, language_instruction, section_notes)


def split_into_sections(
    chunks: tuple[str, ...], *, budget_tokens: int, overhead_tokens: int
) -> list[tuple[str, ...]]:
    """Consecutive whole chunks, as many per section as fit budget_tokens together with the
    prompt (overhead_tokens). Each chunk costs its estimate plus one token for the separator,
    which is never less than the estimate of the joined text, so a section never goes over.
    A chunk too big for the budget on its own still gets a section of its own: chunks are never
    cut. With the default budget that cannot happen (a chunk has at most 500 words)."""
    sections: list[tuple[str, ...]] = []
    current: list[str] = []
    used = overhead_tokens
    for chunk in chunks:
        cost = estimate_tokens(chunk) + 1
        if current and used + cost > budget_tokens:
            sections.append(tuple(current))
            current, used = [], overhead_tokens
        current.append(chunk)
        used += cost
    if current:
        sections.append(tuple(current))
    return sections


def clean_notes(text: str) -> str:
    """Removes what small models add around plain-text notes despite the prompt."""
    text = CODE_FENCE.sub("", text)
    text = HEADING.sub("", text)
    text = INTRODUCTION.sub("", text, count=1)
    text = EXTRA_BLANK_LINES.sub("\n\n", text)
    return text.strip()


# --------------------------------------------------------------------------- internals


def _language_instruction(language: str | None) -> str:
    """language is books.source_language: a lowercase ISO 639 code or None. Only the two
    languages of the application are named; any other code (or None) keeps the text's own."""
    name = LANGUAGE_NAMES.get(language or "")
    return f"Write in {name}." if name else SAME_LANGUAGE


def _render(
    prompt: Prompt, title: str, language_instruction: str, length: str, text: str
) -> tuple[str, str]:
    return prompt.render(
        title=title, language_instruction=language_instruction, length=length, text=text
    )


def _request_tokens(system: str, user: str) -> int:
    return estimate_tokens(system + user)


def _overhead_tokens(prompt: Prompt, language_instruction: str, title: str) -> int:
    """The prompt of one section without its text. The longest title a section gets is used."""
    longest_title = f"{title} (part 9999 of 9999)"
    return _request_tokens(
        *_render(prompt, longest_title, language_instruction, SECTION_LENGTH[0], "")
    )


async def _merge(
    llm: LLMClient, model: str, title: str, language_instruction: str, notes: list[str]
) -> str:
    merge_prompt = load_prompt("merge_notes")
    text = PARAGRAPH_SEPARATOR.join(
        f"Part {number}:\n{part}" for number, part in enumerate(notes, start=1)
    )
    return await _ask(llm, model, merge_prompt, title, language_instruction, CHAPTER_LENGTH, text)


async def _ask(
    llm: LLMClient,
    model: str,
    prompt: Prompt,
    title: str,
    language_instruction: str,
    lengths: tuple[str, str],
    text: str,
) -> str:
    """One request, and one more with a shorter length if the answer is unusable."""
    problem = ""
    for length in lengths:
        system, user = _render(prompt, title, language_instruction, length, text)
        result = await llm.complete(  # LLMError is passed on unchanged
            step=UsageStep.MAP, model=model, system=system, user=user, max_tokens=MAX_TOKENS
        )
        if result.stop_reason == "length":
            problem = "the answer was cut off at max_tokens"
            continue
        notes = clean_notes(result.text)
        try:
            validate_chapter_notes(notes)
        except PipelineContractViolation as error:
            problem = "; ".join(error.problems)
            continue
        return notes
    raise LLMError(
        LLMErrorCode.LLM_REQUEST_FAILED,
        f"{prompt.name} for {title!r}: two invalid answers, the last one: {problem}",
        retryable=True,
    )


async def _gather_or_cancel(calls: Iterable[Coroutine[object, object, str]]) -> list[str]:
    """asyncio.gather(), but when one section fails the others are cancelled: the chapter fails
    anyway, and their answers would only cost tokens."""
    tasks = [asyncio.ensure_future(call) for call in calls]
    try:
        return list(await asyncio.gather(*tasks))
    except BaseException:
        for task in tasks:
            task.cancel()
        await asyncio.gather(*tasks, return_exceptions=True)
        raise
