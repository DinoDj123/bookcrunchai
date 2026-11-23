"""PDF reader: file -> RawDocument (design §4). Implemented in A3.

Only the entry point exists for now, so parser.py can already route "%PDF" files here.
"""

from __future__ import annotations

from pathlib import Path

from .document import RawDocument


def read_pdf(path: Path) -> RawDocument:
    """Read a text-based PDF. Raises ParseError(CORRUPT_FILE | ENCRYPTED_FILE | NO_TEXT_LAYER)."""
    raise NotImplementedError("PDF reader comes with A3 (feat/parser-pdf)")
