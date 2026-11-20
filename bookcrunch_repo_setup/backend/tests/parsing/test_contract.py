"""Tests of the parse_book contract itself and of the fake parser.

Track A adds its own tests for the real parser in other files of this folder and
calls validate_parsed_book() on every test book.
"""

from dataclasses import replace
from pathlib import Path

import pytest

from app.parsing import (
    Chapter,
    ChapterKind,
    ContractViolation,
    FileFormat,
    Paragraph,
    ParsedBook,
    ParseError,
    ParseErrorCode,
    count_words,
    get_parse_book,
    normalize_text,
    validate_parsed_book,
)
from app.parsing.contract import USER_MESSAGES
from app.parsing.fake import fake_parse_book


@pytest.fixture
def book_file(tmp_path: Path) -> Path:
    path = tmp_path / "book.epub"
    path.write_bytes(b"PK\x03\x04 not a real epub")
    return path


@pytest.fixture
def book(book_file: Path) -> ParsedBook:
    return fake_parse_book(book_file)


# --------------------------------------------------------------------------- fake parser


def test_fake_epub_book_passes_the_contract(book: ParsedBook) -> None:
    validate_parsed_book(book)
    assert book.file_format == FileFormat.EPUB


def test_fake_pdf_book_passes_the_contract(book_file: Path, monkeypatch) -> None:
    monkeypatch.setenv("FAKE_PARSER_FORMAT", "pdf")
    book = fake_parse_book(book_file)
    validate_parsed_book(book)
    assert all(p.page is not None for c in book.chapters for p in c.paragraphs)


def test_fake_parser_raises_requested_error(book_file: Path, monkeypatch) -> None:
    monkeypatch.setenv("FAKE_PARSER_ERROR", "NO_TEXT_LAYER")
    with pytest.raises(ParseError) as exc:
        fake_parse_book(book_file)
    assert exc.value.code == ParseErrorCode.NO_TEXT_LAYER


def test_missing_file_is_a_parse_error(tmp_path: Path) -> None:
    with pytest.raises(ParseError) as exc:
        fake_parse_book(tmp_path / "missing.epub")
    assert exc.value.code == ParseErrorCode.CORRUPT_FILE


def test_get_parse_book_selects_implementation(monkeypatch) -> None:
    monkeypatch.setenv("BOOKCRUNCH_PARSER", "fake")
    assert get_parse_book() is fake_parse_book
    monkeypatch.setenv("BOOKCRUNCH_PARSER", "something-else")
    with pytest.raises(ValueError):
        get_parse_book()


# --------------------------------------------------------------------------- data and helpers


def test_round_trip_through_dict(book: ParsedBook) -> None:
    assert ParsedBook.from_dict(book.to_dict()) == book


def test_word_counts_are_consistent(book: ParsedBook) -> None:
    assert book.word_count == sum(c.word_count for c in book.chapters)
    assert count_words("  two\nwords ") == 2


def test_normalize_text() -> None:
    assert normalize_text("  Caf\u0065\u0301 \n\t con\u00adtent\u200b  ") == "Café content"


def test_every_error_code_has_a_user_message() -> None:
    assert set(USER_MESSAGES) == set(ParseErrorCode)


# --------------------------------------------------------------------------- validator catches violations


def _chapter(position: int, kind: ChapterKind = ChapterKind.CHAPTER, title: str | None = None,
             paragraphs: tuple[Paragraph, ...] = (Paragraph("Some text."),)) -> Chapter:
    return Chapter(position=position, title=title or f"Chapter {position}", kind=kind, paragraphs=paragraphs)


def _book(*chapters: Chapter, **kwargs) -> ParsedBook:
    values = dict(title="T", author=None, language="en", file_format=FileFormat.EPUB,
                  chapters_detected=True, chapters=tuple(chapters))
    values.update(kwargs)
    return ParsedBook(**values)


@pytest.mark.parametrize(
    "bad_book",
    [
        pytest.param(_book(), id="no chapters"),
        pytest.param(_book(_chapter(1), _chapter(3)), id="gap in positions"),
        pytest.param(_book(_chapter(1, ChapterKind.FRONT_MATTER)), id="only front matter"),
        pytest.param(_book(_chapter(1), _chapter(2, ChapterKind.FRONT_MATTER)), id="front matter after chapter"),
        pytest.param(_book(_chapter(1, paragraphs=())), id="empty chapter"),
        pytest.param(_book(_chapter(1, paragraphs=(Paragraph("two\nlines"),))), id="paragraph not normalized"),
        pytest.param(_book(_chapter(1, paragraphs=(Paragraph("Text.", page=4),))), id="page on EPUB"),
        pytest.param(_book(_chapter(1), file_format=FileFormat.PDF), id="missing page on PDF"),
        pytest.param(_book(_chapter(1, paragraphs=(Paragraph("A.", 5), Paragraph("B.", 4))),
                           file_format=FileFormat.PDF), id="pages going back"),
        pytest.param(_book(_chapter(1), title="  "), id="blank title"),
        pytest.param(_book(_chapter(1), language="en-US"), id="language not normalized"),
        pytest.param(_book(_chapter(1, title="Chapter 1"), chapters_detected=False), id="fallback without Part N"),
    ],
)
def test_validator_rejects(bad_book: ParsedBook) -> None:
    with pytest.raises(ContractViolation):
        validate_parsed_book(bad_book)


def test_validator_accepts_fallback_parts() -> None:
    book = _book(_chapter(1, title="Part 1"), _chapter(2, title="Part 2"), chapters_detected=False)
    validate_parsed_book(book)


def test_validator_lists_all_problems(book: ParsedBook) -> None:
    broken = replace(book, title="", language="EN")
    with pytest.raises(ContractViolation) as exc:
        validate_parsed_book(broken)
    assert len(exc.value.problems) == 2
