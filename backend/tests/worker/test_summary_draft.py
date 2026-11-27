"""The draft summary contract of the worker (app.worker.pipeline_draft): the fake reduce function
follows it, validate_summary catches what FR-008 and FR-009 forbid. No database needed."""

import dataclasses

import pytest

from app.db.models import Language, SummaryLength
from app.llm.fake import FakeLLMClient
from app.worker.pipeline_draft import (
    KEY_IDEA_RANGE,
    LENGTH_TOLERANCE,
    TARGET_WORDS,
    ChapterForSummary,
    ChapterSummaryDraft,
    KeyIdeaDraft,
    PipelineContractViolation,
    SummaryDraft,
    drop_invalid_key_ideas,
    fake_write_book_summary,
    get_write_book_summary,
    summary_word_count,
    validate_summary,
)

pytestmark = pytest.mark.anyio

FIVE = SummaryLength.FIVE_MIN
FIFTEEN = SummaryLength.FIFTEEN_MIN


def chapters(count: int) -> tuple[ChapterForSummary, ...]:
    return tuple(
        ChapterForSummary(position=n, title=f"Chapter {n}", notes=f"Notes of chapter {n}.")
        for n in range(1, count + 1)
    )


async def fake_summary(count: int = 4, length: SummaryLength = FIVE) -> SummaryDraft:
    return await fake_write_book_summary(
        "A Book", chapters(count), length=length, language=Language.CNR,
        llm=FakeLLMClient(), model="any",
    )  # fmt: skip


def positions(count: int) -> set[int]:
    return set(range(1, count + 1))


def test_targets_follow_the_specification() -> None:
    assert TARGET_WORDS == {FIVE: 1100, FIFTEEN: 3300}  # 220 words per minute (FR-008)
    assert LENGTH_TOLERANCE == 0.15
    assert KEY_IDEA_RANGE == {FIVE: (5, 10), FIFTEEN: (10, 15)}  # FR-009


@pytest.mark.parametrize("length", [FIVE, FIFTEEN])
@pytest.mark.parametrize("count", [1, 4, 40])
async def test_fake_summary_follows_the_contract(length: SummaryLength, count: int) -> None:
    draft = await fake_summary(count, length)

    validate_summary(draft, positions(count), length)
    assert summary_word_count(draft) == TARGET_WORDS[length]
    assert [c.chapter_position for c in draft.chapters] == list(range(1, count + 1))
    assert len(draft.key_ideas) == KEY_IDEA_RANGE[length][0]
    assert {idea.chapter_position for idea in draft.key_ideas} <= positions(count)


async def test_fake_makes_no_llm_call() -> None:
    llm = FakeLLMClient()
    await fake_write_book_summary(
        "A Book", chapters(2), length=FIVE, language=Language.EN, llm=llm, model="any"
    )
    assert llm.calls == []


def test_word_count_covers_overview_chapters_and_key_ideas() -> None:
    draft = SummaryDraft(
        overview="one two",
        chapters=(ChapterSummaryDraft(1, "three four five"),),
        key_ideas=(KeyIdeaDraft("six", 1),),
    )
    assert summary_word_count(draft) == 6


async def test_drop_invalid_key_ideas_keeps_the_others_in_order() -> None:
    draft = await fake_summary(4)
    ideas = (
        KeyIdeaDraft("first", 2),
        KeyIdeaDraft("unknown chapter", 99),
        KeyIdeaDraft("   ", 1),
        KeyIdeaDraft("second", 4),
    )
    cleaned = drop_invalid_key_ideas(dataclasses.replace(draft, key_ideas=ideas), positions(4))
    assert cleaned.key_ideas == (KeyIdeaDraft("first", 2), KeyIdeaDraft("second", 4))
    assert cleaned.chapters == draft.chapters


def _with_chapter(draft: SummaryDraft, index: int, **changes: object) -> SummaryDraft:
    chapters_ = list(draft.chapters)
    chapters_[index] = dataclasses.replace(chapters_[index], **changes)  # type: ignore[arg-type]
    return dataclasses.replace(draft, chapters=tuple(chapters_))


def _padded(draft: SummaryDraft, extra_words: int) -> SummaryDraft:
    first = draft.chapters[0]
    content = first.content + " pad" * extra_words if extra_words > 0 else first.content
    if extra_words < 0:
        content = " ".join(first.content.split()[:extra_words])
    return _with_chapter(draft, 0, content=content)


@pytest.mark.parametrize(
    ("change", "problem"),
    [
        (lambda d: dataclasses.replace(d, overview="  "), "overview is empty"),
        (lambda d: dataclasses.replace(d, chapters=()), "no chapter summaries"),
        (lambda d: _with_chapter(d, 1, chapter_position=99), "unknown chapter 99"),
        (lambda d: _with_chapter(d, 1, chapter_position=1), "chapter 1 more than once"),
        (lambda d: _with_chapter(d, 2, content=""), "chapter 3 is empty"),
        (lambda d: _padded(d, 200), "words, outside"),
        (lambda d: _padded(d, -200), "words, outside"),
        (lambda d: dataclasses.replace(d, key_ideas=d.key_ideas[:4]), "4 key ideas"),
        (lambda d: dataclasses.replace(d, key_ideas=d.key_ideas * 3), "15 key ideas"),
    ],
)
async def test_validate_summary_rejects(change: object, problem: str) -> None:
    draft = await fake_summary(4)
    broken = change(draft)  # type: ignore[operator]
    with pytest.raises(PipelineContractViolation, match=problem):
        validate_summary(broken, positions(4), FIVE)


async def test_length_within_fifteen_percent_is_accepted() -> None:
    draft = await fake_summary(4)
    validate_summary(_padded(draft, 160), positions(4), FIVE)  # 1,260 words
    validate_summary(_padded(draft, -160), positions(4), FIVE)  # 940 words


async def test_missing_chapters_are_accepted() -> None:
    draft = await fake_summary(4)
    # Chapter 2 only, with the words of all four, so the length stays right.
    only_two = ChapterSummaryDraft(2, " ".join(c.content for c in draft.chapters))
    validate_summary(dataclasses.replace(draft, chapters=(only_two,)), positions(4), FIVE)


def test_only_the_fake_reduce_exists_yet(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("BOOKCRUNCH_SUMMARY", "fake")
    assert get_write_book_summary() is fake_write_book_summary
    monkeypatch.setenv("BOOKCRUNCH_SUMMARY", "real")
    with pytest.raises(ValueError, match="BOOKCRUNCH_SUMMARY must be 'fake'"):
        get_write_book_summary()
