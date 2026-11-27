# Pipeline contract (chunking and chapter notes) — version 1.0

This is the agreement between the two tracks of bookcrunch.ai for the steps after parsing:

| Track | Owner | Role in the contract |
|---|---|---|
| A — parsing and LLM pipeline | Dino Đerekerac | **Producer**: implements `chunk_chapter()` and `write_chapter_notes()` |
| B — platform, API, UI | Daris Velović | **Consumer**: the worker calls them after `parse_book()` and stores the result |

The code of the contract is `backend/app/pipeline/contract.py`. This document explains it.
If the two ever disagree, the code wins and this document must be fixed.

It grew out of Daris's draft `chunk_chapter` contract 0.1 (25.11.2026). The answers to its open
questions are in section 9.

## 1. Why a contract

- Paragraphs are **not stored** in the database: the worker keeps only `position`, `title`, `kind`
  and `word_count` of every chapter (`parse_book` contract 5.4). So chunking runs in the same job,
  right after parsing, on the `ParsedBook` still in memory.
- The chunks are then the only stored copy of the book's text. The chapter reader, the chapter
  notes, the embeddings and the citations all read the text from them. That is why section 3 asks
  that no text is lost or changed.
- Chapter notes are written from the **stored chunks**, not from `ParsedBook`: a retried job that
  skips parsing (rule 4.6) has no `ParsedBook`, only chunks.

## 2. The functions and the data

```python
from app.parsing import Chapter                        # the parse_book contract's Chapter
from app.pipeline.contract import ChunkDraft, ChapterForNotes
from app.llm.base import LLMClient

def chunk_chapter(chapter: Chapter) -> tuple[ChunkDraft, ...]: ...

async def write_chapter_notes(
    chapter: ChapterForNotes, *, language: str | None, llm: LLMClient, model: str,
    budget_tokens: int,
) -> str: ...
```

- The real implementations live in `backend/app/pipeline/chunking.py` and
  `backend/app/pipeline/notes.py`.
- The worker never imports them directly. It calls `get_chunk_chapter()` and
  `get_write_chapter_notes()` from `app.pipeline`, which pick the implementation from
  `BOOKCRUNCH_CHUNKER` and `BOOKCRUNCH_NOTES` (`fake` or `real`, default `real`), like
  `get_parse_book()`.

```
ChunkDraft                       frozen dataclass
├── position: int        1, 2, 3 ... inside the chapter
├── text: str            the chunk's paragraphs joined with "\n\n" (PARAGRAPH_SEPARATOR)
├── word_count: int      count_words(text)
├── token_count: int     estimate_tokens(text)
└── page_start: int|None 1-based PDF page where the chunk starts; None for EPUB

ChapterForNotes                  frozen dataclass
├── position: int
├── title: str
├── kind: ChapterKind
└── chunks: tuple[str]   the stored chunk texts, in order
```

`ChunkDraft` has no IDs: the worker adds `book_id` and `chapter_id` when it stores it as a
`chunks` row (specification 6.2). `embedding` stays `NULL` until the embedding step (week 3).

**Text format.** Paragraphs never contain a newline (`parse_book` rule 6), so
`text.split("\n\n")` gives the paragraphs of a chunk back, and the reader can show them as
paragraphs. A piece of a split long paragraph (rule 3) contains no `"\n\n"`.

**Token count.** `estimate_tokens(text) = ceil(len(text) / 4)`. Pure and deterministic, the same
number everywhere; only an estimate (some 20 % off, more for Montenegrin). It is used for budgets
and display, never for billing: cost comes from the token counts the API returns (`llm_usage`).
Nobody counts tokens any other way, like `count_words()` for words.

## 3. Chunking: rules for the producer (Track A)

Every rule below is checked by `validate_chunks(chapter, chunks)` unless marked *(not checked)*.

**Sizes** (FR-006)
1. The target is about **350 words** (`TARGET_CHUNK_WORDS`), the maximum **500 words**
   (`MAX_CHUNK_WORDS`), counted with `count_words`.
2. Paragraphs are grouped **in order**. A paragraph is never cut, except rule 3.
3. A paragraph longer than 500 words is split on **sentence boundaries** into pieces of at most 500
   words, and every piece is its own chunk. A single sentence longer than 500 words is split
   between words. *(how sentences are found is not checked)*
4. Every chunk has at least one word and at most 500.
5. Chunks shorter than 100 words appear only when the chapter itself is that short or when a split
   paragraph leaves a short last piece; a short last chunk is merged into the one before it if the
   result stays within 500 words. *(not checked: a guideline)*

**Nothing lost, nothing changed**
6. The words of all chunks, in order, are exactly the words of the chapter's paragraphs, in order.
   No word is dropped, repeated or changed.
7. Inside a chunk, paragraphs are joined with `"\n\n"` and nothing else. No other text is added
   (no titles, no "Chapter 3:" prefix, no "…").

**Order and pages**
8. One chapter at a time: a chunk never crosses a chapter boundary. Positions are exactly `1..n`.
9. EPUB: every `page_start` is `None`.
10. PDF: `page_start` is the page of the chunk's first paragraph (for a piece of a split paragraph,
    that paragraph's page) and never decreases through the chapter.

**Counts**
11. `word_count == count_words(text)` and `token_count == estimate_tokens(text)`.

**Behaviour** *(not checked)*
12. No database, no network, no writing files, no global state.
13. Deterministic: the same chapter always gives the same chunks. Citations stored for a chunk
    must keep pointing to the same text when a book is processed again.
14. There are no expected failures: every chapter that passed `validate_parsed_book()` can be
    chunked. Any exception is a bug.
15. Fast: Pride and Prejudice (about 126,000 words) is chunked in under 2 seconds.

A simple sentence rule is enough for the MVP: `.`, `!`, `?` or `…`, optionally followed by a closing
quote, then a space and an upper-case letter or an opening quote. Abbreviations such as "Mr. Darcy"
sometimes split a sentence; that only moves a chunk boundary and loses nothing (rule 6 still holds).

## 4. Chunking: rules for the consumer (Track B)

1. Get the function with `get_chunk_chapter()`; never import `chunking.py` or `fake.py` in worker
   code.
2. Stage `chunking` (`jobs.stage`) is committed before chunking starts, so the UI shows it.
3. Call `chunk_chapter()` for every chapter, in a thread (`asyncio.to_thread`), and
   `validate_chunks()` on every result **before** saving anything.
4. Map the outcome:

   | Outcome | `books.status` | `jobs.error_code` |
   |---|---|---|
   | Valid chunks for every chapter | stays `processing`; the next stage starts | — |
   | `PipelineContractViolation` | `failed` | `CHUNKER_CONTRACT_VIOLATION` |
   | Any other exception | `failed` | `CHUNKER_INTERNAL_ERROR` |

   Both fail the job at once: chunking is deterministic, so another try would fail the same way.
   The user sees the generic message; the error goes to the log as a bug for Track A.
5. Chapters and their chunks are stored in **one transaction**: re-parsing replaces both.
6. A retried job continues from the last completed step (specification 4.5): if the book already
   has chapters with chunks, parsing and chunking are skipped, so notes already paid for are kept.
7. `chunks.position` is the chunk's `position`; the unique index on (`chapter_id`, `position`)
   holds because rule 8 holds.

## 5. Which chapters get notes

`NOTES_KINDS = {chapter, front_matter}`; `needs_notes(kind)` says whether a chapter gets notes.
`back_matter` (notes, index, other works printed after the main text) does not: it adds cost and
little to a summary of the book. The summary step (FR-008) works from the notes that exist.

## 6. Chapter notes: rules for the producer (Track A)

FR-007. The input is a `ChapterForNotes` built from the stored chunks of one chapter whose kind is
in `NOTES_KINDS`.

1. **Language.** `language` is `books.source_language`; the notes are written in that language.
   `None` means: the same language as the text.
2. **Model and client.** `llm` is made by the worker (`make_llm_client`), so the cost limit,
   retries and `llm_usage` recording are already handled; every call uses step `map`. `model` is
   the worker's setting (`settings.llm_model_fast`); the model name is configuration, not part of
   Track A's code.
3. **Budget.** `budget_tokens` is the input budget of **one** LLM request, counted with
   `estimate_tokens()`. A chapter that does not fit is split into **sections of whole chunks**,
   each section fits the budget, each is summarised and the results are merged into one set of
   notes. The worker reads the value from its settings (`NOTES_BUDGET_TOKENS`, default
   `DEFAULT_NOTES_BUDGET_TOKENS = 6000`, which leaves room for the prompt, the answer and the error
   of the estimate in a model context of 8,192 tokens).
4. **Output.** Plain text of about 150–300 words, only information present in the text, that
   passes `validate_chapter_notes()`: not empty, at most 500 words (`MAX_NOTES_WORDS`).
5. **Invalid answers.** If the model returns notes that fail `validate_chapter_notes()`, the
   request is made once more. If the second answer fails too, `write_chapter_notes()` raises
   `LLMError(LLM_REQUEST_FAILED, ..., retryable=True)`: the model, not the code, was wrong, so a
   later try of the job may succeed.
6. **Errors.** `LLMError` from the client is passed on unchanged. Any other exception is a bug.
7. No database, no file writes, no global state; the only network access is through `llm`.

## 7. Chapter notes: rules for the consumer (Track B)

1. Get the function with `get_write_chapter_notes()`; never import `notes.py` or `fake.py` in
   worker code.
2. Stage `chapter_notes`: notes for every chapter with `needs_notes(kind)` whose `notes` are still
   `NULL`. Chapters may run in parallel; the client's semaphore limits concurrency.
3. Call `validate_chapter_notes()` on every result before saving it. Each chapter's notes are
   committed as soon as they arrive, so a retried job writes only the missing ones.
4. Map the outcome:

   | Outcome | Job |
   |---|---|
   | Valid notes | stored; the book becomes `ready` when every chapter that needs notes has them |
   | `LLMError` with `retryable=True` | tried again later (on the last try fails with `error.code`) |
   | `LLMError` with `retryable=False` (also `COST_LIMIT_EXCEEDED`) | fails at once with `error.code` |
   | `PipelineContractViolation` | fails at once, `NOTES_CONTRACT_VIOLATION` |
   | Any other exception | fails at once, `NOTES_INTERNAL_ERROR` |

   Decide by `retryable`, not by the code: `LLM_REQUEST_FAILED` from rule 6.5 is retryable.

## 8. Fakes (until the real implementations are merged)

Set `BOOKCRUNCH_CHUNKER=fake` and `BOOKCRUNCH_NOTES=fake`.

- `fake_chunk_chapter()` makes one chunk per paragraph and cuts a longer paragraph every 500 words.
  It follows every checked rule, also on the three test books, but its chunks are smaller than the
  target and it ignores sentence boundaries.
- `fake_write_chapter_notes()` returns fixed notes made from the chapter title and its first
  words. It makes **no LLM call** and costs nothing.

| Variable | Effect |
|---|---|
| `FAKE_NOTES_DELAY=1` | waits 1 second per chapter, to see the `chapter_notes` stage in the UI |

At integration the values change to `real` (the default).

### Test books

`backend/tests/pipeline/test_contract.py` chunks every chapter of the fake book and of the three
test books (`backend/tests/fixtures/books/`) with the fake and calls `validate_chunks()` on each.
Track A's tests do the same with the real chunker, and add:

| Book | Check |
|---|---|
| Pride and Prejudice (EPUB) | every `page_start` is `None`; average chunk between 250 and 450 words |
| The Prince (PDF) | `page_start` on every chunk, never decreasing; paragraphs over 500 words are split |
| Meditations (EPUB) | the long books are split into several chunks, none over 500 words |

A small hand-written chapter tests the edge cases: one short paragraph, a 1,200-word paragraph, a
600-word sentence without a full stop.

## 9. Decisions on the draft 0.1

| Question | Decision |
|---|---|
| Folder `backend/app/pipeline/` with shared `contract.py`, `__init__.py`, `fake.py`, `tests/pipeline/test_contract.py` | Yes |
| Token count | Option A, `estimate_tokens = ceil(len / 4)`. Rejected: an API token counting call per chunk (network in a pure function); a local tokenizer (different for every model) |
| `"\n\n"` between paragraphs inside a chunk | Yes |
| Rule 5 (short chunks) | A guideline, not checked |
| Input of the notes | The stored chunks (`ChapterForNotes`), plus `budget_tokens` |
| Which chapters get notes | `chapter` and `front_matter`, not `back_matter` |

## 10. Changing the contract

1. Open a pull request that changes `contract.py`, this document and `test_contract.py` together.
2. Bump `CONTRACT_VERSION` (new optional field → 1.1; anything breaking → 2.0) and add a line to the
   changelog below.
3. The other developer must approve it before merge.

## Changelog

| Version | Date | Change |
|---|---|---|
| 1.0 | 28.11.2026 | First version, from Daris Velović's draft 0.1 (25.11.2026) with the decisions of section 9; adds `NOTES_KINDS` / `needs_notes`, `budget_tokens` and the rule for invalid notes |
