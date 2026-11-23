from pathlib import Path

import pytest

from app.parsing.contract import (
    FileFormat,
    ParseError,
    ParseErrorCode,
    count_words,
    validate_parsed_book,
)
from app.parsing.parser import parse_book

from .builders import make_epub, paragraphs
from .test_epub import GUTENBERG_FOOTER, GUTENBERG_HEADER

BOOKS = Path(__file__).parent.parent / "fixtures" / "books"


def test_synthetic_epub_end_to_end(tmp_path: Path) -> None:
    path = make_epub(
        tmp_path,
        title="A Small Book.",
        author="  Ann   Writer ",
        language="en-US",
        docs=[("a.xhtml", "<h2>Chapter 1</h2>" + paragraphs(5)), ("b.xhtml", paragraphs(5))],
    )
    book = parse_book(path)
    validate_parsed_book(book)
    assert (book.title, book.author, book.language) == ("A Small Book", "Ann Writer", "en")
    assert book.file_format is FileFormat.EPUB
    assert book.chapters_detected is False
    assert [c.title for c in book.chapters] == ["Part 1"]
    assert book.word_count == 2 + 10 * 10


def test_title_falls_back_to_file_name(tmp_path: Path) -> None:
    path = make_epub(
        tmp_path,
        title=None,
        author=None,
        language="not a language",
        docs=[("a.xhtml", "<p>x</p>")],
        name="my_great-book.epub",
    )
    book = parse_book(path)
    validate_parsed_book(book)
    assert (book.title, book.author, book.language) == ("my great book", None, None)


def test_long_title_and_author_are_cut_at_a_word(tmp_path: Path) -> None:
    path = make_epub(
        tmp_path, title="word " * 200, author="name " * 100, docs=[("a.xhtml", "<p>x</p>")]
    )
    book = parse_book(path)
    validate_parsed_book(book)
    assert len(book.title) <= 500 and book.title.endswith("word")
    assert len(book.author or "") <= 300 and (book.author or "").endswith("name")


def test_only_boilerplate_is_empty_book(tmp_path: Path) -> None:
    path = make_epub(tmp_path, docs=[("a.xhtml", GUTENBERG_HEADER + GUTENBERG_FOOTER)])
    with pytest.raises(ParseError) as info:
        parse_book(path)
    assert info.value.code is ParseErrorCode.EMPTY_BOOK


@pytest.mark.parametrize(
    ("name", "title", "author"),
    [
        ("pride-and-prejudice.epub", "Pride and Prejudice", "Jane Austen"),
        ("meditations.epub", "Meditations", "Emperor of Rome Marcus Aurelius"),
    ],
)
def test_real_epub_smoke(name: str, title: str, author: str) -> None:
    book = parse_book(BOOKS / name)
    validate_parsed_book(book)
    assert (book.title, book.author, book.language) == (title, author, "en")
    assert book.file_format is FileFormat.EPUB
    for chapter in book.chapters:
        for paragraph in chapter.paragraphs:
            assert "Project Gutenberg" not in paragraph.text
            assert "PROJECT GUTENBERG" not in paragraph.text
    assert book.word_count == sum(count_words(p.text) for c in book.chapters for p in c.paragraphs)


@pytest.mark.parametrize("name", ["pride-and-prejudice.epub", "meditations.epub"])
def test_real_epub_is_deterministic(name: str) -> None:
    assert parse_book(BOOKS / name).to_dict() == parse_book(BOOKS / name).to_dict()
