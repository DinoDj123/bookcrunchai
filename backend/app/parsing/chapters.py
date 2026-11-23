"""Chapter detection: RawDocument blocks -> contract Chapters (design §5).

A2 contains only the "Part N" fallback (design §5 step 1.4, contract rule 5). TOC- and
pattern-based detection comes with A4.
"""

from __future__ import annotations

from collections.abc import Sequence

from .contract import Chapter, ChapterKind, Paragraph, count_words, fallback_part_title
from .document import Block, RawDocument

# A part closes at the first paragraph boundary at or after this many words.
TARGET_PART_WORDS = 5000
# A last part shorter than this is merged into the previous part.
MIN_LAST_PART_WORDS = 1000


def split_into_parts(
    blocks: Sequence[Block],
    target_words: int = TARGET_PART_WORDS,
    min_last_words: int = MIN_LAST_PART_WORDS,
) -> tuple[Chapter, ...]:
    """Split blocks into "Part N" chapters of about `target_words` words each.

    Every block becomes a paragraph (headings too: without detected chapters they are just text).
    """
    parts: list[list[Paragraph]] = []
    current: list[Paragraph] = []
    words = 0
    for block in blocks:
        current.append(Paragraph(block.text, block.page))
        words += count_words(block.text)
        if words >= target_words:
            parts.append(current)
            current, words = [], 0
    if current:
        if parts and words < min_last_words:
            parts[-1].extend(current)
        else:
            parts.append(current)

    return tuple(
        Chapter(
            position=n,
            title=fallback_part_title(n),
            kind=ChapterKind.CHAPTER,
            paragraphs=tuple(paragraphs),
        )
        for n, paragraphs in enumerate(parts, start=1)
    )


def build_chapters(doc: RawDocument) -> tuple[tuple[Chapter, ...], bool]:
    """Chapters of the book and whether they were really detected (False = "Part N" fallback).

    Until A4 this always returns the fallback.
    """
    return split_into_parts(doc.blocks), False
