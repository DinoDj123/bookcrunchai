from app.parsing.chapters import build_chapters, split_into_parts
from app.parsing.contract import (
    ChapterKind,
    FileFormat,
    ParsedBook,
    count_words,
    validate_parsed_book,
)
from app.parsing.document import Block, RawDocument


def blocks(*word_counts: int) -> list[Block]:
    return [Block(" ".join(["w"] * n), None, None) for n in word_counts]


def sizes(parts) -> list[list[int]]:  # type: ignore[no-untyped-def]
    return [[count_words(p.text) for p in c.paragraphs] for c in parts]


def test_part_closes_at_first_boundary_at_or_after_target() -> None:
    parts = split_into_parts(blocks(40, 40, 30, 60, 50, 50), target_words=100, min_last_words=20)
    assert sizes(parts) == [[40, 40, 30], [60, 50], [50]]


def test_exact_target_closes_the_part() -> None:
    parts = split_into_parts(blocks(50, 50, 50, 50), target_words=100, min_last_words=10)
    assert sizes(parts) == [[50, 50], [50, 50]]


def test_short_last_part_is_merged() -> None:
    parts = split_into_parts(blocks(100, 100, 19), target_words=100, min_last_words=20)
    assert sizes(parts) == [[100], [100, 19]]


def test_last_part_at_minimum_is_kept() -> None:
    parts = split_into_parts(blocks(100, 20), target_words=100, min_last_words=20)
    assert sizes(parts) == [[100], [20]]


def test_short_book_is_one_part() -> None:
    parts = split_into_parts(blocks(5, 3), target_words=100, min_last_words=20)
    assert sizes(parts) == [[5, 3]]


def test_titles_positions_kinds_and_pages() -> None:
    raw = [Block(" ".join(["w"] * 3000), page, None) for page in (1, 2, 3, 4, 5)]
    parts = split_into_parts(raw)
    assert [(c.position, c.title, c.kind) for c in parts] == [
        (1, "Part 1", ChapterKind.CHAPTER),
        (2, "Part 2", ChapterKind.CHAPTER),
        (3, "Part 3", ChapterKind.CHAPTER),
    ]
    assert [[p.page for p in c.paragraphs] for c in parts] == [[1, 2], [3, 4], [5]]


def test_headings_stay_as_paragraphs() -> None:
    raw = [Block("Chapter 1", None, 2), Block("Text.", None, None)]
    parts = split_into_parts(raw)
    assert [p.text for p in parts[0].paragraphs] == ["Chapter 1", "Text."]


def test_build_chapters_is_fallback_until_a4() -> None:
    doc = RawDocument(
        title="T",
        author=None,
        language=None,
        file_format=FileFormat.EPUB,
        blocks=tuple(blocks(4000, 4000, 4000)),
        toc=(),
    )
    chapters, detected = build_chapters(doc)
    assert detected is False
    validate_parsed_book(
        ParsedBook(
            title="T",
            author=None,
            language=None,
            file_format=FileFormat.EPUB,
            chapters_detected=detected,
            chapters=chapters,
        )
    )
