"""chunk_chapter(): splits one chapter into chunks of about 350 words, at most 500 (FR-006).

Rules: docs/contracts/pipeline.md, section 3. Pure and deterministic: no I/O, no global state.

1. Paragraphs are grouped in order and joined with "\n\n". A chunk is closed before the next
   paragraph when that paragraph would take it over 500 words, or when the chunk has already
   reached 350 words (from there every further paragraph only moves it away from the target).
2. A paragraph over 500 words is split into sentences (sentences.py), the sentences are grouped
   into pieces of at most 500 words and every piece is a chunk of its own. A sentence over 500
   words is split between words.
3. A short last chunk (under 100 words) is merged into the one before it if the sum stays within
   500 words.
"""

from __future__ import annotations

from dataclasses import dataclass, field

from app.parsing import Chapter, count_words

from .contract import (
    MAX_CHUNK_WORDS,
    PARAGRAPH_SEPARATOR,
    TARGET_CHUNK_WORDS,
    ChunkDraft,
    estimate_tokens,
)
from .sentences import split_sentences

MIN_LAST_CHUNK_WORDS = 100  # contract rule 5


@dataclass
class _Draft:
    page: int | None  # page of the first paragraph
    whole: bool = True  # False for a piece of a split paragraph
    parts: list[str] = field(default_factory=list)
    words: int = 0

    def add(self, text: str, words: int) -> None:
        self.parts.append(text)
        self.words += words


def chunk_chapter(chapter: Chapter) -> tuple[ChunkDraft, ...]:
    drafts: list[_Draft] = []
    current: _Draft | None = None
    for paragraph in chapter.paragraphs:
        words = count_words(paragraph.text)
        if words > MAX_CHUNK_WORDS:
            if current is not None:
                drafts.append(current)
                current = None
            for piece in split_paragraph(paragraph.text):
                draft = _Draft(page=paragraph.page, whole=False)
                draft.add(piece, count_words(piece))
                drafts.append(draft)
            continue
        if current is not None and (
            current.words + words > MAX_CHUNK_WORDS or current.words >= TARGET_CHUNK_WORDS
        ):
            drafts.append(current)
            current = None
        if current is None:
            current = _Draft(page=paragraph.page)
        current.add(paragraph.text, words)
    if current is not None:
        drafts.append(current)

    _merge_short_last(drafts)
    return tuple(_chunk(position, draft) for position, draft in enumerate(drafts, start=1))


def split_paragraph(text: str) -> list[str]:
    """Pieces of at most MAX_CHUNK_WORDS words, cut between sentences (or, inside a sentence
    longer than that, between words). " ".join(pieces) == text."""
    units: list[str] = []
    for sentence in split_sentences(text):
        words = sentence.split(" ")
        if len(words) <= MAX_CHUNK_WORDS:
            units.append(sentence)
        else:
            units.extend(
                " ".join(words[start : start + MAX_CHUNK_WORDS])
                for start in range(0, len(words), MAX_CHUNK_WORDS)
            )

    pieces: list[str] = []
    current: list[str] = []
    current_words = 0
    for unit in units:
        words = count_words(unit)
        if current and current_words + words > MAX_CHUNK_WORDS:
            pieces.append(" ".join(current))
            current, current_words = [], 0
        current.append(unit)
        current_words += words
    pieces.append(" ".join(current))
    return pieces


def _merge_short_last(drafts: list[_Draft]) -> None:
    """Merges a last chunk of whole paragraphs under MIN_LAST_CHUNK_WORDS into the one before.

    A last chunk that is a piece of a split paragraph is never merged: the piece before it is
    from the same paragraph and was closed because the two together are over the maximum."""
    if len(drafts) < 2:
        return
    last, before = drafts[-1], drafts[-2]
    if (
        last.whole
        and last.words < MIN_LAST_CHUNK_WORDS
        and before.words + last.words <= MAX_CHUNK_WORDS
    ):
        before.parts.extend(last.parts)
        before.words += last.words
        drafts.pop()


def _chunk(position: int, draft: _Draft) -> ChunkDraft:
    text = PARAGRAPH_SEPARATOR.join(draft.parts)
    return ChunkDraft(
        position=position,
        text=text,
        word_count=count_words(text),
        token_count=estimate_tokens(text),
        page_start=draft.page,
    )
