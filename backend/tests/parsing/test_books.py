"""Tests on the three real books (design §7) and the chapter snapshots in fixtures/books/expected.

The snapshot holds title, author, language, format and, per entry, position / kind / title /
word count (no paragraph text, to keep the repo small). A change in chapter detection shows up as
a diff of these files. Regenerate them with `UPDATE_SNAPSHOTS=1 pytest tests/parsing/test_books.py`.
"""

from __future__ import annotations

import json
import os
import re
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import pytest

from app.parsing.contract import ChapterKind, FileFormat, ParsedBook, validate_parsed_book
from app.parsing.parser import parse_book

BOOKS = Path(__file__).parent.parent / "fixtures" / "books"
EXPECTED = BOOKS / "expected"

# file name -> number of `chapter` entries (contract §7, measured in A2/A4)
CHAPTER_COUNTS = {
    "pride-and-prejudice.epub": 61,
    "the-prince.pdf": 26,
    "meditations.epub": 12,
}
PARSE_SECONDS_LIMIT = 20.0  # contract: 300 pages < 60 s; generous margin for CI

_ROMAN_ONLY = re.compile(r"M{0,4}(?:CM|CD|D?C{0,3})(?:XC|XL|L?X{0,3})(?:IX|IV|V?I{0,3})\.?")
_DIGITS_ONLY = re.compile(r"[\d\s.,-]+")


@dataclass(frozen=True)
class Parsed:
    name: str
    book: ParsedBook
    seconds: float


@pytest.fixture(scope="module", params=sorted(CHAPTER_COUNTS))
def parsed(request: pytest.FixtureRequest) -> Parsed:
    name: str = request.param
    start = time.perf_counter()
    book = parse_book(BOOKS / name)
    return Parsed(name, book, time.perf_counter() - start)


def paragraph_texts(book: ParsedBook) -> list[str]:
    return [p.text for c in book.chapters for p in c.paragraphs]


def titles_of(book: ParsedBook, kind: ChapterKind) -> list[str]:
    return [c.title for c in book.chapters if c.kind is kind]


def position_of(book: ParsedBook, title_part: str) -> int:
    matches = [c.position for c in book.chapters if title_part.lower() in c.title.lower()]
    assert matches, f"no entry with {title_part!r} in its title"
    return matches[0]


# --------------------------------------------------------------------------- all books


def test_contract_is_met(parsed: Parsed) -> None:
    validate_parsed_book(parsed.book)


def test_chapter_count(parsed: Parsed) -> None:
    chapters = [c for c in parsed.book.chapters if c.kind is ChapterKind.CHAPTER]
    assert len(chapters) == CHAPTER_COUNTS[parsed.name]


def test_chapters_detected(parsed: Parsed) -> None:
    assert parsed.book.chapters_detected is True


def test_no_boilerplate_or_leftovers_in_text(parsed: Parsed) -> None:
    for text in paragraph_texts(parsed.book):
        assert "Project Gutenberg" not in text
        assert "Planet eBook" not in text
        assert "\u00ad" not in text
        assert "[*]" not in text


def test_no_paragraph_is_only_a_number(parsed: Parsed) -> None:
    for text in paragraph_texts(parsed.book):
        assert not _DIGITS_ONLY.fullmatch(text), f"digits-only paragraph {text!r}"
        assert not _ROMAN_ONLY.fullmatch(text), f"roman-numeral paragraph {text!r}"


def test_pages(parsed: Parsed) -> None:
    pages = [p.page for c in parsed.book.chapters for p in c.paragraphs]
    if parsed.book.file_format is FileFormat.EPUB:
        assert all(page is None for page in pages)
    else:
        assert all(isinstance(page, int) and page >= 1 for page in pages)
        assert pages == sorted(pages)  # type: ignore[type-var]


def test_parse_is_deterministic(parsed: Parsed) -> None:
    again = parse_book(BOOKS / parsed.name)
    assert again.to_dict() == parsed.book.to_dict()


def test_parses_fast_enough(parsed: Parsed) -> None:
    assert parsed.seconds < PARSE_SECONDS_LIMIT


# --------------------------------------------------------------------------- per book


def test_pride_and_prejudice_structure() -> None:
    book = parse_book(BOOKS / "pride-and-prejudice.epub")
    assert (book.title, book.author, book.language) == ("Pride and Prejudice", "Jane Austen", "en")
    first = next(c for c in book.chapters if c.kind is ChapterKind.CHAPTER)
    assert "chapter i" in first.title.lower()
    for chapter in book.chapters:
        if chapter.kind is not ChapterKind.CHAPTER:
            assert "licen" not in chapter.title.lower()
            assert not any("licen" in p.text.lower() for p in chapter.paragraphs)
    assert not titles_of(book, ChapterKind.BACK_MATTER)


def test_prince_introduction_and_dedication_are_front_matter() -> None:
    book = parse_book(BOOKS / "the-prince.pdf")
    assert (book.title, book.author) == ("The Prince", "Nicolo Machiavelli")
    first_chapter = position_of(book, "CHAPTER I")
    by_position = {c.position: c for c in book.chapters}
    for part in ("INTRODUCTION", "DEDICATION"):
        position = position_of(book, part)
        assert by_position[position].kind is ChapterKind.FRONT_MATTER
        assert position < first_chapter


def test_meditations_introduction_and_notes() -> None:
    book = parse_book(BOOKS / "meditations.epub")
    assert (book.title, book.author) == ("Meditations", "Marcus Aurelius")
    by_title = {c.title.upper(): c for c in book.chapters}
    assert by_title["INTRODUCTION"].kind is ChapterKind.FRONT_MATTER
    assert by_title["NOTES"].kind is ChapterKind.BACK_MATTER


# --------------------------------------------------------------------------- snapshots


def summary(book: ParsedBook) -> dict[str, Any]:
    """The part of the parse result that is written to expected/*.json (no paragraph text)."""
    return {
        "title": book.title,
        "author": book.author,
        "language": book.language,
        "file_format": book.file_format.value,
        "chapters_detected": book.chapters_detected,
        "chapters": [
            {
                "position": c.position,
                "kind": c.kind.value,
                "title": c.title,
                "word_count": c.word_count,
            }
            for c in book.chapters
        ],
    }


def test_matches_snapshot(parsed: Parsed) -> None:
    path = EXPECTED / (Path(parsed.name).stem + ".json")
    current = summary(parsed.book)
    if os.environ.get("UPDATE_SNAPSHOTS") == "1":
        path.parent.mkdir(exist_ok=True)
        path.write_text(
            json.dumps(current, indent=2, ensure_ascii=False) + "\n", encoding="utf-8", newline="\n"
        )
        return
    if not path.exists():
        pytest.fail(
            f"snapshot {path.name} is missing; generate it with "
            "UPDATE_SNAPSHOTS=1 pytest tests/parsing/test_books.py -k snapshot"
        )
    assert current == json.loads(path.read_text(encoding="utf-8"))
