"""The temporary pipeline module of the worker: the fake chunker follows the draft contract, and
validate_chunks catches what the contract forbids. No database needed."""

import dataclasses

import pytest

from app.parsing import Chapter, ChapterKind, Paragraph
from app.worker.pipeline_draft import (
    MAX_CHUNK_WORDS,
    ChunkDraft,
    PipelineContractViolation,
    estimate_tokens,
    fake_chunk_chapter,
    validate_chapter_notes,
    validate_chunks,
)


def chapter(*texts: str, pages: list[int] | None = None) -> Chapter:
    return Chapter(
        position=3,
        title="Chapter",
        kind=ChapterKind.CHAPTER,
        paragraphs=tuple(
            Paragraph(text=text, page=pages[i] if pages else None) for i, text in enumerate(texts)
        ),
    )


def words(count: int, word: str = "word") -> str:
    return " ".join(f"{word}{n}" for n in range(count))


def test_fake_chunker_makes_one_chunk_per_paragraph() -> None:
    source = chapter("First paragraph.", "Second paragraph.")
    chunks = fake_chunk_chapter(source)

    assert [chunk.text for chunk in chunks] == ["First paragraph.", "Second paragraph."]
    assert [chunk.position for chunk in chunks] == [1, 2]
    validate_chunks(source, chunks)


def test_fake_chunker_cuts_a_long_paragraph_and_keeps_every_word() -> None:
    source = chapter(words(1200), "After.", pages=[7, 9])
    chunks = fake_chunk_chapter(source)

    assert [chunk.word_count for chunk in chunks] == [500, 500, 200, 1]
    assert [chunk.page_start for chunk in chunks] == [7, 7, 7, 9]
    validate_chunks(source, chunks)


def test_counts_follow_the_text() -> None:
    (chunk,) = fake_chunk_chapter(chapter("Twelve characters."))
    assert chunk.word_count == 2
    assert chunk.token_count == estimate_tokens("Twelve characters.") == 5


def problems(source: Chapter, chunks: tuple[ChunkDraft, ...]) -> str:
    with pytest.raises(PipelineContractViolation) as caught:
        validate_chunks(source, chunks)
    return str(caught.value)


def test_lost_words_are_caught() -> None:
    source = chapter("One two three.", "Four five.")
    (first, _second) = fake_chunk_chapter(source)
    assert "differ from the chapter" in problems(source, (first,))


def test_a_cut_paragraph_is_caught() -> None:
    source = chapter("One two. Three four.")
    halves = (
        ChunkDraft(1, "One two.", 2, estimate_tokens("One two."), None),
        ChunkDraft(2, "Three four.", 2, estimate_tokens("Three four."), None),
    )
    assert "cuts a paragraph" in problems(source, halves)


def test_too_many_words_and_wrong_positions_are_caught() -> None:
    long_text = words(MAX_CHUNK_WORDS + 1)
    source = chapter(long_text)
    chunk = ChunkDraft(2, long_text, MAX_CHUNK_WORDS + 1, estimate_tokens(long_text), None)
    found = problems(source, (chunk,))
    assert "more than 500" in found
    assert "positions must be 1..1" in found


def test_pages_are_checked() -> None:
    epub = chapter("Text.")
    (chunk,) = fake_chunk_chapter(epub)
    assert "None for EPUB" in problems(epub, (dataclasses.replace(chunk, page_start=1),))

    pdf = chapter("First.", "Second.", pages=[4, 5])
    first, second = fake_chunk_chapter(pdf)
    assert "never decrease" in problems(
        pdf, (dataclasses.replace(first, page_start=5), dataclasses.replace(second, page_start=4))
    )


@pytest.mark.parametrize("notes", ["", "   ", words(501)])
def test_empty_or_long_notes_are_caught(notes: str) -> None:
    with pytest.raises(PipelineContractViolation):
        validate_chapter_notes(notes)
