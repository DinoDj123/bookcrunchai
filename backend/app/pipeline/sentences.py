"""split_sentences(): a simple sentence splitter for cutting long paragraphs (contract rule 3).

It only chooses where a paragraph longer than 500 words is cut, so a wrong boundary moves a chunk
boundary and loses nothing. The rule (docs/contracts/pipeline.md, section 3):

    a boundary is ".", "!", "?" or "…", optionally followed by closing quotes or brackets,
    then one space, then an upper-case letter, an opening quote or a dash.

No boundary after a known abbreviation ("Mr. Darcy", "npr. Marko"), after an initial
("J. Comyns Carr") or after a Roman numeral when the next word starts with a lower-case letter
("Luj XIV. – kralj"). The space at a boundary is the only character removed, so
" ".join(split_sentences(text)) == text for every text.
"""

from __future__ import annotations

import re

# English and Montenegrin abbreviations that are usually followed by a capital or a name.
# Matched case-sensitively: "dr." and "Dr." are both listed, "No." does not stop "said no. Then".
ABBREVIATIONS = frozenset(
    {
        # English
        "Mr.", "Mrs.", "Ms.", "Dr.", "St.", "Jr.", "Sr.", "Prof.", "Rev.", "Gen.", "Col.",
        "Capt.", "Lt.", "Mt.", "vs.", "etc.", "e.g.", "i.e.", "No.", "Vol.", "Ch.",
        # Montenegrin
        "npr.", "tj.", "itd.", "sl.", "str.", "br.", "dr.", "g.", "gđa.", "gđica.", "god.", "v.",
    }
)  # fmt: skip

_OPENING_QUOTES = "\"'“‘«„"
_DASHES = "-–—"

# Sentence-ending mark, optional closing quotes or brackets, then exactly one space. One space,
# not \s+, so that removing it and joining with " " gives the original text back.
_BOUNDARY = re.compile(r"[.!?…][\"'”’»)\]]* ")
_ROMAN = re.compile(r"[IVXLCDM]+\.")
_INITIALS = re.compile(r"(?:[^\W\d_]\.)+")  # "J." or "J.R.R." (checked for upper case below)
_NEXT_LETTER = re.compile(r"[\"'“‘«„\-–— ]*(\w)")


def split_sentences(text: str) -> list[str]:
    """The sentences of `text`, in order; " ".join(result) == text."""
    sentences: list[str] = []
    start = 0
    for match in _BOUNDARY.finditer(text):
        after = match.end()  # first character of the next sentence
        if after >= len(text) or not _starts_sentence(text[after]):
            continue
        if match.group() == ". " and _keeps_sentence(text, start, match.start(), after):
            continue
        sentences.append(text[start : after - 1])
        start = after
    sentences.append(text[start:])
    return sentences


def _starts_sentence(char: str) -> bool:
    return char.isupper() or char in _OPENING_QUOTES or char in _DASHES


def _keeps_sentence(text: str, start: int, dot: int, after: int) -> bool:
    """True when the full stop at `dot` ends an abbreviation, an initial or a Roman numeral
    followed by a lower-case word, so the sentence goes on."""
    word_start = max(text.rfind(" ", start, dot) + 1, start)
    word = text[word_start : dot + 1].lstrip(_OPENING_QUOTES + _DASHES + "([")
    if word in ABBREVIATIONS:
        return True
    if _INITIALS.fullmatch(word) and word.isupper():
        return True
    if _ROMAN.fullmatch(word):
        next_letter = _NEXT_LETTER.match(text, after)
        return next_letter is not None and next_letter.group(1).islower()
    return False
