"""Fake parser for Track B (worker, database, API, UI).

It honours the contract exactly, so the worker can be built and tested before the
real parser exists. It never reads the content of the file.

Environment variables for testing the worker:
    FAKE_PARSER_ERROR=NO_TEXT_LAYER   -> raise ParseError with that code
    FAKE_PARSER_DELAY=3               -> sleep 3 seconds (to see progress in the UI)
    FAKE_PARSER_FORMAT=pdf            -> return a PDF-shaped book with page numbers
"""

import os
import time
from pathlib import Path

from .contract import (
    Chapter,
    ChapterKind,
    FileFormat,
    Paragraph,
    ParsedBook,
    ParseError,
    ParseErrorCode,
)

_TEXT = (
    "This is paragraph {n} of {title}. It is fake text produced by the fake parser "
    "so that the worker, the database and the user interface can be built before "
    "the real parser is ready."
)


def fake_parse_book(file_path: Path) -> ParsedBook:
    file_path = Path(file_path)
    if not file_path.is_file():
        raise ParseError(ParseErrorCode.CORRUPT_FILE, f"file not found: {file_path}")

    delay = float(os.getenv("FAKE_PARSER_DELAY", "0"))
    if delay > 0:
        time.sleep(delay)

    error = os.getenv("FAKE_PARSER_ERROR", "").strip()
    if error:
        raise ParseError(ParseErrorCode(error), "raised by the fake parser on request")

    file_format = FileFormat(os.getenv("FAKE_PARSER_FORMAT", "epub").strip().lower())
    page = 1

    def paragraphs(title: str, count: int) -> tuple[Paragraph, ...]:
        nonlocal page
        result = []
        for n in range(1, count + 1):
            result.append(
                Paragraph(
                    text=_TEXT.format(n=n, title=title),
                    page=page if file_format == FileFormat.PDF else None,
                )
            )
            if file_format == FileFormat.PDF and n % 2 == 0:
                page += 1
        return tuple(result)

    entries = [
        ("Preface", ChapterKind.FRONT_MATTER, 2),
        ("Chapter 1. The Beginning", ChapterKind.CHAPTER, 6),
        ("Chapter 2. The Middle", ChapterKind.CHAPTER, 8),
        ("Chapter 3. The End", ChapterKind.CHAPTER, 5),
        ("Notes", ChapterKind.BACK_MATTER, 2),
    ]
    chapters = tuple(
        Chapter(position=i, title=title, kind=kind, paragraphs=paragraphs(title, count))
        for i, (title, kind, count) in enumerate(entries, start=1)
    )
    return ParsedBook(
        title="Fake Book",
        author="Fake Author",
        language="en",
        file_format=file_format,
        chapters_detected=True,
        chapters=chapters,
    )
