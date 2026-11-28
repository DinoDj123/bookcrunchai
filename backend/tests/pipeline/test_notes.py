"""Tests of the real chapter notes (FR-007, docs/contracts/pipeline.md section 6).

Every test uses FakeLLMClient: no network, no database, no cost.
"""

from __future__ import annotations

import asyncio
import dataclasses
from pathlib import Path

import pytest

from app.db.models import UsageStep
from app.llm.base import LLMError, LLMErrorCode, LLMResult
from app.llm.fake import FakeLLMClient
from app.parsing import ChapterKind
from app.pipeline import (
    DEFAULT_NOTES_BUDGET_TOKENS,
    PARAGRAPH_SEPARATOR,
    ChapterForNotes,
    estimate_tokens,
    needs_notes,
    validate_chapter_notes,
)
from app.pipeline.notes import (
    MAX_TOKENS,
    _overhead_tokens,
    clean_notes,
    split_into_sections,
    write_chapter_notes,
)
from app.pipeline.prompts import Prompt, load_prompt

SNAPSHOTS = Path(__file__).parent / "snapshots"
BOOKS = Path(__file__).parent.parent / "fixtures" / "books"
MODEL = "test-model"
GOOD_NOTES = "Elizabeth meets Mr. Darcy at the ball. " * 20  # 160 words


class CutOff(str):
    """A response that FakeLLMClient returns with stop_reason "length"."""


class LengthAwareFake(FakeLLMClient):
    async def complete(self, **kwargs: object) -> LLMResult:  # type: ignore[override]
        response = self._responses[0] if self._responses else None
        result = await super().complete(**kwargs)  # type: ignore[arg-type]
        if isinstance(response, CutOff):
            return dataclasses.replace(result, stop_reason="length")
        return result


def chapter(*chunks: str, title: str = "Chapter 1") -> ChapterForNotes:
    return ChapterForNotes(position=1, title=title, kind=ChapterKind.CHAPTER, chunks=chunks)


def marked_chunks(count: int, words: int = 300) -> tuple[str, ...]:
    """`count` chunks, each starting with a unique marker, about 2,000 characters each."""
    return tuple(
        f"chunk{number:03d} " + " ".join(f"word{n}" for n in range(words))
        for number in range(count)
    )


def write(
    source: ChapterForNotes,
    llm: FakeLLMClient,
    *,
    language: str | None = "en",
    budget_tokens: int = DEFAULT_NOTES_BUDGET_TOKENS,
) -> str:
    return asyncio.run(
        write_chapter_notes(
            source, language=language, llm=llm, model=MODEL, budget_tokens=budget_tokens
        )
    )


def between(text: str, start: str, end: str) -> str:
    return text.split(start, 1)[1].split(end, 1)[0].strip()


def section_count(source: ChapterForNotes, budget_tokens: int) -> int:
    overhead = _overhead_tokens(load_prompt("chapter_notes"), "Write in English.", source.title)
    return len(
        split_into_sections(source.chunks, budget_tokens=budget_tokens, overhead_tokens=overhead)
    )


# --------------------------------------------------------------------------- one request


def test_short_chapter_is_one_call() -> None:
    llm = FakeLLMClient([GOOD_NOTES])
    source = chapter("First chunk of the chapter.", "Second chunk.")

    notes = write(source, llm)

    assert notes == GOOD_NOTES.strip()
    validate_chapter_notes(notes)
    assert len(llm.calls) == 1
    call = llm.calls[0]
    assert between(call.user, "<chapter>", "</chapter>") == PARAGRAPH_SEPARATOR.join(source.chunks)
    assert "Title: Chapter 1" in call.user
    assert "150–300 words" in call.system


def test_every_call_uses_step_map_the_model_and_max_tokens() -> None:
    llm = FakeLLMClient(default_text=GOOD_NOTES)
    write(chapter(*marked_chunks(12)), llm, budget_tokens=3000)

    assert len(llm.calls) > 2
    assert {call.step for call in llm.calls} == {UsageStep.MAP}
    assert {call.model for call in llm.calls} == {MODEL}
    assert {call.max_tokens for call in llm.calls} == {MAX_TOKENS}


@pytest.mark.parametrize(
    ("language", "instruction"),
    [
        ("en", "Write in English."),
        ("cnr", "Write in Montenegrin, Latin script, ijekavian."),
        (None, "Write in the same language as the text."),
        ("it", "Write in the same language as the text."),
    ],
)
def test_language_instruction(language: str | None, instruction: str) -> None:
    llm = FakeLLMClient([GOOD_NOTES])
    write(chapter("Some text."), llm, language=language)
    assert f"- {instruction}\n" in llm.calls[0].system


def test_the_book_text_is_not_searched_for_placeholders() -> None:
    text = "A {curly} text that mentions {{title}} and {{language_instruction}} and {0}."
    llm = FakeLLMClient([GOOD_NOTES])
    write(chapter(text, title="The {{text}} title"), llm)
    user = llm.calls[0].user
    assert between(user, "<chapter>", "</chapter>") == text
    assert "Title: The {{text}} title" in user


# --------------------------------------------------------------------------- sections and merge


def test_long_chapter_is_split_into_sections_and_merged() -> None:
    budget = 3000
    source = chapter(*marked_chunks(12))
    assert estimate_tokens(PARAGRAPH_SEPARATOR.join(source.chunks)) > budget
    sections = section_count(source, budget)
    assert 2 <= sections < len(source.chunks)  # several chunks per section
    section_answers = [f"Notes on part {n}. " + GOOD_NOTES for n in range(1, sections + 1)]
    llm = FakeLLMClient([*section_answers, "Merged. " + GOOD_NOTES])

    notes = write(source, llm, budget_tokens=budget)

    assert notes == ("Merged. " + GOOD_NOTES).strip()
    assert len(llm.calls) == sections + 1
    *section_calls, merge_call = llm.calls
    for number, call in enumerate(section_calls, start=1):
        assert estimate_tokens(call.system + call.user) <= budget
        assert "<chapter>" in call.user
        assert f"Title: Chapter 1 (part {number} of {sections})" in call.user
        assert "100–200 words" in call.system

    assert "<notes>" in merge_call.user
    assert "150–300 words" in merge_call.system
    merged_input = between(merge_call.user, "<notes>", "</notes>")
    for number in range(1, sections + 1):
        assert f"Part {number}:\nNotes on part {number}." in merged_input


def test_every_chunk_is_in_exactly_one_section_in_order() -> None:
    source = chapter(*marked_chunks(25))
    llm = FakeLLMClient(default_text=GOOD_NOTES)
    write(source, llm, budget_tokens=2500)

    seen: list[str] = []
    for call in llm.calls[:-1]:
        seen += between(call.user, "<chapter>", "</chapter>").split(PARAGRAPH_SEPARATOR)
    assert seen == list(source.chunks)


def test_sections_of_every_pride_and_prejudice_chapter_fit_the_budget() -> None:
    from app.parsing.parser import parse_book
    from app.pipeline.chunking import chunk_chapter

    book = parse_book(BOOKS / "pride-and-prejudice.epub")
    prompt = load_prompt("chapter_notes")
    split_chapters = 0
    for parsed in book.chapters:
        if not needs_notes(parsed.kind):
            continue
        chunks = tuple(chunk.text for chunk in chunk_chapter(parsed))
        overhead = _overhead_tokens(prompt, "Write in English.", parsed.title)
        sections = split_into_sections(
            chunks, budget_tokens=DEFAULT_NOTES_BUDGET_TOKENS, overhead_tokens=overhead
        )
        assert tuple(chunk for section in sections for chunk in section) == chunks
        for section in sections:
            text = PARAGRAPH_SEPARATOR.join(section)
            assert overhead + estimate_tokens(text) <= DEFAULT_NOTES_BUDGET_TOKENS
        split_chapters += len(sections) > 1
    assert split_chapters >= 1  # the long preface needs more than one section


def test_one_failed_section_cancels_the_others() -> None:
    error = LLMError(LLMErrorCode.LLM_UNAVAILABLE, "down", retryable=True)
    started: list[int] = []
    finished: list[int] = []

    class SlowFake(FakeLLMClient):
        async def complete(self, **kwargs: object) -> LLMResult:  # type: ignore[override]
            number = len(started)
            started.append(number)
            if number == 1:
                raise error
            await asyncio.sleep(1)
            finished.append(number)
            return await super().complete(**kwargs)  # type: ignore[arg-type]

    with pytest.raises(LLMError) as raised:
        write(chapter(*marked_chunks(12)), SlowFake(), budget_tokens=3000)
    assert raised.value is error
    assert len(started) > 2
    assert finished == []


# --------------------------------------------------------------------------- answers


def test_clean_notes_removes_markdown_and_the_introduction() -> None:
    answer = (
        "Here are the notes:\n"
        "```text\n"
        "# Chapter 1\n"
        "## Notes\n"
        "Elizabeth meets Mr. Darcy. He refuses to dance with her.\n"
        "\n\n\n"
        "Mrs. Bennet is offended.\n"
        "```\n"
    )
    assert clean_notes(answer) == (
        "Elizabeth meets Mr. Darcy. He refuses to dance with her.\n\nMrs. Bennet is offended."
    )


@pytest.mark.parametrize(
    "answer",
    [
        "Sure! Here are the notes on Chapter 1:\nElizabeth meets Mr. Darcy.",
        "Here is a summary of the notes: Elizabeth meets Mr. Darcy.",
        "  here are my notes for the chapter:\n\nElizabeth meets Mr. Darcy.",
    ],
)
def test_clean_notes_removes_introductions(answer: str) -> None:
    assert clean_notes(answer) == "Elizabeth meets Mr. Darcy."


def test_clean_notes_keeps_the_text_itself() -> None:
    text = "He said: here are the facts. #1 was not a heading; C# is a language."
    assert clean_notes(text) == text


def test_cleaned_answer_is_returned() -> None:
    llm = FakeLLMClient([f"```\n# Notes\n{GOOD_NOTES}\n```"])
    assert write(chapter("Text."), llm) == GOOD_NOTES.strip()
    assert len(llm.calls) == 1


@pytest.mark.parametrize(
    "bad_answer",
    [
        "",
        "```\n# Notes\n```",
        "word " * 501,
        CutOff(GOOD_NOTES),
    ],
    ids=["empty", "only-markdown", "too-long", "cut-off"],
)
def test_an_invalid_answer_is_asked_for_once_more(bad_answer: str) -> None:
    llm = LengthAwareFake([bad_answer, GOOD_NOTES])
    assert write(chapter("Text."), llm) == GOOD_NOTES.strip()
    assert len(llm.calls) == 2
    first, second = llm.calls
    assert "150–300 words" in first.system
    assert "100–150 words" in second.system  # shorter
    assert first.user == second.user


def test_two_invalid_answers_raise_a_retryable_llm_error() -> None:
    llm = LengthAwareFake([CutOff(GOOD_NOTES), "word " * 600])
    with pytest.raises(LLMError) as raised:
        write(chapter("Text."), llm)
    assert raised.value.code == LLMErrorCode.LLM_REQUEST_FAILED
    assert raised.value.retryable is True
    assert len(llm.calls) == 2


def test_an_invalid_section_answer_is_asked_for_once_more() -> None:
    source = chapter(*marked_chunks(12))
    llm = FakeLLMClient(["", *[GOOD_NOTES] * 20])
    assert write(source, llm, budget_tokens=3000) == GOOD_NOTES.strip()
    assert "80–120 words" in llm.calls[1].system  # the retry of the first section


@pytest.mark.parametrize("retryable", [True, False])
def test_llm_error_from_the_client_is_passed_on_unchanged(retryable: bool) -> None:
    error = LLMError(LLMErrorCode.COST_LIMIT_EXCEEDED, "limit", retryable=retryable)
    llm = FakeLLMClient([error])
    with pytest.raises(LLMError) as raised:
        write(chapter("Text."), llm)
    assert raised.value is error
    assert len(llm.calls) == 1  # no second try


def test_llm_error_in_the_merge_is_passed_on_unchanged() -> None:
    error = LLMError(LLMErrorCode.LLM_UNAVAILABLE, "down", retryable=True)
    source = chapter(*marked_chunks(12))
    llm = FakeLLMClient([*[GOOD_NOTES] * section_count(source, 3000), error])
    with pytest.raises(LLMError) as raised:
        write(source, llm, budget_tokens=3000)
    assert raised.value is error


# --------------------------------------------------------------------------- prompts


def test_system_prompt_snapshot() -> None:
    """The chapter notes prompt changes the quality of every summary. When it is changed on
    purpose, update the snapshot file and say why in the pull request."""
    llm = FakeLLMClient([GOOD_NOTES])
    write(chapter("Text."), llm, language="cnr")
    expected = (SNAPSHOTS / "chapter_notes_system_cnr.txt").read_text(encoding="utf-8")
    assert llm.calls[0].system == expected.rstrip("\n")


@pytest.mark.parametrize("name", ["chapter_notes", "merge_notes"])
def test_prompt_files_have_the_expected_placeholders(name: str) -> None:
    prompt = load_prompt(name)
    assert prompt.placeholders == {"title", "language_instruction", "length", "text"}
    assert "{{text}}" in prompt.user
    assert "{{language_instruction}}" in prompt.system


def test_render_needs_exactly_the_placeholders() -> None:
    prompt = Prompt(name="test", system="{{a}}", user="{{b}}")
    assert prompt.render(a="1", b="2") == ("1", "2")
    with pytest.raises(KeyError):
        prompt.render(a="1")
    with pytest.raises(KeyError):
        prompt.render(a="1", b="2", c="3")
