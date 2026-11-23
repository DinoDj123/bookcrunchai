"""Real parser. Owned by Track A (Dino).

Must follow docs/contracts/parse_book.md.
Design: docs/superpowers/specs/2026-11-23-parser-design.md.

    file -> detect format by signature -> epub.read_epub / pdf.read_pdf -> RawDocument
         -> chapters.build_chapters -> ParsedBook
"""

from __future__ import annotations

import re
from pathlib import Path

from . import chapters, epub, pdf
from .contract import (
    MAX_AUTHOR_LENGTH,
    MAX_TITLE_LENGTH,
    FileFormat,
    ParsedBook,
    ParseError,
    ParseErrorCode,
)
from .document import RawDocument
from .text import clean_title, cut_at_word, language_code

_SIGNATURES: tuple[tuple[bytes, FileFormat], ...] = (
    (b"PK\x03\x04", FileFormat.EPUB),
    (b"%PDF", FileFormat.PDF),
)
_FILE_NAME_SEPARATORS = re.compile(r"[_\-.]+")
_UNTITLED = "Untitled"


def parse_book(file_path: Path) -> ParsedBook:
    """Parse an EPUB or text-based PDF into a ParsedBook.

    - Detect the format by file signature, not by extension (stored files may have none).
    - Return a ParsedBook that passes validate_parsed_book().
    - Raise only ParseError for expected failures.
    - No database, no network, no writing files, deterministic output.
    """
    path = Path(file_path)
    file_format = _detect_format(path)
    doc = epub.read_epub(path) if file_format is FileFormat.EPUB else pdf.read_pdf(path)
    if not doc.blocks:
        raise ParseError(ParseErrorCode.EMPTY_BOOK, "no text left after clean-up")

    book_chapters, detected = chapters.build_chapters(doc)
    return ParsedBook(
        title=_book_title(doc, path),
        author=_author(doc),
        language=language_code(doc.language),
        file_format=doc.file_format,
        chapters_detected=detected,
        chapters=book_chapters,
    )


def _detect_format(path: Path) -> FileFormat:
    try:
        with path.open("rb") as file:
            head = file.read(8)
    except OSError as exc:
        raise ParseError(ParseErrorCode.CORRUPT_FILE, f"cannot read file: {exc}") from exc
    for signature, file_format in _SIGNATURES:
        if head.startswith(signature):
            return file_format
    raise ParseError(ParseErrorCode.UNSUPPORTED_FORMAT, f"unknown file signature {head[:4]!r}")


def _book_title(doc: RawDocument, path: Path) -> str:
    """Metadata title, else the cleaned file name (contract 1.0 wording, Decision D3)."""
    title = clean_title(doc.title)
    if title is None:
        title = clean_title(_FILE_NAME_SEPARATORS.sub(" ", path.stem))
    return cut_at_word(title or _UNTITLED, MAX_TITLE_LENGTH)


def _author(doc: RawDocument) -> str | None:
    author = clean_title(doc.author)
    return cut_at_word(author, MAX_AUTHOR_LENGTH) if author else None
