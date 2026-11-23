"""Project Gutenberg licence header/footer detection (contract rule 10)."""

from __future__ import annotations

import re

# Element ids that Project Gutenberg uses for the licence header and footer in its EPUBs.
GUTENBERG_IDS: tuple[str, ...] = ("pg-header", "pg-footer")
# Class that marks boilerplate sections in newer Gutenberg files.
GUTENBERG_CLASS = "pg-boilerplate"

# "*** START OF THE PROJECT GUTENBERG EBOOK PRIDE AND PREJUDICE ***"
# "*** END OF THIS PROJECT GUTENBERG EBOOK ..." (older files)
_START = re.compile(r"\*{3}\s*START OF (?:THE|THIS) PROJECT GUTENBERG E-?BOOK", re.IGNORECASE)
_END = re.compile(r"\*{3}\s*END OF (?:THE|THIS) PROJECT GUTENBERG E-?BOOK", re.IGNORECASE)


def is_gutenberg_start(text: str) -> bool:
    """True if the text contains the '*** START OF THE PROJECT GUTENBERG EBOOK' marker."""
    return _START.search(text) is not None


def is_gutenberg_end(text: str) -> bool:
    """True if the text contains the '*** END OF THE PROJECT GUTENBERG EBOOK' marker."""
    return _END.search(text) is not None
