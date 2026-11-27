# bookcrunch.ai — instructions for Claude

bookcrunch.ai: upload an EPUB or text-based PDF, get a faithful summary (5 or 15 minutes, English or
Montenegrin) and ask questions with answers that cite the exact passage. Four-week team project,
AI-DEV 2026, two developers.

The source of requirements is the Software Specification: read `docs/specification.md` (text copy of
the Word document sent to the mentors). Requirement IDs (FR-xxx), the data model (section 6) and the
API (section 7) come from there. If a task conflicts with the specification, point it out instead of
silently changing the design.

## Who owns what

Two developers work in parallel. Only change files owned by the developer you are working for.
If a task needs a change in the other track, stop and say so instead of editing their files.

| Area | Owner |
|---|---|
| `backend/app/parsing/` except the shared files below, `backend/tests/parsing/` except `test_contract.py`, `backend/tests/fixtures/books/`, `backend/app/pipeline/` except the shared files below (with `backend/app/pipeline/prompts/`), `backend/tests/pipeline/` except `test_contract.py`, `backend/app/api/summaries.py`, `backend/app/worker/summary_task.py`, `frontend/src/components/summary/`, `backend/scripts/pipeline_*.py`, later the rest of the LLM pipeline (summaries, retrieval) | **Track A — Dino Đerekerac** |
| `frontend/` except `src/components/summary/`, `backend/app/api/` except `summaries.py`, `backend/app/db/`, `backend/app/worker/` except `summary_task.py`, `backend/app/core/`, migrations, Docker, CI, README | **Track B — Daris Velović** |
| **Shared contracts:** `backend/app/parsing/contract.py`, `backend/app/parsing/__init__.py`, `backend/app/parsing/fake.py`, `backend/tests/parsing/test_contract.py`, `backend/app/pipeline/contract.py`, `backend/app/pipeline/__init__.py`, `backend/app/pipeline/fake.py`, `backend/tests/pipeline/test_contract.py`, `docs/contracts/` | **Both — change only with agreement** |

## The parse_book contract (must follow)

Read `docs/contracts/parse_book.md` before touching parsing or the worker.

- Never edit the shared contract files as part of an ordinary task. Propose the change to the user
  instead; it needs both developers' approval and a version bump.
- Track A: `parse_book(file_path) -> ParsedBook` in `backend/app/parsing/parser.py`. Use
  `normalize_text()` and `count_words()` from the contract. Raise only `ParseError`. No database,
  network or file writes in parsing code. Every test book must pass `validate_parsed_book()`.
- Track B: get the parser only through `get_parse_book()` from `app.parsing`. Call
  `validate_parsed_book()` before saving. Map errors exactly as in section 5 of the contract.
  Do not depend on anything that is not in `contract.py`.
- `pytest backend/tests/parsing/test_contract.py` must pass on every pull request.

## The pipeline contract (must follow)

Read `docs/contracts/pipeline.md` before touching chunking, chapter notes or the worker stages that
call them. The same rules for shared files apply as for `parse_book`.

- Track A: `chunk_chapter(chapter) -> tuple[ChunkDraft, ...]` in `backend/app/pipeline/chunking.py`
  and `write_chapter_notes(chapter, *, language, llm, model, budget_tokens) -> str` in
  `backend/app/pipeline/notes.py`. Use `count_words()` and `estimate_tokens()` from the contracts.
  Chunking is pure (no database, network or file writes) and raises nothing. Notes call the model
  only through `llm`, pass `LLMError` on unchanged and raise
  `LLMError(LLM_REQUEST_FAILED, retryable=True)` after two invalid answers. Every test book must
  pass `validate_chunks()`.
- Track B: get the functions only through `get_chunk_chapter()` and `get_write_chapter_notes()`
  from `app.pipeline`. Call `validate_chunks()` / `validate_chapter_notes()` before saving. Write
  notes only for chapters where `needs_notes(kind)` is true, with `budget_tokens` from the
  settings. Map errors as in sections 4 and 7 of the contract.
- `pytest backend/tests/pipeline/test_contract.py` must pass on every pull request.

## General rules

- Python 3.12 with type hints, Ruff; TypeScript strict, ESLint, Prettier.
- Never commit secrets: keys only in `.env` (git-ignored); document variables in `.env.example`.
- Never call paid APIs (Claude, embeddings) in automated tests; use fakes.
- Small, meaningful commits on feature branches (`feat/...`, `fix/...`); merge by pull request
  reviewed by the other developer.
- Record significant design decisions and rejected alternatives in the specification or README.
- Both developers must be able to explain every change: keep code simple and explain non-obvious
  choices in the pull request description.
