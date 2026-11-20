"""parse_book contract, version 1.0.

This file is shared between the two tracks:
  - Track A (parser, Dino) produces a ParsedBook.
  - Track B (worker, API, database, Daris) consumes it.

Do not change this file without the agreement of both developers.
Every change bumps CONTRACT_VERSION and is described in docs/contracts/parse_book.md.
"""

from __future__ import annotations

import re
import unicodedata
from dataclasses import dataclass
from enum import StrEnum
from pathlib import Path
from typing import Any, Protocol

CONTRACT_VERSION = "1.0"

MAX_TITLE_LENGTH = 500
MAX_AUTHOR_LENGTH = 300


# --------------------------------------------------------------------------- enums


class FileFormat(StrEnum):
    EPUB = "epub"
    PDF = "pdf"


class ChapterKind(StrEnum):
    CHAPTER = "chapter"            # main text of the book
    FRONT_MATTER = "front_matter"  # preface, introduction, dedication, foreword
    BACK_MATTER = "back_matter"    # notes, appendix, afterword, index


class ParseErrorCode(StrEnum):
    UNSUPPORTED_FORMAT = "UNSUPPORTED_FORMAT"  # not an EPUB or PDF by file signature
    CORRUPT_FILE = "CORRUPT_FILE"              # file cannot be opened or read
    ENCRYPTED_FILE = "ENCRYPTED_FILE"          # DRM or password protected
    NO_TEXT_LAYER = "NO_TEXT_LAYER"            # scanned PDF, OCR is not supported
    EMPTY_BOOK = "EMPTY_BOOK"                  # no readable main text after clean-up


USER_MESSAGES: dict[ParseErrorCode, str] = {
    ParseErrorCode.UNSUPPORTED_FORMAT: "This file is not an EPUB or a PDF.",
    ParseErrorCode.CORRUPT_FILE: "The file is damaged and could not be read.",
    ParseErrorCode.ENCRYPTED_FILE: "The file is protected (DRM or password) and cannot be processed.",
    ParseErrorCode.NO_TEXT_LAYER: "This PDF contains scanned images without text. Scanned books are not supported yet.",
    ParseErrorCode.EMPTY_BOOK: "No readable text was found in this book.",
}


# --------------------------------------------------------------------------- errors


class ParseError(Exception):
    """The only exception parse_book() is allowed to raise on purpose.

    Any other exception is treated by the worker as a parser bug.
    `detail` is for logs and developers; `user_message` is shown in the UI.
    """

    def __init__(self, code: ParseErrorCode, detail: str = "") -> None:
        self.code = ParseErrorCode(code)
        self.detail = detail
        super().__init__(f"{self.code}: {detail}" if detail else str(self.code))

    @property
    def user_message(self) -> str:
        return USER_MESSAGES[self.code]


class ContractViolation(AssertionError):
    """Raised by validate_parsed_book() when a ParsedBook breaks the contract."""

    def __init__(self, problems: list[str]) -> None:
        self.problems = problems
        super().__init__("ParsedBook breaks the parse_book contract:\n  - " + "\n  - ".join(problems))


# --------------------------------------------------------------------------- data


@dataclass(frozen=True, slots=True)
class Paragraph:
    text: str               # one clean paragraph, see normalize_text()
    page: int | None = None  # 1-based page of the PDF where the paragraph starts; None for EPUB


@dataclass(frozen=True, slots=True)
class Chapter:
    position: int                    # 1, 2, 3 ... in reading order, over ALL kinds
    title: str
    kind: ChapterKind
    paragraphs: tuple[Paragraph, ...]

    @property
    def word_count(self) -> int:
        return sum(count_words(p.text) for p in self.paragraphs)


@dataclass(frozen=True, slots=True)
class ParsedBook:
    title: str
    author: str | None
    language: str | None        # lowercase ISO 639 code ("en", "it", "cnr"), None if unknown
    file_format: FileFormat
    chapters_detected: bool     # False when the book was split into "Part N" fallback parts
    chapters: tuple[Chapter, ...]

    @property
    def word_count(self) -> int:
        return sum(c.word_count for c in self.chapters)

    def to_dict(self) -> dict[str, Any]:
        """JSON-safe dictionary; used for test fixtures and debugging."""
        return {
            "contract_version": CONTRACT_VERSION,
            "title": self.title,
            "author": self.author,
            "language": self.language,
            "file_format": self.file_format.value,
            "chapters_detected": self.chapters_detected,
            "chapters": [
                {
                    "position": c.position,
                    "title": c.title,
                    "kind": c.kind.value,
                    "paragraphs": [{"text": p.text, "page": p.page} for p in c.paragraphs],
                }
                for c in self.chapters
            ],
        }

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> ParsedBook:
        return cls(
            title=data["title"],
            author=data["author"],
            language=data["language"],
            file_format=FileFormat(data["file_format"]),
            chapters_detected=data["chapters_detected"],
            chapters=tuple(
                Chapter(
                    position=c["position"],
                    title=c["title"],
                    kind=ChapterKind(c["kind"]),
                    paragraphs=tuple(Paragraph(text=p["text"], page=p["page"]) for p in c["paragraphs"]),
                )
                for c in data["chapters"]
            ),
        )


class ParseBookFn(Protocol):
    """Signature every parse_book implementation (real and fake) must have."""

    def __call__(self, file_path: Path) -> ParsedBook: ...


# --------------------------------------------------------------------------- shared helpers

_WHITESPACE = re.compile(r"\s+")
_INVISIBLE = dict.fromkeys(map(ord, "\u00ad\u200b\u200c\u200d\ufeff\x00"))  # soft hyphen, zero-width chars, BOM, NUL


def normalize_text(text: str) -> str:
    """The one way to clean text inside a paragraph or title.

    NFC Unicode, invisible characters removed, every whitespace run (including
    newlines and tabs) collapsed to one space, trimmed. Parsers must pass every
    paragraph and title through this function.
    """
    text = unicodedata.normalize("NFC", text).translate(_INVISIBLE)
    return _WHITESPACE.sub(" ", text).strip()


def count_words(text: str) -> int:
    """The one definition of a word in the whole project (parser, database, summaries)."""
    return len(text.split())


def fallback_part_title(n: int) -> str:
    """Title of the n-th part when chapters could not be detected."""
    return f"Part {n}"


# --------------------------------------------------------------------------- validation

_LANGUAGE = re.compile(r"^[a-z]{2,3}$")
_KIND_ORDER = {ChapterKind.FRONT_MATTER: 0, ChapterKind.CHAPTER: 1, ChapterKind.BACK_MATTER: 2}


def validate_parsed_book(book: ParsedBook) -> None:
    """Check every rule of the contract. Raises ContractViolation listing all problems.

    The worker calls this on every result before saving anything, and the parser
    tests call it on every test book.
    """
    problems: list[str] = []
    add = problems.append

    # book metadata
    if not book.title or book.title != normalize_text(book.title):
        add("title must be non-empty and normalized")
    elif len(book.title) > MAX_TITLE_LENGTH:
        add(f"title longer than {MAX_TITLE_LENGTH} characters")
    if book.author is not None:
        if not book.author or book.author != normalize_text(book.author):
            add("author must be None or a non-empty normalized string")
        elif len(book.author) > MAX_AUTHOR_LENGTH:
            add(f"author longer than {MAX_AUTHOR_LENGTH} characters")
    if book.language is not None and not _LANGUAGE.match(book.language):
        add(f"language must be a lowercase 2-3 letter code or None, got {book.language!r}")
    if not isinstance(book.file_format, FileFormat):
        add("file_format must be a FileFormat")
    if not isinstance(book.chapters, tuple):
        add("chapters must be a tuple")

    # chapters
    if not book.chapters:
        add("book has no chapters")
        raise ContractViolation(problems)

    positions = [c.position for c in book.chapters]
    if positions != list(range(1, len(book.chapters) + 1)):
        add(f"chapter positions must be 1..{len(book.chapters)} in order, got {positions}")

    if not any(c.kind == ChapterKind.CHAPTER for c in book.chapters):
        add("book needs at least one chapter of kind 'chapter'")

    kinds = [_KIND_ORDER.get(c.kind, -1) for c in book.chapters]
    if -1 in kinds:
        add("every chapter kind must be a ChapterKind")
    elif kinds != sorted(kinds):
        add("order must be front_matter* -> chapter+ -> back_matter*")

    if not book.chapters_detected:
        for c in book.chapters:
            if c.kind != ChapterKind.CHAPTER or c.title != fallback_part_title(c.position):
                add("when chapters_detected is False, every entry must be kind 'chapter' titled 'Part N'")
                break

    # paragraphs
    last_page = 0
    for c in book.chapters:
        where = f"chapter {c.position}"
        if not c.title or c.title != normalize_text(c.title):
            add(f"{where}: title must be non-empty and normalized")
        elif len(c.title) > MAX_TITLE_LENGTH:
            add(f"{where}: title longer than {MAX_TITLE_LENGTH} characters")
        if not isinstance(c.paragraphs, tuple):
            add(f"{where}: paragraphs must be a tuple")
        if not c.paragraphs:
            add(f"{where}: has no paragraphs (drop or merge empty headings)")
        for i, p in enumerate(c.paragraphs, start=1):
            if not p.text or p.text != normalize_text(p.text):
                add(f"{where}, paragraph {i}: text must be non-empty and normalized")
                break
            if book.file_format == FileFormat.EPUB and p.page is not None:
                add(f"{where}, paragraph {i}: page must be None for EPUB")
                break
            if book.file_format == FileFormat.PDF:
                if not isinstance(p.page, int) or p.page < 1:
                    add(f"{where}, paragraph {i}: page must be an integer >= 1 for PDF")
                    break
                if p.page < last_page:
                    add(f"{where}, paragraph {i}: page numbers must never decrease")
                    break
                last_page = p.page

    if problems:
        raise ContractViolation(problems)
