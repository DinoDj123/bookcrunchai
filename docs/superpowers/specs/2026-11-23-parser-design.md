# Parser (Track A, Week 1: A2–A5) — Design

**Owner:** Track A — Dino Đerekerac. **Status:** approved by Dino on 23.11.2026; D3 also needs
Daris (contract 1.1).

**Source of requirements:** `docs/specification.md` FR-003, FR-004, FR-005, section 11.1 (M1);
`docs/contracts/parse_book.md` v1.0 (the code in `backend/app/parsing/contract.py` wins).

**Goal:** `parse_book(file_path) -> ParsedBook` for EPUB and text-based PDF, with clean paragraphs,
chapters and front/back matter, passing `validate_parsed_book()` on the three test books.

## 1. Architecture

Two stages, so EPUB and PDF share one chapter-detection step:

```
file ──► detect format by signature
          ├─ EPUB ─► epub.read_epub(path) ──┐
          └─ PDF  ─► pdf.read_pdf(path)  ───┤   RawDocument (internal, Track A only)
                                            ▼
                               chapters.build_chapters(doc)
                                            ▼
                         parser.parse_book assembles ParsedBook
```

The readers only extract and clean text; they never decide what a chapter is. `chapters.py` only
groups blocks; it never opens files. Each part can be unit-tested with hand-built inputs.

### Modules (all in `backend/app/parsing/`, all Track A)

| Module | Responsibility |
|---|---|
| `parser.py` | `parse_book()`: signature detection, dispatch, metadata clean-up, builds `ParsedBook` |
| `document.py` | Internal types `Block`, `TocEntry`, `RawDocument` (not part of the contract) |
| `text.py` | Shared text helpers: hyphenation join, footnote-marker removal, language code, title clean-up |
| `boilerplate.py` | Project Gutenberg licence header/footer detection |
| `epub.py` | `read_epub(path) -> RawDocument` |
| `pdf.py` | `read_pdf(path) -> RawDocument` |
| `chapters.py` | `build_chapters(doc) -> tuple[tuple[Chapter, ...], bool]`, kind classification, `Part N` fallback |

The worker never sees any of these: it only calls `get_parse_book()` (contract §5.1).

### Internal model (`document.py`)

```python
@dataclass(frozen=True, slots=True)
class Block:
    text: str                    # already passed through normalize_text(), non-empty
    page: int | None             # PDF: 1-based page where the block starts; EPUB: None
    heading_level: int | None    # 1..6 when the block looks like a heading, else None
    anchors: tuple[str, ...] = ()  # EPUB: "path/in/zip.xhtml#id" targets that land on this block

@dataclass(frozen=True, slots=True)
class TocEntry:
    title: str                   # normalized
    level: int                   # 1 = top level
    target: str | int            # EPUB: "path/in/zip.xhtml" or "...#id"; PDF: 1-based page

@dataclass(frozen=True, slots=True)
class RawDocument:
    title: str | None
    author: str | None
    language: str | None
    file_format: FileFormat
    blocks: tuple[Block, ...]    # whole book in reading order, boilerplate already removed
    toc: tuple[TocEntry, ...]    # EPUB nav/NCX or PDF outline; may be empty
```

## 2. Format detection and errors (`parser.py`)

- Read the first 8 bytes. `PK\x03\x04` → EPUB, `%PDF` → PDF, anything else →
  `ParseError(UNSUPPORTED_FORMAT)`. Missing/unreadable file → `CORRUPT_FILE`.
- Library exceptions are converted at the reader boundary only: `zipfile.BadZipFile`, missing
  `META-INF/container.xml` or OPF, `pymupdf.FileDataError` → `CORRUPT_FILE`. No blanket
  `except Exception`: any other exception is a bug and must surface (contract rule 15).
- `ENCRYPTED_FILE`: EPUB with `META-INF/encryption.xml` that encrypts content documents, or with
  `META-INF/rights.xml`. **Font obfuscation** (algorithms `http://www.idpf.org/2008/embedding` and
  `http://ns.adobe.com/pdf/enc#RC`) only touches fonts and is *not* DRM: ignore it. PDF:
  `doc.needs_pass` → `ENCRYPTED_FILE` (PDFs with only permission flags open fine).
- `NO_TEXT_LAYER`: PDF with average extracted characters per page < 100 (computed before clean-up).
- `EMPTY_BOOK`: no blocks left after clean-up.
- Determinism: no sets iterated into output, no time/random, sort only with stable keys.

## 3. EPUB reader (`epub.py`) — A2

**Decision D1 (DECIDED):** read EPUB with stdlib `zipfile` + `BeautifulSoup` (`lxml` parser)
instead of EbookLib. Reasons: full control over which exception becomes which `ParseError`; we need
element-level anchors for chapter splitting, which EbookLib does not help with; OPF/nav/NCX parsing
is ~80 lines; one dependency less (EbookLib is AGPL and lightly maintained). The specification
lists EbookLib only under References, so this needs a line in Appendix D (Design Decision Log).

Steps:
1. `META-INF/container.xml` → OPF path. Parse OPF: `dc:title`, `dc:creator` (first, role `aut`
   preferred), `dc:language`; manifest (id → href, media-type, properties); spine (idrefs in order,
   skip `linear="no"` only if it is the cover).
2. Navigation: EPUB3 nav document (`properties="nav"`, `<nav epub:type="toc">`) or else EPUB2 NCX
   (`toc` attribute of spine). Flatten to `TocEntry` in document order with `level`. Targets are
   resolved relative to the nav file into zip paths (`OEBPS/x.xhtml#id`), URL-decoded.
3. For each spine document: parse XHTML; drop `script`, `style`, `nav`, `img`, `svg`, `figure`
   captions-only, `aside[epub:type=footnote]`, page-break markers (`epub:type="pagebreak"`).
   Emit one `Block` per block-level element that contains text (`p`, `div` with direct text,
   `h1`–`h6`, `li`, `blockquote > p`, `pre`, `td` rows joined with " "). Headings get
   `heading_level`. `<br>` inside a paragraph becomes a space (poetry lines join; accepted for MVP).
4. Anchors: every `id` on the element itself or on an empty/inline element inside it (Gutenberg
   uses `<a id="link2HCH0001"></a>` before headings) is attached to the block as
   `"<zip path>#<id>"`; the first block of a document also gets the bare `"<zip path>"`. An `id`
   on an element with no text attaches to the next block.
5. Gutenberg boilerplate (`boilerplate.py`): drop elements with `id="pg-header"`/`"pg-footer"` or
   class `pg-boilerplate`; as a fallback drop everything before the line containing
   `*** START OF THE PROJECT GUTENBERG EBOOK` and after `*** END OF THE PROJECT GUTENBERG EBOOK`
   (case-insensitive, also "THIS PROJECT GUTENBERG"). TOC entries that point into removed parts are
   dropped.
6. Every text goes through `normalize_text()` after `text.join_hyphenated()`; empty blocks dropped.

## 4. PDF reader (`pdf.py`) — A3

PyMuPDF (`import pymupdf`). Facts measured on `the-prince.pdf` (Planet eBook, 194 pages,
322×484 pt): body text 10 pt; chapter headings 20 pt, split over up to 5 lines; drop caps 28.5 pt
single letter; footer line at y≈411 on every page: page number plus "Free eBooks at Planet
eBook.com" (odd pages) or "The Prince" (even pages), 7 pt; first-line indent x=54 vs x=42 for other
lines; line-end hyphenation uses U+00AD (soft hyphen); the PDF outline has 30 entries: "The Prince",
"INTRODUCTION", CHAPTER I–XXVI, two entries with page −1.

Re-measured in A3 (23.11.2026), differences from the facts above: the footer line spans y≈411–420
of a 484 pt page, i.e. 85–87 % of the height, so it is **not** in the bottom 10 % (the reader uses
a 15 % zone measured at the line centre); page numbers 2–9 are private-use glyphs U+F643+digit, not
ASCII digits (from page 10 on they are digits); footnotes are **body-size** (10 pt) paragraphs that
start with `[*]`, `[+]`, `[%]`, `[#]`, `[&]` or `[$]`, usually indented, placed after the paragraph
that cites them (there is no small-type footnote text; all 7 pt text is the footer); the book title
on page 1 is 24 pt (so chapter headings get `heading_level` 2); the titles of the two extra works
(D2) are body-size all-caps paragraphs of 2–5 lines (up to 26 words); a drop cap with a trailing
space (`"I "`, p. 20) is a whole word; the catalog has no `/Lang`. Everything else matches.

Steps:
1. Open; errors as in §2. A document with 0 pages (MuPDF "repairs" a badly truncated file into
   an empty document instead of raising) → `CORRUPT_FILE`. `NO_TEXT_LAYER` check on raw text.
2. Per page, `page.get_text("dict")` → lines with `bbox`, spans (size, flags, text). Body size =
   the font size with the most characters in the whole book.
3. **Running headers/footers and page numbers:** a line is a candidate if it lies in the top or
   bottom 15 % of the page height, measured at the line centre (10 % missed the-prince.pdf footer
   at 85–87 %). Heading-size lines (≥ 1.3 × body) are never candidates. Key = lower-cased text
   with digits (and private-use digit glyphs U+E000–U+F8FF) replaced by `#`. Keys that appear on
   ≥ 25 % of pages (and ≥ 3 pages) are removed everywhere in those zones (25 % because odd and even
   pages alternate). Also remove any candidate line that is only a number: arabic digits,
   private-use digit glyphs, or a roman numeral.
4. **Footnotes (dropped in MVP):** lines in the bottom 25 % of the page with font size
   ≤ 0.85 × body that are not headers/footers. Any body paragraph that starts with a footnote
   marker is dropped, wherever it is (Planet eBook sets footnotes as body-size paragraphs).
   Paragraph-start markers: `[*]`, `[+]`, `[%]`, `[#]`, `[&]`, `[$]`, `[†]`, `[‡]`, `[§]` (also
   repeated, e.g. `[**]`), 1–3 digit numbers (`[1]`, `[12]`), a bare `*`
   (`text.starts_with_footnote_marker()`; `[a]` does not count). Inline markers (the same set
   plus `[a]`–`[z]`) are removed from body text, headings and outline titles by
   `text.strip_footnote_markers()`.
5. **Drop caps:** a span ≥ 2 × body size containing a single letter is prefixed to the next body
   line beside it without a space (`N` + `icolo` → `Nicolo`); a cap whose text ends with a space is
   a whole word (`"I "` + `will` → `I will`). Lines beside the cap are not first-line indents.
6. **Headings:** consecutive lines of the same size ≥ 1.3 × body, closer than one font size, are
   merged into one heading block (`heading_level` 1 for the largest heading size in the book, 2 for
   every other heading size). An all-caps body-size paragraph (it may span several lines) of
   ≤ 30 words is a heading candidate with `heading_level=3` (used only by pattern rules in
   `chapters.py`, e.g. "DEDICATION" and the D2 extra-work titles).
7. **Paragraphs:** a new paragraph starts when the line's x0 is ≥ 0.8 × body size to the right of
   the common left margin (first-line indent; measured over the whole book, separately for odd
   and even pages, so a page of only indented one-line paragraphs still splits), or the vertical gap to the previous line is
   > 1.5 × the common line spacing, or after a heading. A paragraph continues across a page break
   unless the last line of the page was short (< 85 % of column width) and ended with `.?!:”’`.
   `page` = page where the paragraph starts.
8. **Hyphenation:** soft hyphen at line end → always join without space. Hard `-` at line end
   followed by a lowercase letter → join without space and drop the `-` (accepted loss for real
   compounds split at line end; documented limitation).
9. Metadata: `doc.metadata` title/author; values like `Microsoft Word - …`, `untitled`, file names
   (`*.pdf`, `*.docx`) are treated as missing. Language from the catalog `/Lang` if present, else
   `None`. Outline from `doc.get_toc(simple=True)`; entries with page < 1 are dropped.

## 5. Chapter detection (`chapters.py`) — A4

Input: `RawDocument`. Output: `(chapters, chapters_detected)`.

**Step 1 – boundaries.** In order of preference:
1. **TOC** (EPUB nav/NCX, PDF outline) if it maps to ≥ 2 distinct block positions. Map each entry
   to the first block carrying its anchor (EPUB) or the first heading block on its page, else the
   first block on its page (PDF). Nested TOCs: use the deepest level whose entries match the
   chapter patterns; if none, level 1. Entries that resolve to the same block keep the first title.
   Entries whose title is a document title (equals book title) are skipped.
2. **Extra boundaries from patterns**, added even in TOC mode: a heading block (or level-3
   candidate) whose text matches a front/back-matter keyword (§Step 3) and that is not already a
   boundary. This is how The Prince gets "DEDICATION" (not in its outline).
3. **Patterns** (no usable TOC): heading blocks matching
   `^(chapter|book|part|glava|poglavlje|knjiga|dio)\s+([0-9]+|[ivxlcdm]+|one|two|…|first|…)\b`
   (case-insensitive) or `^(the\s+)?(first|second|…|twelfth)\s+book\b`; also `heading_level` 1–2
   blocks when they recur at least 3 times.
4. **Fallback:** split into parts of ~5,000 words on paragraph boundaries (a part closes at the
   first paragraph boundary at or after 5,000 words; a last part shorter than 1,000 words is merged
   into the previous one), titles `Part 1…`, all kind `chapter`, `chapters_detected=False`
   (contract rule 5).

**Step 2 – entries.** Text before the first boundary becomes a provisional entry titled
"Front matter" (**Decision D4,** kept as `front_matter` if it has ≥ 30 words after
clean-up, otherwise dropped). Heading blocks that are the boundary itself become the title, not a
paragraph. Entries with no paragraphs are merged forward (their title is dropped) — contract rule 4.

**Step 3 – kinds.** Keyword match on the normalized, lower-cased title (English and Montenegrin):
- front: `preface, foreword, introduction, dedication, to the reader, translator's note, title
  page, predgovor, uvod, posveta, riječ prevodioca`
- back: `notes, endnotes, footnotes, appendix, glossary, index, afterword, bibliography,
  references, bilješke, napomene, dodatak, prilog, pojmovnik, indeks, pogovor, literatura`
- dropped entirely (rule 9): `contents, table of contents, list of illustrations, illustrations,
  sadržaj`, and entries whose paragraphs are > 60 % TOC-like lines (short lines ending in a number
  or matching a TOC title).
- everything else: `chapter`.
Position rule (keeps contract rule 3): `front_matter` only before the first `chapter`,
`back_matter` only after the last one; a keyword entry in the middle becomes `chapter`.

**Decision D2 (DECIDED) — extra works after the main text.** The Prince PDF contains, after
CHAPTER XXVI, two other works that are not in the outline: "Description of the Methods Adopted by
the Duke Valentino…" (p.150–158) and "The Life of Castruccio Castracani of Lucca" (p.159–194).
Proposal: after the last TOC chapter, an all-caps heading block of ≥ 4 words starts a new entry of
kind `back_matter`. The book's 26 chapters stay 26, and the extra works do not end up inside
chapter XXVI (which would inflate its word count and distort Week 2 summaries).

**Step 4 – titles.** `normalize_text()` + `strip_footnote_markers()`; trailing `.` removed; original
case kept ("CHAPTER I. HOW MANY KINDS OF PRINCIPALITIES…" stays upper case; no Title Case — it
breaks names and roman numerals). Titles longer than 500 characters are cut at a word boundary.
Positions are assigned last, 1..n.

## 6. Book title and the generated file name

**Decision D3 (DECIDED by Dino, needs Daris's approval as contract 1.1):** stored uploads are named `<uuid>.<ext>`
(`api/books.py`), and the worker overwrites `books.title` with `parsed.title`. Contract §2 says
"otherwise the cleaned file name" — which would be the UUID. Options:
- (a) Contract 1.1: add `title_from_metadata: bool = True` to `ParsedBook`; the worker keeps the
  upload title when it is `False`. Non-breaking, needs both approvals.
- (b) Parser fallback = first heading of the book, then first line of text, never the file name.
  No contract change, but the rule in §2 must be reworded.
Chosen: (a). Until contract 1.1 is merged the parser returns the cleaned file stem (current
contract wording); afterwards it also sets `title_from_metadata=False` in that case.

## 7. Test books

| Book | Source | Expected (contract §7) | Measured |
|---|---|---|---|
| Pride and Prejudice | gutenberg.org/ebooks/1342, EPUB no images (see below) | 61 × `chapter` | NCX has exactly 61 chapter entries (A2); final count in A4 |
| The Prince | planetebook.com/free-ebooks/the-prince.pdf | 26 × `chapter`, introduction + dedication `front_matter` | outline has exactly 26 chapters; dedication is NOT in the outline (needs keyword rule); 2 extra works after ch. XXVI (D2) |
| Meditations | gutenberg.org/ebooks/2680, EPUB3 with images | 12 × `chapter`, introduction `front_matter`, notes `back_matter` | nav has 12 book entries + INTRODUCTION, APPENDIX, NOTES, GLOSSARY (A2); "HIS FIRST BOOK" needs care in A4 |

If a measured count differs from the contract table, the test uses the measured value only after
a manual check, and contract §7 is updated with Daris (it is a shared file).

### EPUB inspection (A2, 23.11.2026)

gutenberg.org was down (503); both files come from the official mirror `gutenberg.pglaf.org`.
P&P "EPUB3 with images" is 24.9 MB, so the no-images file (EPUB 2, 0.6 MB) is used (Dino).
Details in `backend/tests/fixtures/books/SOURCES.md`.

| | Pride and Prejudice (`pg1342.epub`) | Meditations (`pg2680-images-3.epub`) |
|---|---|---|
| OPF metadata | EPUB 2.0; `dc:title` "Pride and Prejudice"; `dc:creator` "Jane Austen" (no role); `dc:language` "en" | EPUB 3.0; `dc:title` "Meditations"; `dc:creator` "Emperor of Rome Marcus Aurelius" (role `aut` via `refines`); `dc:language` "en" |
| Spine | 17 XHTML: cover wrapper (image only, no text), `pg-header` file (licence header + printed TOC + preface + Chapter I), 15 text files; the footer is at the end of the last file | 19 XHTML: cover wrapper, `pg-header` file (licence header, title, printed TOC), 16 text files, `pg-footer` file |
| Navigation | NCX only, 63 entries, all level 1: book title, 61 chapters, licence | nav document (`toc.xhtml`) + NCX; 23 entries, levels 1–2 |
| Gutenberg header/footer | `<div id="pg-header" class="pg-boilerplate">` and `<div id="pg-footer" class="pg-boilerplate">`; the `*** START/END ***` lines are inside them | `<header id="pg-header" class="pg-boilerplate">` and `<footer id="pg-footer" class="pg-boilerplate">`; markers inside |
| Chapter headings | `<h2><a id="CHAPTER_II"/><span class="caption">I hope Mr. Bingley will like it.</span><br/><br/>CHAPTER II.</h2>`: illustration caption inside the heading (dropped by the reader); spelling varies: "Chapter I.", "CHAPTER XIII" (no dot), "CHAPTERXXVII." (no space) | `<h2>` "THE FIRST BOOK" … "THE TWELFTH BOOK", anchor ids `link2H_4_0001`…; level-3 sub-headings |
| NCX/nav titles | contain the caption too: "He rode a black horse. CHAPTER III." → A4 should take the title from the heading block, not from the TOC label | clean |
| Nav entries that look like chapters | **61** (`CHAPTER I`…`LXI`) ✔ matches contract | **12** ("THE FIRST BOOK"…"THE TWELFTH BOOK") ✔; plus "HIS FIRST BOOK" (heading before Book 1) with level-2 "concerning HIMSELF:", which A4 must not count as a chapter |
| Other nav entries | "PRIDE. and PREJUDICE" (title page) | MEDITATIONS, "MARCUS AURELIUS ANTONINUS THE ROMAN EMPEROR", BOOKS (printed TOC), INTRODUCTION, APPENDIX, NOTES, GLOSSARY, "Paragraphs with First Lines" (level 2 under GLOSSARY) |
| Licence nav entry | "THE FULL PROJECT GUTENBERG™ LICENSE": dropped (points into the removed footer) | same, dropped |
| Text before chapter 1 | printed TOC, colophon, frontispiece caption, title page, dedication, PREFACE by George Saintsbury | title, printed TOC ("BOOKS"), INTRODUCTION |
| Reader output | 2,249 blocks, 126,676 words, 0.5 s | 1,173 blocks, 77,440 words, 0.2 s |

Reader notes found during inspection: Gutenberg XHTML uses self-closing `<a id="…"/>` and `<div/>`,
which the HTML parser turns into open elements that swallow the rest of the document. The reader
therefore parses well-formed XHTML with the XML parser and only falls back to the HTML parser for
files that are not well-formed. Page-number spans (`x-ebookmaker-pageno`) contain no text.

## 8. Known limitations (go to README at integration)

Multi-column PDFs, scanned PDFs (OCR is a stretch goal), poetry line breaks joined into one
paragraph, hard hyphens at line end always joined, footnotes dropped, PDFs without outline rely on
font-size heuristics, tables flattened to text. PDF: paragraphs without a first-line indent or a
vertical gap between them are merged into one (e.g. the-prince.pdf pp. 188–190); indented poetry
lines become separate paragraphs.
