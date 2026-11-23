"""Text helpers shared by the EPUB and PDF readers and by chapter detection."""

from __future__ import annotations

import re

from .contract import normalize_text

# Soft hyphen (U+00AD) at the end of a line is always a break inside one word.
_SOFT_HYPHEN_BREAK = re.compile(r"­[ \t]*\r?\n[ \t]*")
# A hard hyphen at the end of a line followed by a lowercase letter is treated as a word split
# ("con-\ntent" -> "content"). Real compounds split exactly there lose their hyphen; this is a
# documented limitation. An uppercase letter ("Anglo-\nSaxon") keeps the hyphen.
_HARD_HYPHEN_LOWER = re.compile(r"(?<=\w)-[ \t]*\r?\n[ \t]*(?=[a-z])")
_HARD_HYPHEN_OTHER = re.compile(r"(?<=\w)-[ \t]*\r?\n[ \t]*(?=\w)")

# Footnote markers such as "[*]", "[12]", "[a]" (Gutenberg and Planet eBook style).
_FOOTNOTE_MARKER = re.compile(r"\s*\[(?:\*+|\d{1,3}|[a-z])\]")

# BCP 47 tag: primary language subtag of 2-3 letters, optionally followed by subtags.
_LANGUAGE_TAG = re.compile(r"^([A-Za-z]{2,3})(?:[-_][A-Za-z0-9]{1,8})*$")

# A title made only of punctuation, digits-free symbols or whitespace is junk.
_HAS_LETTER_OR_DIGIT = re.compile(r"\w")


def join_hyphenated(text: str) -> str:
    """Join words split across a line break by a soft or hard hyphen."""
    text = _SOFT_HYPHEN_BREAK.sub("", text)
    text = _HARD_HYPHEN_LOWER.sub("", text)
    return _HARD_HYPHEN_OTHER.sub("-", text)


def strip_footnote_markers(text: str) -> str:
    """Remove inline footnote markers like "[*]" and "[12]"."""
    return _FOOTNOTE_MARKER.sub("", text)


def language_code(raw: str | None) -> str | None:
    """Lowercase ISO 639 code from a language tag: "en-US" -> "en", "cnr-ME" -> "cnr"."""
    if raw is None:
        return None
    match = _LANGUAGE_TAG.match(raw.strip())
    if match is None:
        return None
    return match.group(1).lower()


def clean_title(raw: str | None) -> str | None:
    """Normalized title without footnote markers and trailing ".", or None if nothing useful."""
    if raw is None:
        return None
    title = normalize_text(strip_footnote_markers(normalize_text(raw)))
    title = title.rstrip(".").rstrip()
    if not title or not _HAS_LETTER_OR_DIGIT.search(title):
        return None
    return title


def cut_at_word(text: str, limit: int) -> str:
    """Cut text to at most `limit` characters, at a word boundary when possible."""
    if len(text) <= limit:
        return text
    cut = text[: limit + 1]
    space = cut.rfind(" ")
    if space > 0:
        return cut[:space].rstrip()
    return text[:limit]
