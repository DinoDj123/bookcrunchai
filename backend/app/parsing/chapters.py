"""Chapter detection: RawDocument blocks -> contract Chapters (design §5).

Boundaries come from the TOC, else from heading patterns, else the "Part N" fallback
(contract rule 5). Keyword lists and regexes are constants at the top of the module.
"""

from __future__ import annotations

import re
from collections.abc import Sequence
from dataclasses import dataclass

from .contract import (
    MAX_TITLE_LENGTH,
    Chapter,
    ChapterKind,
    Paragraph,
    count_words,
    fallback_part_title,
    normalize_text,
)
from .document import Block, RawDocument, TocEntry
from .text import clean_title, cut_at_word

_NUMBER_WORDS = (
    "one|two|three|four|five|six|seven|eight|nine|ten|eleven|twelve|thirteen|fourteen|fifteen"
    "|sixteen|seventeen|eighteen|nineteen|twenty"
)
_ORDINAL_WORDS = (
    "first|second|third|fourth|fifth|sixth|seventh|eighth|ninth|tenth|eleventh|twelfth"
    "|thirteenth|fourteenth|fifteenth|sixteenth|seventeenth|eighteenth|nineteenth|twentieth"
)
_LABELS = "chapter|book|part|volume|glava|poglavlje|knjiga|dio"
# "Chapter 7", "CHAPTER VII", "CHAPTERXXVII" (no space), "Book Two", "Glava 3", "THE FIRST BOOK".
CHAPTER_PATTERN = re.compile(
    rf"^(?:(?:{_LABELS})\s*(?:[0-9]+|[ivxlcdm]+)\b"
    rf"|(?:{_LABELS})\s+(?:{_NUMBER_WORDS}|{_ORDINAL_WORDS})\b"
    rf"|(?:the\s+)?(?:{_ORDINAL_WORDS})\s+book\b)",
    re.IGNORECASE,
)

FRONT_KEYWORDS = (
    "preface",
    "foreword",
    "introduction",
    "dedication",
    "to the reader",
    "translator's note",
    "title page",
    "predgovor",
    "uvod",
    "posveta",
    "riječ prevodioca",
)
BACK_KEYWORDS = (
    "notes",
    "endnotes",
    "footnotes",
    "appendix",
    "glossary",
    "index",
    "afterword",
    "bibliography",
    "references",
    "bilješke",
    "napomene",
    "dodatak",
    "prilog",
    "pojmovnik",
    "indeks",
    "pogovor",
    "literatura",
)
# Dropped entirely (contract rule 9: no printed tables of contents).
DROP_KEYWORDS = (
    "contents",
    "table of contents",
    "list of illustrations",
    "illustrations",
    "sadržaj",
    # An index of the first lines of paragraphs (Meditations, after GLOSSARY) repeats the book's
    # text; kept, it would be summarised and retrieved as if it were content.
    "paragraphs with first lines",
    "index of first lines",
)
# The author's main text (contract §3, Kinds): always kind `chapter`, never moved to front or
# back matter by the position rules.
ALWAYS_CHAPTER_KEYWORDS = ("prologue", "epilogue", "prolog", "epilog")


def _keyword_regex(words: Sequence[str]) -> re.Pattern[str]:
    return re.compile(r"^(?:" + "|".join(re.escape(w) for w in words) + r")\b", re.IGNORECASE)


_FRONT_RE = _keyword_regex(FRONT_KEYWORDS)
_BACK_RE = _keyword_regex(BACK_KEYWORDS)
_ALWAYS_CHAPTER_RE = _keyword_regex(ALWAYS_CHAPTER_KEYWORDS)
# Drop keywords must equal the whole title: "Contents of the Will" is a real chapter.
_DROP_TITLES = frozenset(DROP_KEYWORDS)

# Heading blocks with a keyword title become boundaries only if the heading is short.
MAX_KEYWORD_HEADING_WORDS = 8
# Text before the first boundary is kept as front matter from this many words (decision D4).
MIN_FRONT_MATTER_WORDS = 30
# A pattern/heading boundary set needs at least this many members to be trusted.
MIN_PATTERN_BOUNDARIES = 2
MIN_RECURRING_HEADINGS = 3
# R3: position rule for non-keyword entries applies when at least this many entries match
# CHAPTER_PATTERN.
MIN_PATTERN_FOR_POSITION = 3
# D2: all-caps heading of at least this many words after the last TOC chapter.
MIN_EXTRA_WORK_WORDS = 4
# An entry is dropped when more than this share of its paragraphs look like TOC lines.
TOC_LIKE_SHARE = 0.6
MIN_TOC_ENTRY_PARAGRAPHS = 3
TOC_LINE_MAX_WORDS = 15
# A paragraph made mostly of roman numerals / numbers is a printed list of chapters.
NUMERAL_RUN_MIN_TOKENS = 8
NUMERAL_RUN_SHARE = 0.6

_NUMERAL_TOKEN = re.compile(r"^(?:[ivxlcdm]+|[0-9]+)$", re.IGNORECASE)
_ENDS_WITH_NUMBER = re.compile(r"(?:\b\d+|\b[ivxlcdm]+)\.?$", re.IGNORECASE)
_FRONT_MATTER_TITLE = "Front matter"

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


# ------------------------------------------------------------------ detection


def _key(text: str) -> str:
    """Comparison key: lower case letters and digits only."""
    return re.sub(r"[\W_]+", "", text.lower())


def _keyword_text(title: str) -> str:
    return normalize_text(title).replace("’", "'").strip(" .:;,")


def classify_kind(title: str) -> ChapterKind | None:
    """Kind from the title keywords; None means "drop this entry" (contract rule 9)."""
    text = _keyword_text(title)
    if text.lower() in _DROP_TITLES:
        return None
    if _FRONT_RE.match(text):
        return ChapterKind.FRONT_MATTER
    if _BACK_RE.match(text):
        return ChapterKind.BACK_MATTER
    return ChapterKind.CHAPTER


def _is_always_chapter(title: str) -> bool:
    """Prologue / epilogue (and prolog / epilog) belong to the main text."""
    return _ALWAYS_CHAPTER_RE.match(_keyword_text(title)) is not None


def _matches_pattern(title: str) -> bool:
    return CHAPTER_PATTERN.match(title.strip()) is not None


@dataclass(slots=True)
class _Boundary:
    index: int  # block index where the entry starts
    label: str | None  # TOC label (used when the block is not a heading)
    from_toc: bool = False
    forced_kind: ChapterKind | None = None


def _block_title(block: Block, label: str | None) -> str | None:
    """R1: a heading block names its own entry; otherwise the TOC label does."""
    if block.heading_level is not None:
        title = clean_title(block.text)
        if title is not None:
            return title
    return clean_title(label) if label is not None else None


def _resolve_toc_target(
    entry: TocEntry, blocks: Sequence[Block], anchors: dict[str, int], pages: dict[int, list[int]]
) -> int | None:
    target = entry.target
    if isinstance(target, str):
        found = anchors.get(target)
        if found is None and "#" in target:
            found = anchors.get(target.split("#", 1)[0])
        return found
    candidates = pages.get(target)
    if candidates is None:
        later = sorted(p for p in pages if p > target)
        if not later:
            return None
        candidates = pages[later[0]]
    headings = [i for i in candidates if blocks[i].heading_level is not None]
    wanted = _key(entry.title)
    for i in headings:
        own = _key(blocks[i].text)
        if own and (wanted.startswith(own) or own.startswith(wanted)):
            return i
    return headings[0] if headings else candidates[0]


def _toc_boundaries(doc: RawDocument, book_key: str) -> list[_Boundary]:
    blocks = doc.blocks
    anchors: dict[str, int] = {}
    pages: dict[int, list[int]] = {}
    for i, block in enumerate(blocks):
        for anchor in block.anchors:
            anchors.setdefault(anchor, i)
        if block.page is not None:
            pages.setdefault(block.page, []).append(i)

    entries = [e for e in doc.toc if clean_title(e.title) is not None and _key(e.title) != book_key]
    matching = sorted({e.level for e in entries if _matches_pattern(e.title)})
    level = matching[-1] if matching else min((e.level for e in entries), default=1)
    chosen = [
        e
        for e in entries
        if e.level == level
        or (e.level < level and classify_kind(e.title) is not ChapterKind.CHAPTER)
    ]

    found: list[_Boundary] = []
    for entry in chosen:
        index = _resolve_toc_target(entry, blocks, anchors, pages)
        if index is not None:
            found.append(_Boundary(index, entry.title, from_toc=True))
    found.sort(key=lambda b: b.index)  # stable: the same block keeps the first TOC title
    unique: list[_Boundary] = []
    for boundary in found:
        if not unique or unique[-1].index != boundary.index:
            unique.append(boundary)
    return unique


def _pattern_boundaries(doc: RawDocument, book_key: str) -> list[_Boundary]:
    headings = [
        (i, b)
        for i, b in enumerate(doc.blocks)
        if b.heading_level is not None and _key(b.text) != book_key
    ]
    matched = [
        _Boundary(i, None)
        for i, b in headings
        if (t := clean_title(b.text)) is not None and _matches_pattern(t)
    ]
    if len(matched) >= MIN_PATTERN_BOUNDARIES:
        return matched
    for level in (1, 2):
        recurring = [_Boundary(i, None) for i, b in headings if b.heading_level == level]
        if len(recurring) >= MIN_RECURRING_HEADINGS:
            return recurring
    return []


def _add_keyword_boundaries(doc: RawDocument, found: list[_Boundary], book_key: str) -> None:
    present = {b.index for b in found}
    for i, block in enumerate(doc.blocks):
        if block.heading_level is None or i in present or _key(block.text) == book_key:
            continue
        if count_words(block.text) > MAX_KEYWORD_HEADING_WORDS:
            continue
        if classify_kind(block.text) is not ChapterKind.CHAPTER:
            found.append(_Boundary(i, None))
    found.sort(key=lambda b: b.index)


def _add_extra_works(doc: RawDocument, found: list[_Boundary]) -> None:
    """D2: all-caps headings after the last TOC chapter start back matter entries (R4)."""
    chapter_like = [
        b
        for b in found
        if b.from_toc and (t := _block_title(doc.blocks[b.index], b.label)) and _matches_pattern(t)
    ]
    if not chapter_like:
        return
    start = chapter_like[-1].index
    later = [b.index for b in found if b.index > start]
    end = later[0] if later else len(doc.blocks)
    extra: list[_Boundary] = []
    for i in range(start + 1, end):
        block = doc.blocks[i]
        if block.heading_level is None or not block.text.isupper():
            continue
        if count_words(block.text) < MIN_EXTRA_WORK_WORDS:
            continue
        if doc.blocks[i - 1].heading_level is not None:
            continue  # not the first heading of a run: a by-line of the previous work
        extra.append(_Boundary(i, None, forced_kind=ChapterKind.BACK_MATTER))
    found.extend(extra)
    found.sort(key=lambda b: b.index)


def _is_numeral_run(text: str) -> bool:
    tokens = text.split()
    if len(tokens) < NUMERAL_RUN_MIN_TOKENS:
        return False
    numerals = sum(1 for t in tokens if _NUMERAL_TOKEN.match(t.strip(".,;:()")))
    return numerals / len(tokens) >= NUMERAL_RUN_SHARE


def _is_toc_line(text: str, toc_keys: set[str]) -> bool:
    if count_words(text) > TOC_LINE_MAX_WORDS:
        return False
    ends_with_number = count_words(text) >= 2 and _ENDS_WITH_NUMBER.search(text) is not None
    return ends_with_number or _key(text) in toc_keys


def _entry_paragraphs(
    blocks: Sequence[Block], skip_first: bool, toc_keys: set[str]
) -> list[Paragraph]:
    """Paragraphs of one entry; a printed table of contents comes back empty (rule 9)."""
    paragraphs = [
        Paragraph(normalize_text(b.text), b.page)
        for n, b in enumerate(blocks)
        if not (skip_first and n == 0) and normalize_text(b.text) and not _is_numeral_run(b.text)
    ]
    if len(paragraphs) >= MIN_TOC_ENTRY_PARAGRAPHS:
        toc_like = sum(1 for p in paragraphs if _is_toc_line(p.text, toc_keys))
        if toc_like / len(paragraphs) > TOC_LIKE_SHARE:
            return []
    return paragraphs


@dataclass(slots=True)
class _Entry:
    title: str
    kind: ChapterKind
    paragraphs: list[Paragraph]
    pattern: bool = False


def _build_entries(doc: RawDocument, found: list[_Boundary]) -> list[_Entry]:
    blocks = doc.blocks
    toc_keys = {_key(t) for b in found if (t := _block_title(blocks[b.index], b.label))}
    entries: list[_Entry] = []

    first = found[0].index
    if first > 0:
        paragraphs = _entry_paragraphs(blocks[:first], False, toc_keys)
        if sum(count_words(p.text) for p in paragraphs) >= MIN_FRONT_MATTER_WORDS:
            entries.append(_Entry(_FRONT_MATTER_TITLE, ChapterKind.FRONT_MATTER, paragraphs))

    ends = [b.index for b in found[1:]] + [len(blocks)]
    for boundary, end in zip(found, ends, strict=True):
        block = blocks[boundary.index]
        title = _block_title(block, boundary.label) or "Untitled"
        kind = boundary.forced_kind or classify_kind(title)
        if kind is not None and _is_always_chapter(title):
            kind = ChapterKind.CHAPTER
        if kind is None:
            continue  # contents, list of illustrations: dropped
        is_title = block.heading_level is not None and clean_title(block.text) is not None
        paragraphs = _entry_paragraphs(blocks[boundary.index : end], is_title, toc_keys)
        if not paragraphs:
            continue  # empty heading (merged forward) or printed TOC (dropped)
        title = cut_at_word(title, MAX_TITLE_LENGTH)
        is_pattern = kind is ChapterKind.CHAPTER and _matches_pattern(title)
        entries.append(_Entry(title, kind, paragraphs, pattern=is_pattern))
    return entries


def _apply_positions(entries: list[_Entry]) -> None:
    """Contract rule 3: front only before the first chapter, back only after the last."""
    pattern_idx = [i for i, e in enumerate(entries) if e.pattern]
    if len(pattern_idx) >= MIN_PATTERN_FOR_POSITION:  # R3
        for i, entry in enumerate(entries):
            if (
                entry.kind is ChapterKind.CHAPTER
                and not entry.pattern
                and not _is_always_chapter(entry.title)
            ):
                if i < pattern_idx[0]:
                    entry.kind = ChapterKind.FRONT_MATTER
                elif i > pattern_idx[-1]:
                    entry.kind = ChapterKind.BACK_MATTER
    changed = True
    while changed:
        changed = False
        chapters = [i for i, e in enumerate(entries) if e.kind is ChapterKind.CHAPTER]
        if not chapters:
            return
        for i, entry in enumerate(entries):
            if entry.kind is ChapterKind.FRONT_MATTER and i > chapters[0]:
                entry.kind, changed = ChapterKind.CHAPTER, True
            elif entry.kind is ChapterKind.BACK_MATTER and i < chapters[-1]:
                entry.kind, changed = ChapterKind.CHAPTER, True


def build_chapters(doc: RawDocument) -> tuple[tuple[Chapter, ...], bool]:
    """Chapters of the book and whether they were really detected (False = "Part N" fallback)."""
    book_key = _key(doc.title) if doc.title else ""
    found = _toc_boundaries(doc, book_key)
    if len(found) >= 2:
        _add_keyword_boundaries(doc, found, book_key)
        _add_extra_works(doc, found)
    else:
        found = _pattern_boundaries(doc, book_key)
        if found:
            _add_keyword_boundaries(doc, found, book_key)
    if not found:
        return split_into_parts(doc.blocks), False

    entries = _build_entries(doc, found)
    _apply_positions(entries)
    if not any(e.kind is ChapterKind.CHAPTER for e in entries):
        return split_into_parts(doc.blocks), False
    chapters = tuple(
        Chapter(position=n, title=e.title, kind=e.kind, paragraphs=tuple(e.paragraphs))
        for n, e in enumerate(entries, start=1)
    )
    return chapters, True
