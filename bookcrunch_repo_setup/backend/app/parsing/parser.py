"""Real parser. Owned by Track A (Dino).

Must follow docs/contracts/parse_book.md. Helper modules (epub.py, pdf.py,
chapters.py, cleanup.py ...) live next to this file and are also owned by Track A.
"""

from pathlib import Path

from .contract import ParsedBook


def parse_book(file_path: Path) -> ParsedBook:
    """Parse an EPUB or text-based PDF into a ParsedBook.

    - Detect the format by file signature, not by extension (stored files may have none).
    - Return a ParsedBook that passes validate_parsed_book().
    - Raise only ParseError for expected failures.
    - No database, no network, no writing files, deterministic output.
    """
    raise NotImplementedError("Track A: implement parse_book (see docs/contracts/parse_book.md)")
