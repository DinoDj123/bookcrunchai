"""Tests of the pipeline contract itself and of the fake chunker and notes.

Track A adds its own tests for the real chunker and notes in other files of this folder and calls
validate_chunks() / validate_chapter_notes() on their output.
"""

from dataclasses import replace
from pathlib import Path

import pytest

from app.parsing import Chapter, ChapterKind, Paragraph, ParsedBook
from app.parsing.fake import fake_parse_book
from app.parsing.parser import parse_book
from app.pipeline import (
    DEFAULT_NOTES_BUDGET_TOKENS,
    MAX_CHUNK_WORDS,
    MAX_NOTES_WORDS,
    PARAGRAPH_SEPARATOR,
    ChapterForNotes,
    ChunkDraft,
    PipelineContractViolation,
    estimate_tokens,
    get_chunk_chapter,
    get_write_chapter_notes,
    needs_notes,
    validate_chapter_notes,
    validate_chunks,
)
from app.pipeline.fake import fake_chunk_chapter, fake_write_chapter_notes

BOOKS = Path(__file__).parent.parent / "fixtures" / "books"
REAL_BOOKS = ["meditations.epub", "pride-and-prejudice.epub", "the-prince.pdf"]


def chapter(*texts: str, pages: list[int] | None = None) -> Chapter:
    return Chapter(
        position=3,
        title="Chapter",
        kind=ChapterKind.CHAPTER,
        paragraphs=tuple(
            Paragraph(text=text, page=pages[i] if pages else None) for i, text in enumerate(texts)
        ),
    )


def chunk(position: int, text: str, page: int | None = None) -> ChunkDraft:
    return ChunkDraft(position, text, len(text.split()), estimate_tokens(text), page)


def words(count: int, word: str = "word") -> str:
    return " ".join(f"{word}{n}" for n in range(count))


# --------------------------------------------------------------------------- helpers


def test_estimate_tokens_is_a_quarter_of_the_characters_rounded_up() -> None:
    assert estimate_tokens("") == 0
    assert estimate_tokens("abcd") == 1
    assert estimate_tokens("Twelve characters.") == 5  # 18 characters


@pytest.mark.parametrize(
    ("kind", "expected"),
    [
        (ChapterKind.CHAPTER, True),
        (ChapterKind.FRONT_MATTER, True),
        (ChapterKind.BACK_MATTER, False),
    ],
)
def test_needs_notes(kind: ChapterKind, expected: bool) -> None:
    assert needs_notes(kind) is expected


# --------------------------------------------------------------------------- validate_chunks

# Every checked rule: chunks that follow it and chunks that break it, for the same chapter.
LONG = words(MAX_CHUNK_WORDS + 1)
EPUB = chapter("One two three.", "Four five.")
PDF = chapter("One two three.", "Four five.", pages=[4, 6])

RULES = [
    pytest.param(
        EPUB,
        (chunk(1, "One two three."), chunk(2, "Four five.")),
        [chunk(1, "One two three."), chunk(2, "Four five.")],
        "must be a tuple",
        id="chunks are a tuple",
    ),
    pytest.param(
        EPUB,
        (chunk(1, "One two three." + PARAGRAPH_SEPARATOR + "Four five."),),
        (),
        "no chunks",
        id="at least one chunk",
    ),
    pytest.param(
        EPUB,
        (chunk(1, "One two three."), chunk(2, "Four five.")),
        (chunk(2, "One two three."), chunk(3, "Four five.")),
        "positions must be 1..2",
        id="positions 1..n",
    ),
    pytest.param(
        EPUB,
        (chunk(1, "One two three."), chunk(2, "Four five.")),
        (chunk(1, "One two three."), chunk(2, "Four five."), chunk(3, "")),
        "has no words",
        id="every chunk has words",
    ),
    pytest.param(
        chapter(LONG),
        (chunk(1, words(MAX_CHUNK_WORDS)), chunk(2, f"word{MAX_CHUNK_WORDS}")),
        (chunk(1, LONG),),
        f"more than {MAX_CHUNK_WORDS}",
        id="at most 500 words",
    ),
    pytest.param(
        EPUB,
        (chunk(1, "One two three."), chunk(2, "Four five.")),
        (replace(chunk(1, "One two three."), word_count=2), chunk(2, "Four five.")),
        "word_count 2, counted 3",
        id="word_count",
    ),
    pytest.param(
        EPUB,
        (chunk(1, "One two three."), chunk(2, "Four five.")),
        (replace(chunk(1, "One two three."), token_count=3), chunk(2, "Four five.")),
        "token_count is not estimate_tokens",
        id="token_count",
    ),
    pytest.param(
        EPUB,
        (chunk(1, "One two three."), chunk(2, "Four five.")),
        (chunk(1, "One two three."),),
        "differ from the chapter",
        id="no word lost",
    ),
    pytest.param(
        EPUB,
        (chunk(1, "One two three."), chunk(2, "Four five.")),
        (chunk(1, "Chapter 3: One two three."), chunk(2, "Four five.")),
        "differ from the chapter",
        id="no text added",
    ),
    pytest.param(
        EPUB,
        (chunk(1, "One two three."), chunk(2, "Four five.")),
        (chunk(1, "One two"), chunk(2, "three."), chunk(3, "Four five.")),
        "cuts a paragraph",
        id="paragraphs stay whole",
    ),
    pytest.param(
        EPUB,
        (chunk(1, "One two three." + PARAGRAPH_SEPARATOR + "Four five."),),
        (chunk(1, "One two three.\nFour five."),),
        "cuts a paragraph",
        id="paragraphs joined with a blank line",
    ),
    pytest.param(
        EPUB,
        (chunk(1, "One two three."), chunk(2, "Four five.")),
        (chunk(1, "One two three.", page=1), chunk(2, "Four five.")),
        "None for EPUB",
        id="no pages on EPUB",
    ),
    pytest.param(
        PDF,
        (chunk(1, "One two three.", page=4), chunk(2, "Four five.", page=6)),
        (chunk(1, "One two three.", page=4), chunk(2, "Four five.", page=5)),
        "the page of a paragraph",
        id="PDF page is a paragraph's page",
    ),
    pytest.param(
        PDF,
        (chunk(1, "One two three.", page=4), chunk(2, "Four five.", page=6)),
        (chunk(1, "One two three.", page=6), chunk(2, "Four five.", page=4)),
        "never decrease",
        id="PDF pages never decrease",
    ),
]


@pytest.mark.parametrize(("source", "good", "bad", "problem"), RULES)
def test_validate_chunks_rule(
    source: Chapter, good: tuple[ChunkDraft, ...], bad: tuple[ChunkDraft, ...], problem: str
) -> None:
    validate_chunks(source, good)
    with pytest.raises(PipelineContractViolation) as caught:
        validate_chunks(source, bad)
    assert problem in str(caught.value)


def test_validate_chunks_lists_all_problems() -> None:
    bad = (replace(chunk(2, "One two three.", page=1), word_count=9),)
    with pytest.raises(PipelineContractViolation) as caught:
        validate_chunks(EPUB, bad)
    assert len(caught.value.problems) == 4  # positions, word_count, lost words, page on EPUB


def test_short_chunks_are_allowed() -> None:
    """Rule 5 (few chunks under 100 words) is a guideline, not checked."""
    source = chapter(*(f"Sentence {n}." for n in range(10)))
    validate_chunks(source, tuple(chunk(n + 1, f"Sentence {n}.") for n in range(10)))


# --------------------------------------------------------------------------- validate_chapter_notes


@pytest.mark.parametrize(
    ("good", "bad", "problem"),
    [
        pytest.param("Short notes.", "  ", "empty", id="not empty"),
        pytest.param(words(MAX_NOTES_WORDS), words(MAX_NOTES_WORDS + 1), "501 words", id="max"),
    ],
)
def test_validate_chapter_notes_rule(good: str, bad: str, problem: str) -> None:
    validate_chapter_notes(good)
    with pytest.raises(PipelineContractViolation) as caught:
        validate_chapter_notes(bad)
    assert problem in str(caught.value)


# --------------------------------------------------------------------------- fakes


@pytest.fixture(params=["epub", "pdf"])
def fake_book(request: pytest.FixtureRequest, tmp_path: Path, monkeypatch) -> ParsedBook:
    monkeypatch.setenv("FAKE_PARSER_FORMAT", request.param)
    path = tmp_path / "book"
    path.write_bytes(b"not read by the fake parser")
    return fake_parse_book(path)


@pytest.fixture(scope="module", params=REAL_BOOKS)
def real_book(request: pytest.FixtureRequest) -> ParsedBook:
    return parse_book(BOOKS / request.param)


def for_notes(source: Chapter) -> ChapterForNotes:
    chunks = fake_chunk_chapter(source)
    texts = tuple(c.text for c in chunks)
    return ChapterForNotes(source.position, source.title, source.kind, texts)


async def check_fakes(book: ParsedBook) -> None:
    for source in book.chapters:
        validate_chunks(source, fake_chunk_chapter(source))
        if needs_notes(source.kind):
            notes = await fake_write_chapter_notes(
                for_notes(source),
                language=book.language,
                llm=None,  # type: ignore[arg-type]  # the fake makes no call
                model="fake-model",
                budget_tokens=DEFAULT_NOTES_BUDGET_TOKENS,
            )
            validate_chapter_notes(notes)
            assert source.title in notes


@pytest.mark.anyio
async def test_fakes_follow_the_contract_on_the_fake_book(fake_book: ParsedBook) -> None:
    await check_fakes(fake_book)


@pytest.mark.anyio
async def test_fakes_follow_the_contract_on_real_books(real_book: ParsedBook) -> None:
    await check_fakes(real_book)


def test_fake_chunker_splits_a_long_paragraph_and_keeps_the_page() -> None:
    source = chapter(words(1200), "After.", pages=[7, 9])
    chunks = fake_chunk_chapter(source)
    assert [c.word_count for c in chunks] == [500, 500, 200, 1]
    assert [c.page_start for c in chunks] == [7, 7, 7, 9]
    validate_chunks(source, chunks)


@pytest.mark.anyio
async def test_fake_notes_wait_when_asked(monkeypatch) -> None:
    monkeypatch.setenv("FAKE_NOTES_DELAY", "0.01")
    notes = await fake_write_chapter_notes(
        ChapterForNotes(1, "Preface", ChapterKind.FRONT_MATTER, ("Some text.",)),
        language=None,
        llm=None,  # type: ignore[arg-type]
        model="fake-model",
        budget_tokens=100,
    )
    validate_chapter_notes(notes)


# --------------------------------------------------------------------------- choosing


def test_get_chunk_chapter_selects_implementation(monkeypatch) -> None:
    from app.pipeline.chunking import chunk_chapter

    monkeypatch.setenv("BOOKCRUNCH_CHUNKER", "fake")
    assert get_chunk_chapter() is fake_chunk_chapter
    monkeypatch.setenv("BOOKCRUNCH_CHUNKER", " Real ")
    assert get_chunk_chapter() is chunk_chapter
    monkeypatch.delenv("BOOKCRUNCH_CHUNKER")
    assert get_chunk_chapter() is chunk_chapter  # default
    monkeypatch.setenv("BOOKCRUNCH_CHUNKER", "something-else")
    with pytest.raises(ValueError):
        get_chunk_chapter()


def test_get_write_chapter_notes_selects_implementation(monkeypatch) -> None:
    from app.pipeline.notes import write_chapter_notes

    monkeypatch.setenv("BOOKCRUNCH_NOTES", "fake")
    assert get_write_chapter_notes() is fake_write_chapter_notes
    monkeypatch.setenv("BOOKCRUNCH_NOTES", "real")
    assert get_write_chapter_notes() is write_chapter_notes
    monkeypatch.delenv("BOOKCRUNCH_NOTES")
    assert get_write_chapter_notes() is write_chapter_notes  # default
    monkeypatch.setenv("BOOKCRUNCH_NOTES", "something-else")
    with pytest.raises(ValueError):
        get_write_chapter_notes()
