"""Tests of the real chunker (FR-006, docs/contracts/pipeline.md section 3).

Every result is checked with validate_chunks(); the sizes are checked on top of that.
"""

from __future__ import annotations

import time
from pathlib import Path

import pytest

from app.parsing import Chapter, ChapterKind, Paragraph, ParsedBook, count_words
from app.parsing.parser import parse_book
from app.pipeline import (
    MAX_CHUNK_WORDS,
    PARAGRAPH_SEPARATOR,
    TARGET_CHUNK_WORDS,
    ChunkDraft,
    estimate_tokens,
    validate_chunks,
)
from app.pipeline.chunking import chunk_chapter

BOOKS = Path(__file__).parent.parent / "fixtures" / "books"
REAL_BOOKS = ["meditations.epub", "pride-and-prejudice.epub", "the-prince.pdf"]


def chapter(*texts: str, pages: list[int] | None = None) -> Chapter:
    return Chapter(
        position=1,
        title="Chapter",
        kind=ChapterKind.CHAPTER,
        paragraphs=tuple(
            Paragraph(text=text, page=pages[i] if pages else None) for i, text in enumerate(texts)
        ),
    )


def words(count: int, word: str = "word") -> str:
    """`count` words without a full stop: one sentence."""
    return " ".join(f"{word}{n}" for n in range(count))


def sentences(count: int, length: int = 20) -> str:
    """`count` sentences of `length` words each, every one starting with a capital."""
    return " ".join(f"Start {words(length - 1)}." for _ in range(count))


def chunked(source: Chapter) -> tuple[ChunkDraft, ...]:
    chunks = chunk_chapter(source)
    validate_chunks(source, chunks)
    return chunks


# --------------------------------------------------------------------------- synthetic


def test_short_chapter_is_one_chunk() -> None:
    source = chapter("A short first paragraph.", "And a second one.")
    chunks = chunked(source)
    assert len(chunks) == 1
    assert chunks[0].text == "A short first paragraph." + PARAGRAPH_SEPARATOR + "And a second one."
    assert chunks[0].word_count == 8
    assert chunks[0].token_count == estimate_tokens(chunks[0].text)
    assert chunks[0].page_start is None


def test_many_short_paragraphs_are_grouped_to_the_target() -> None:
    source = chapter(*(words(30, f"p{n}w") for n in range(40)))  # 1,200 words
    chunks = chunked(source)
    # A chunk closes as soon as it reaches 350 words: 12 paragraphs of 30 = 360.
    assert [c.word_count for c in chunks] == [360, 360, 360, 120]
    assert all(len(c.text.split(PARAGRAPH_SEPARATOR)) == c.word_count // 30 for c in chunks)


def test_chunk_closes_before_a_paragraph_that_would_pass_the_maximum() -> None:
    source = chapter(words(300, "a"), words(250, "b"), words(150, "c"))
    assert [c.word_count for c in chunked(source)] == [300, 400]


def test_paragraph_of_1200_words_is_split_on_sentences() -> None:
    long = sentences(60)  # 60 sentences of 20 words
    after = words(150, "after")  # long enough not to be merged into the last piece
    source = chapter("Before it.", long, after)
    chunks = chunked(source)
    assert [c.word_count for c in chunks] == [2, 500, 500, 200, 150]
    pieces = [c.text for c in chunks[1:4]]
    assert " ".join(pieces) == long
    assert all(piece.endswith(".") and piece.startswith("Start ") for piece in pieces)


def test_sentence_of_600_words_without_a_full_stop_is_split_between_words() -> None:
    long = words(600)
    chunks = chunked(chapter(long))
    assert [c.word_count for c in chunks] == [500, 100]
    assert " ".join(c.text for c in chunks) == long


def test_long_sentence_tail_is_grouped_with_the_next_sentences() -> None:
    long = words(600) + ". " + sentences(10)  # 600 + 200 words
    assert [c.word_count for c in chunked(chapter(long))] == [500, 300]


def test_paragraph_of_exactly_500_words_is_not_split() -> None:
    source = chapter(words(20, "a"), sentences(25), words(20, "b"))  # 20 + 500 + 20
    chunks = chunked(source)
    assert [c.word_count for c in chunks] == [20, 500, 20]
    assert chunks[1].text == source.paragraphs[1].text


def test_pdf_page_start_is_the_page_of_the_first_paragraph() -> None:
    source = chapter(
        words(200, "a"), words(200, "b"), words(100, "c"), sentences(60), words(200, "d"),
        pages=[3, 4, 4, 5, 7],
    )  # fmt: skip
    chunks = chunked(source)
    assert [c.word_count for c in chunks] == [400, 100, 500, 500, 200, 200]
    assert [c.page_start for c in chunks] == [3, 4, 5, 5, 5, 7]


def test_short_last_chunk_is_merged_into_the_one_before() -> None:
    source = chapter(words(360, "a"), words(50, "b"))
    chunks = chunked(source)
    assert [c.word_count for c in chunks] == [410]
    assert chunks[0].text == source.paragraphs[0].text + PARAGRAPH_SEPARATOR + words(50, "b")


def test_short_last_chunk_stays_when_the_sum_would_pass_the_maximum() -> None:
    source = chapter(words(480, "a"), words(50, "b"))
    assert [c.word_count for c in chunked(source)] == [480, 50]


def test_short_last_chunk_is_merged_after_a_split_paragraph() -> None:
    source = chapter(sentences(60), words(30, "b"))
    assert [c.word_count for c in chunked(source)] == [500, 500, 230]


def test_short_last_piece_of_a_split_paragraph_is_not_merged() -> None:
    source = chapter(sentences(26))  # 520 words: 500 + 20
    assert [c.word_count for c in chunked(source)] == [500, 20]


def test_chunking_is_deterministic() -> None:
    source = chapter(*(sentences(n % 7 + 1, 13 + n % 5) for n in range(80)), sentences(70))
    assert chunk_chapter(source) == chunk_chapter(source)


# --------------------------------------------------------------------------- real books


@pytest.fixture(scope="module", params=REAL_BOOKS)
def book(request: pytest.FixtureRequest) -> ParsedBook:
    return parse_book(BOOKS / request.param)


def test_every_chapter_of_the_real_books_follows_the_contract(book: ParsedBook) -> None:
    chunks = []
    for source in book.chapters:
        chunks.extend(chunked(source))
    average = sum(c.word_count for c in chunks) / len(chunks)
    assert 250 <= average <= 450


def test_split_paragraphs_are_not_cut_after_mr_mrs_dr(book: ParsedBook) -> None:
    whole = {p.text for c in book.chapters for p in c.paragraphs}
    cuts = 0
    for source in book.chapters:
        for chunk in chunk_chapter(source):
            last = chunk.text.split(PARAGRAPH_SEPARATOR)[-1]
            if last not in whole:  # the chunk ends inside a split paragraph
                cuts += 1
                assert last.split()[-1] not in {"Mr.", "Mrs.", "Dr."}, last[-80:]
    long_paragraphs = [
        p for c in book.chapters for p in c.paragraphs if count_words(p.text) > MAX_CHUNK_WORDS
    ]
    assert long_paragraphs, "every test book has paragraphs over 500 words"
    assert cuts >= len(long_paragraphs)


def test_real_books_are_chunked_the_same_way_twice(book: ParsedBook) -> None:
    assert [chunk_chapter(c) for c in book.chapters] == [chunk_chapter(c) for c in book.chapters]


def test_pride_and_prejudice_is_chunked_in_under_two_seconds() -> None:
    book = parse_book(BOOKS / "pride-and-prejudice.epub")
    start = time.perf_counter()
    chunks = [chunk for source in book.chapters for chunk in chunk_chapter(source)]
    assert time.perf_counter() - start < 2.0
    assert all(c.page_start is None for c in chunks)


def test_the_prince_has_pages_on_every_chunk() -> None:
    book = parse_book(BOOKS / "the-prince.pdf")
    for source in book.chapters:
        starts = [c.page_start for c in chunk_chapter(source)]
        assert all(isinstance(page, int) for page in starts)
        assert starts == sorted(starts)


def test_most_chunks_are_near_the_target(book: ParsedBook) -> None:
    sizes = [c.word_count for source in book.chapters for c in chunk_chapter(source)]
    near = [size for size in sizes if TARGET_CHUNK_WORDS - 150 <= size <= MAX_CHUNK_WORDS]
    assert len(near) >= 0.8 * len(sizes)
