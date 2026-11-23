# parse_book contract — version 1.1

This is the agreement between the two tracks of bookcrunch.ai:

| Track | Owner | Role in the contract |
|---|---|---|
| A — parsing and LLM pipeline | Dino Đerekerac | **Producer**: implements `parse_book()` |
| B — platform, API, UI | Daris Velović | **Consumer**: the worker calls `parse_book()` and stores the result |

The code of the contract is `backend/app/parsing/contract.py`. This document explains it.
If the two ever disagree, the code wins and this document must be fixed.

## 1. The function

```python
from pathlib import Path
from app.parsing.contract import ParsedBook

def parse_book(file_path: Path) -> ParsedBook: ...
```

- Input: path to a stored upload. The file may have **no extension**, so the format is detected by
  file signature (`PK\x03\x04` for EPUB, `%PDF` for PDF).
- Output: a `ParsedBook` that passes `validate_parsed_book()`.
- The real implementation lives in `backend/app/parsing/parser.py`.
- The worker never imports `parser.py` directly. It calls `get_parse_book()` from
  `app.parsing`, which picks the implementation from the `BOOKCRUNCH_PARSER` environment variable
  (`fake` or `real`, default `real`).

## 2. The data

```
ParsedBook
├── title: str                 metadata title, otherwise the cleaned file name; never empty
├── title_from_metadata: bool  False when title is only the cleaned file name (default True)
├── author: str | None
├── language: str | None       lowercase ISO 639 code: "en", "it", "cnr"; "en-US" becomes "en"
├── file_format: FileFormat    "epub" | "pdf"
├── chapters_detected: bool
└── chapters: tuple[Chapter]
      ├── position: int        1, 2, 3 ... in reading order, counted over all kinds
      ├── title: str
      ├── kind: ChapterKind    "front_matter" | "chapter" | "back_matter"
      ├── word_count           (property, computed)
      └── paragraphs: tuple[Paragraph]
            ├── text: str      one clean paragraph
            └── page: int|None 1-based PDF page where the paragraph starts; None for EPUB
```

All classes are frozen dataclasses. Collections are **tuples**, not lists.

Uploaded files are stored as `<uuid>.<ext>`, so the "file name" the parser sees is a UUID. When the
book has no title in its metadata, the parser still returns a non-empty `title` (the cleaned file
name) but sets `title_from_metadata = False`; the worker then keeps the title from the upload
(section 5, rule 4).

## 3. Rules for the producer (Track A)

Every rule below is checked by `validate_parsed_book()` unless marked *(not checked)*.

**Structure**
1. At least one entry, and at least one entry of kind `chapter`.
2. Positions are exactly `1..n`, in reading order.
3. Order of kinds: `front_matter*` → `chapter+` → `back_matter*`.
4. Every entry has at least one paragraph. Empty headings (for example a page that only says
   "VOLUME I") are dropped or merged into the next entry.
5. If no chapters can be detected, the book is split into parts of about 5,000 words on paragraph
   boundaries, every part is kind `chapter`, titled `Part 1`, `Part 2` …, and
   `chapters_detected = False`.

**Text**
6. Every paragraph text and every title goes through `normalize_text()`: NFC Unicode, no soft
   hyphens or zero-width characters, every whitespace run collapsed to one space, trimmed.
   A paragraph therefore never contains a newline.
7. Words hyphenated across a line break are joined (`con-` + `tent` → `content`). *(not checked)*
   Known limitation: a real compound broken exactly at its hyphen (`well-` / `known`) cannot be told
   apart from a hyphenated word and is joined too (`wellknown`).
8. PDF running headers, footers and page numbers are removed. Footnotes are dropped in the MVP,
   together with their markers in the text and in titles (for example `CHAPTER XVIII[*].` becomes
   `CHAPTER XVIII.`). *(not checked)*
9. The table of contents and the list of illustrations printed inside the book are not returned as
   text. *(not checked)*
10. Project Gutenberg licence header and footer are removed completely; they are not front or back
    matter. *(not checked)*

**Kinds** *(not checked)*
- `front_matter`: title page text, dedication, preface, foreword, introduction by an editor or
  translator.
- `chapter`: the author's main text, including prologue and epilogue.
- `back_matter`: notes, appendix, glossary, index, afterword, bibliography, and other works
  printed after the main text (for example the two shorter works after chapter XXVI of The Prince).

**Pages**
11. EPUB: every `page` is `None`.
12. PDF: every `page` is an integer ≥ 1 and pages never decrease through the book.

**Behaviour** *(not checked)*
13. No database, no network, no writing files, no global state.
14. Deterministic: the same file always gives the same result.
15. Expected failures raise only `ParseError` with one of the codes below. Any other exception is a
    bug.
16. A 300-page book is parsed in under 60 seconds.

## 4. Errors

| Code | When | Message shown to the user |
|---|---|---|
| `UNSUPPORTED_FORMAT` | Signature is neither EPUB nor PDF | This file is not an EPUB or a PDF. |
| `CORRUPT_FILE` | File cannot be opened or read | The file is damaged and could not be read. |
| `ENCRYPTED_FILE` | DRM or password protection (see note below) | The file is protected (DRM or password) and cannot be processed. |
| `NO_TEXT_LAYER` | PDF averages fewer than 100 characters of text per page | This PDF contains scanned images without text. Scanned books are not supported yet. |
| `EMPTY_BOOK` | No main text left after clean-up | No readable text was found in this book. |

An EPUB whose `META-INF/encryption.xml` covers only fonts (IDPF or Adobe font obfuscation) is not
protected: the text is readable, so it is parsed normally and never raises `ENCRYPTED_FILE`.

```python
raise ParseError(ParseErrorCode.NO_TEXT_LAYER, "12 chars/page on average")
```

`detail` is for logs; `user_message` (from the table) is what the UI shows.

## 5. Rules for the consumer (Track B)

1. Get the function with `get_parse_book()`; never import `parser.py` or `fake.py` in worker code.
2. Call `validate_parsed_book()` on every result **before** saving anything.
3. Map the outcome to the book:

   | Outcome | `books.status` | `books.error_code` |
   |---|---|---|
   | Valid `ParsedBook` | `ready` (Week 1) | `NULL` |
   | `ParseError` | `failed` | `error.code` |
   | `ContractViolation` | `failed` | `PARSER_CONTRACT_VIOLATION` |
   | Any other exception | `failed` | `PARSER_INTERNAL_ERROR` |

   For the last two, log the full error and show a generic message; they are bugs for Track A.
4. Store from the result: book `title` (only when `title_from_metadata` is True; otherwise keep the
   title from the upload), `author`, `source_language` ← `language`, `word_count`,
   `chapters_detected`; for each entry a `chapters` row with `position`, `title`, `kind`,
   `word_count`. In Week 1 paragraph text is not stored; chunks are added in Week 2.
5. Use `count_words()` whenever words are counted anywhere else in the project.
6. Saving is idempotent: re-processing a book replaces its chapters inside one transaction.

## 6. Fake parser (until the real one is merged)

Set `BOOKCRUNCH_PARSER=fake`. `fake_parse_book()` returns a book with Preface, three chapters and
Notes, and follows every rule above. For testing the worker:

| Variable | Effect |
|---|---|
| `FAKE_PARSER_ERROR=NO_TEXT_LAYER` | raises `ParseError` with that code (any code from section 4) |
| `FAKE_PARSER_DELAY=3` | waits 3 seconds, to see progress in the UI |
| `FAKE_PARSER_FORMAT=pdf` | returns a PDF-shaped book with page numbers |

At integration (end of Week 1) the value changes to `real`.

## 7. Test books and expected results

Track A writes tests that parse each book, call `validate_parsed_book()` and compare with:

| Book | File | Expected (verify on the real file) |
|---|---|---|
| Pride and Prejudice | EPUB, Project Gutenberg | 61 entries of kind `chapter`; no Gutenberg licence text |
| The Prince | PDF, Planet eBook | 26 entries of kind `chapter`; introduction and dedication as `front_matter`; "Description of the Methods Adopted by the Duke Valentino…" and "The Life of Castruccio Castracani" as `back_matter`; no "The Prince" footer lines; no page numbers in text |
| Meditations | EPUB, Project Gutenberg | 12 entries of kind `chapter` (the twelve books); introduction as `front_matter`; notes as `back_matter` |

Test files are kept in `backend/tests/fixtures/books/` (public domain, safe to commit).

## 8. Changing the contract

1. Open a pull request that changes `contract.py`, this document and `test_contract.py` together.
2. Bump `CONTRACT_VERSION` (new optional field → 1.1; anything breaking → 2.0) and add a line to the
   changelog below.
3. The other developer must approve it before merge.

## Changelog

| Version | Date | Change |
|---|---|---|
| 1.0 | 20.11.2026 | First version |
| 1.1 | 23.11.2026 | New field `title_from_metadata` (the worker keeps the upload title when it is False); footnote markers and the list of illustrations are dropped; font obfuscation is not `ENCRYPTED_FILE`; other works after the main text are `back_matter`; known limitation of rule 7. Proposed by Dino Đerekerac after checking the contract on the-prince.pdf |
