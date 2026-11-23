"""PDF reader: file -> RawDocument (design §4).

Uses PyMuPDF's line geometry (`page.get_text("dict")`) to remove running headers, footers, page
numbers and small-type footnotes, to join drop caps and hyphenated words, and to rebuild
paragraphs from first-line indents and vertical gaps. Large-font lines become heading blocks.

Only extracts and cleans text. It never decides what a chapter is (that is chapters.py).
Single-column text PDFs only; multi-column layouts and scans are known limitations (design §8).
"""

from __future__ import annotations

import re
from collections import Counter
from collections.abc import Callable
from dataclasses import dataclass, field, replace
from itertools import pairwise
from pathlib import Path

import pymupdf

from .boilerplate import is_publisher_promo
from .contract import FileFormat, ParseError, ParseErrorCode, count_words, normalize_text
from .document import Block, RawDocument, TocEntry
from .text import join_hyphenated, starts_with_footnote_marker, strip_footnote_markers

# Running headers and footers live in the top / bottom 15 % of the page height (line centre).
# The design said 10 %, but the-prince.pdf has its footer at 85-87 % of the page height.
HEADER_ZONE = 0.15
# A header/footer text must repeat on >= 25 % of pages: odd and even pages often alternate.
REPEAT_RATIO = 0.25
# ... and on at least this many pages, so short documents do not lose real lines.
MIN_REPEAT_PAGES = 3
# Small-type footnotes are looked for in the bottom 25 % of the page.
FOOTNOTE_ZONE = 0.25
# A footnote line is set in type at most 85 % of the body size.
FOOTNOTE_SIZE_RATIO = 0.85
# Lines at least 1.3 x the body size are headings.
HEADING_SIZE_RATIO = 1.3
# A single letter at least 2 x the body size, with body text beside it, is a drop cap.
DROP_CAP_RATIO = 2.0
# Fewer extracted characters per page than this (on average) means a scan without text layer.
MIN_CHARS_PER_PAGE = 100
# A line starting 0.8 x body size right of the left margin is a first-line indent.
INDENT_RATIO = 0.8
# A vertical gap over 1.5 x the common line spacing starts a new paragraph.
GAP_RATIO = 1.5
# A page-final line shorter than 85 % of the column width may end its paragraph.
SHORT_LINE_RATIO = 0.85
# A body-size all-caps paragraph with at most 30 words is a level-3 heading. The design said one
# line of <= 12 words, but the titles of the extra works in the-prince.pdf (Decision D2) are
# all-caps paragraphs of 2-5 lines and up to 26 words.
CAPS_HEADING_MAX_WORDS = 30

# Characters a page-final paragraph can end with.
_PARAGRAPH_END = (".", "?", "!", ":", "”", "’", '"', "'")
# Planet eBook prints page numbers 1-9 with private-use glyphs (U+F643 + digit), so they count
# as digits for page numbers and header keys.
_DIGIT = re.compile(r"[\d-]")
_ARABIC_NUMBER = re.compile(r"[\d-]+")
_ROMAN_NUMBER = re.compile(r"M{0,4}(?:CM|CD|D?C{0,3})(?:XC|XL|L?X{0,3})(?:IX|IV|V?I{0,3})", re.I)
# Metadata values that are not a real title or author: Word export names, "untitled", file names.
_JUNK_METADATA = re.compile(
    r"microsoft word\b.*|untitled\d*|.+\.(?:pdf|docx?|rtf|odt|txt|indd|tex)", re.I
)


@dataclass(frozen=True, slots=True)
class _Line:
    text: str
    x0: float
    y0: float
    x1: float
    y1: float
    size: float  # font size of the span with the most characters


@dataclass(frozen=True, slots=True)
class _Page:
    number: int  # 1-based
    height: float
    lines: tuple[_Line, ...]  # in reading order (top to bottom, then left to right)


@dataclass(frozen=True, slots=True)
class _Layout:
    """Font sizes and spacing measured over the whole book."""

    body_size: float
    line_spacing: float
    # Left margin of body lines on odd and even pages, None if that parity has no body lines.
    margins: tuple[float | None, float | None] = (None, None)

    @property
    def heading_size(self) -> float:
        return self.body_size * HEADING_SIZE_RATIO

    def is_heading(self, line: _Line) -> bool:
        return line.size >= self.heading_size


def read_pdf(path: Path) -> RawDocument:
    """Read a text-based PDF. Raises ParseError(CORRUPT_FILE | ENCRYPTED_FILE | NO_TEXT_LAYER)."""
    try:
        doc = pymupdf.open(path)
    except (pymupdf.FileDataError, pymupdf.FileNotFoundError) as exc:
        raise ParseError(ParseErrorCode.CORRUPT_FILE, f"cannot open PDF: {exc}") from exc

    with doc:
        if doc.needs_pass:
            raise ParseError(ParseErrorCode.ENCRYPTED_FILE, "PDF is password protected")
        if doc.page_count == 0:
            # A badly truncated file is "repaired" by MuPDF into a document without pages.
            raise ParseError(ParseErrorCode.CORRUPT_FILE, "PDF has no readable pages")
        pages = [_read_page(page) for page in doc]
        _check_text_layer(pages)
        metadata = doc.metadata or {}
        title = _metadata_value(metadata.get("title"))
        author = _metadata_value(metadata.get("author"))
        language = _catalog_language(doc)
        toc = _outline(doc)

    layout = _measure_layout(pages)
    pages = _remove_running_lines(pages, layout)
    pages = _remove_small_footnotes(pages, layout)
    layout = replace(layout, margins=_measure_margins(pages, layout))
    blocks = _build_blocks(pages, layout)
    return RawDocument(
        title=title,
        author=author,
        language=language,
        file_format=FileFormat.PDF,
        blocks=tuple(blocks),
        toc=tuple(toc),
    )


# --------------------------------------------------------------------------- reading


def _read_page(page: pymupdf.Page) -> _Page:
    lines: list[_Line] = []
    for block in page.get_text("dict", flags=pymupdf.TEXTFLAGS_TEXT)["blocks"]:
        for line in block.get("lines", ()):
            spans = line["spans"]
            text = "".join(span["text"] for span in spans)
            if not text.strip():
                continue
            main_span = max(spans, key=lambda span: len(span["text"].strip()))
            x0, y0, x1, y1 = line["bbox"]
            lines.append(_Line(text, x0, y0, x1, y1, round(main_span["size"], 1)))
    lines.sort(key=lambda line: (line.y0, line.x0))
    return _Page(page.number + 1, page.rect.height, tuple(lines))


def _check_text_layer(pages: list[_Page]) -> None:
    chars = sum(len("".join(line.text.split())) for page in pages for line in page.lines)
    if chars / len(pages) < MIN_CHARS_PER_PAGE:
        raise ParseError(
            ParseErrorCode.NO_TEXT_LAYER,
            f"{chars / len(pages):.0f} characters per page; the PDF looks scanned",
        )


def _metadata_value(raw: str | None) -> str | None:
    value = normalize_text(raw or "")
    if not value or _JUNK_METADATA.fullmatch(value):
        return None
    return value


def _catalog_language(doc: pymupdf.Document) -> str | None:
    kind, value = doc.xref_get_key(doc.pdf_catalog(), "Lang")
    if kind != "string":
        return None
    return normalize_text(value) or None


def _outline(doc: pymupdf.Document) -> list[TocEntry]:
    entries: list[TocEntry] = []
    for level, raw_title, page in doc.get_toc(simple=True):
        title = normalize_text(strip_footnote_markers(raw_title))
        if page >= 1 and title:  # page -1: an entry without a target in this document
            entries.append(TocEntry(title, level, page))
    return entries


# --------------------------------------------------------------------------- clean-up


def _measure_layout(pages: list[_Page]) -> _Layout:
    """Body size = the size with the most characters; line spacing = the most common step."""
    chars: Counter[float] = Counter()
    for page in pages:
        for line in page.lines:
            chars[line.size] += len(line.text.strip())
    body_size = _most_common(chars)

    steps: Counter[float] = Counter()
    for page in pages:
        body = [line for line in page.lines if line.size == body_size]
        for previous, line in pairwise(body):
            step = round(line.y0 - previous.y0, 1)
            if step > 0:
                steps[step] += 1
    line_spacing = _most_common(steps) if steps else body_size * 1.2
    return _Layout(body_size, line_spacing)


def _measure_margins(pages: list[_Page], layout: _Layout) -> tuple[float | None, float | None]:
    """Left margin of body lines over the whole book, for odd and even pages separately.

    Measured per book, not per page: a page of only first-line-indented one-line paragraphs has
    no line at the real margin, so its own most common start would be the indent (design §4).
    """
    starts: tuple[Counter[float], Counter[float]] = (Counter(), Counter())
    for page in pages:
        counter = starts[page.number % 2 == 0]
        counter.update(float(round(ln.x0)) for ln in page.lines if not layout.is_heading(ln))
    odd, even = (_most_common(c) if c else None for c in starts)
    return odd, even


def _most_common(counter: Counter[float]) -> float:
    """Most common value; ties go to the smallest value so the result is deterministic."""
    return min(counter, key=lambda value: (-counter[value], value))


def _in_margin_zone(line: _Line, page: _Page) -> bool:
    centre = (line.y0 + line.y1) / 2
    return centre < page.height * HEADER_ZONE or centre > page.height * (1 - HEADER_ZONE)


def _margin_key(text: str) -> str:
    return _DIGIT.sub("#", normalize_text(text).lower())


def _is_page_number(text: str) -> bool:
    text = normalize_text(text)
    return bool(_ARABIC_NUMBER.fullmatch(text) or (text and _ROMAN_NUMBER.fullmatch(text)))


def _remove_running_lines(pages: list[_Page], layout: _Layout) -> list[_Page]:
    """Drop repeated header/footer lines and page numbers in the top and bottom zones."""

    def is_candidate(line: _Line, page: _Page) -> bool:
        return _in_margin_zone(line, page) and not layout.is_heading(line)

    pages_with_key: dict[str, set[int]] = {}
    for page in pages:
        for line in page.lines:
            if is_candidate(line, page):
                pages_with_key.setdefault(_margin_key(line.text), set()).add(page.number)
    min_pages = max(MIN_REPEAT_PAGES, REPEAT_RATIO * len(pages))
    repeated = {key for key, numbers in pages_with_key.items() if len(numbers) >= min_pages}

    def is_running(line: _Line, page: _Page) -> bool:
        if not is_candidate(line, page):
            return False
        return _margin_key(line.text) in repeated or _is_page_number(line.text)

    return _drop_lines(pages, is_running)


def _remove_small_footnotes(pages: list[_Page], layout: _Layout) -> list[_Page]:
    """Footnotes are dropped in the MVP: small type in the bottom quarter of the page."""
    max_size = layout.body_size * FOOTNOTE_SIZE_RATIO

    def is_footnote(line: _Line, page: _Page) -> bool:
        centre = (line.y0 + line.y1) / 2
        return centre > page.height * (1 - FOOTNOTE_ZONE) and line.size <= max_size

    return _drop_lines(pages, is_footnote)


def _drop_lines(pages: list[_Page], is_dropped: Callable[[_Line, _Page], bool]) -> list[_Page]:
    return [
        _Page(page.number, page.height, tuple(ln for ln in page.lines if not is_dropped(ln, page)))
        for page in pages
    ]


# --------------------------------------------------------------------------- paragraphs


@dataclass(slots=True)
class _Paragraph:
    page: int  # page where the paragraph starts
    heading_size: float | None  # font size for a heading, None for body text
    parts: list[str] = field(default_factory=list)  # raw line texts


def _build_blocks(pages: list[_Page], layout: _Layout) -> list[Block]:
    paragraphs = _paragraphs(pages, layout)
    heading_sizes = [p.heading_size for p in paragraphs if p.heading_size is not None]
    top_size = max(heading_sizes, default=None)

    blocks: list[Block] = []
    for paragraph in paragraphs:
        raw = "\n".join(paragraph.parts)
        if paragraph.heading_size is None and starts_with_footnote_marker(raw):
            continue  # footnote text (dropped in the MVP)
        text = normalize_text(strip_footnote_markers(join_hyphenated(raw)))
        if text and not is_publisher_promo(text):
            blocks.append(Block(text, paragraph.page, _heading_level(paragraph, text, top_size)))
    return blocks


def _heading_level(paragraph: _Paragraph, text: str, top_size: float | None) -> int | None:
    if paragraph.heading_size is not None:
        return 1 if paragraph.heading_size == top_size else 2
    is_caps_heading = (
        text.upper() == text
        and any(char.isalpha() for char in text)
        and count_words(text) <= CAPS_HEADING_MAX_WORDS
    )
    return 3 if is_caps_heading else None


def _paragraphs(pages: list[_Page], layout: _Layout) -> list[_Paragraph]:
    """Group lines into heading and body paragraphs (design §4 steps 5-7)."""
    paragraphs: list[_Paragraph] = []
    current: _Paragraph | None = None
    previous_page_ended = True  # whether the last body line of the previous page ended a paragraph
    indent = INDENT_RATIO * layout.body_size
    max_gap = GAP_RATIO * layout.line_spacing

    for page in pages:
        margin, right = _column(page, layout)
        previous: _Line | None = None  # previous line on this page
        drop_cap = ""  # drop cap letter waiting for the line beside it
        drop_cap_bottom = 0.0  # lines beside the drop cap are not first-line indents

        for index, line in enumerate(page.lines):
            following = page.lines[index + 1] if index + 1 < len(page.lines) else None
            if _is_drop_cap(line, following, layout):
                current = _Paragraph(page.number, None)
                paragraphs.append(current)
                # "N" + "icolo" -> "Nicolo"; a cap with a trailing space is a word: "I " + "will".
                drop_cap = line.text.lstrip()
                drop_cap_bottom = line.y1
                continue

            if layout.is_heading(line):
                continues_heading = (
                    current is not None
                    and current.heading_size == line.size
                    and previous is not None
                    and line.y0 - previous.y1 < line.size
                )
                if not continues_heading or current is None:
                    current = _Paragraph(page.number, line.size)
                    paragraphs.append(current)
                current.parts.append(line.text)
                previous = line
                continue

            if drop_cap and current is not None:
                current.parts.append(drop_cap + line.text.lstrip())
                drop_cap = ""
                previous = line
                continue

            indented = (
                margin is not None and line.x0 - margin >= indent and line.y0 >= drop_cap_bottom
            )
            if current is None or current.heading_size is not None:
                starts_paragraph = True
            elif previous is None:  # first line of the page
                starts_paragraph = indented or previous_page_ended
            else:
                starts_paragraph = indented or line.y0 - previous.y0 > max_gap
            if starts_paragraph or current is None:
                current = _Paragraph(page.number, None)
                paragraphs.append(current)
            current.parts.append(line.text)
            previous = line

        previous_page_ended = _ends_paragraph(page, layout, margin, right)
    return paragraphs


def _column(page: _Page, layout: _Layout) -> tuple[float | None, float]:
    """Left margin (book-wide for this page parity) and right edge of the body text."""
    body = [line for line in page.lines if not layout.is_heading(line)]
    if not body:
        return None, 0.0
    margin = layout.margins[page.number % 2 == 0]
    if margin is None:  # no other page of this parity has body lines
        margin = _most_common(Counter(float(round(line.x0)) for line in body))
    return margin, max(line.x1 for line in body)


def _is_drop_cap(line: _Line, following: _Line | None, layout: _Layout) -> bool:
    letter = line.text.strip()
    return (
        len(letter) == 1
        and letter.isalpha()
        and line.size >= DROP_CAP_RATIO * layout.body_size
        and following is not None
        and not layout.is_heading(following)
        and following.x0 > line.x0
        and following.y0 < line.y1
    )


def _ends_paragraph(page: _Page, layout: _Layout, margin: float | None, right: float) -> bool:
    """A page-final body line that is short and ends a sentence ends its paragraph."""
    body = [line for line in page.lines if not layout.is_heading(line)]
    if not body or margin is None:
        return True
    last = body[-1]
    is_short = last.x1 - margin < SHORT_LINE_RATIO * (right - margin)
    return is_short and last.text.rstrip().endswith(_PARAGRAPH_END)
