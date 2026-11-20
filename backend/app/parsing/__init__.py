"""Public entry point of the parsing package.

The worker imports ONLY from here:

    from app.parsing import get_parse_book, ParseError, validate_parsed_book

Which implementation is used is set by the environment variable BOOKCRUNCH_PARSER:
    fake  -> app.parsing.fake.fake_parse_book   (Track B uses it until the real parser is merged)
    real  -> app.parsing.parser.parse_book      (default)
"""

import os

from .contract import (
    CONTRACT_VERSION,
    Chapter,
    ChapterKind,
    ContractViolation,
    FileFormat,
    Paragraph,
    ParseBookFn,
    ParsedBook,
    ParseError,
    ParseErrorCode,
    count_words,
    normalize_text,
    validate_parsed_book,
)


def get_parse_book() -> ParseBookFn:
    impl = os.getenv("BOOKCRUNCH_PARSER", "real").strip().lower()
    if impl == "fake":
        from .fake import fake_parse_book

        return fake_parse_book
    if impl == "real":
        from .parser import parse_book

        return parse_book
    raise ValueError(f"BOOKCRUNCH_PARSER must be 'fake' or 'real', got {impl!r}")


__all__ = [
    "CONTRACT_VERSION",
    "Chapter",
    "ChapterKind",
    "ContractViolation",
    "FileFormat",
    "Paragraph",
    "ParseBookFn",
    "ParseError",
    "ParseErrorCode",
    "ParsedBook",
    "count_words",
    "get_parse_book",
    "normalize_text",
    "validate_parsed_book",
]
