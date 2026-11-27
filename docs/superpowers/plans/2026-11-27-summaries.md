# Book summaries (B6) Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** generate_summary worker task, summary cache and the two summary endpoints, on a fake
reduce function until Track A's real one exists.

**Architecture:** The API creates a `summaries` row and a `generate_summary` job and enqueues it;
the worker runs the reduce function from `app.worker.pipeline_draft`, validates, stores the
summary, its chapter summaries and key ideas. A ready summary is returned from the database.

**Tech Stack:** FastAPI, SQLAlchemy async, ARQ, pytest with PostgreSQL (TEST_DATABASE_URL).

**Spec:** `docs/superpowers/specs/2026-11-27-summaries-design.md` (and `docs/specification.md`
FR-008, FR-009, FR-014, 7.2).

## Global Constraints

- Target words: 1,100 for `5min`, 3,300 for `15min`; accepted range ±15 %.
- Key ideas: 5–10 for `5min`, 10–15 for `15min`.
- Montenegrin output is requested in Latin script and ijekavian (Track A's prompt; the fake ignores it).
- No real LLM call in tests; `BOOKCRUNCH_SUMMARY=fake` is the only allowed value.
- Error format and codes as in specification 7.1/7.2; another user's book is 404 BOOK_NOT_FOUND.
- Python 3.12, Ruff (line length 100), type hints.

## Review Focus

- Two POSTs for the same new combination at the same moment → one summary, one job (unique-key race).
- Redis down when a summary is requested → 503 and nothing stuck in `queued`.
- Worker dies during a summary → `fail_stale_jobs` marks the summary `failed`, POST can retry it.
- Model returns a key idea for a chapter that does not exist → idea dropped, summary still stored.
- A summary job running on a ready book → book page does not show the book as processing.

---

### Task 1: Draft summary contract and fake (pipeline_draft.py)

**Files:** Modify `backend/app/worker/pipeline_draft.py`; Test `backend/tests/worker/test_pipeline_draft.py`

**Interfaces — Produces:**
- `TARGET_WORDS = {SummaryLength.FIVE_MIN: 1100, SummaryLength.FIFTEEN_MIN: 3300}`,
  `LENGTH_TOLERANCE = 0.15`, `KEY_IDEA_RANGE = {FIVE_MIN: (5, 10), FIFTEEN_MIN: (10, 15)}`
- dataclasses `ChapterForSummary(position: int, title: str, notes: str)`,
  `ChapterSummaryDraft(chapter_position: int, content: str)`,
  `KeyIdeaDraft(text: str, chapter_position: int)`,
  `SummaryDraft(overview: str, chapters: tuple[ChapterSummaryDraft, ...], key_ideas: tuple[KeyIdeaDraft, ...])`
- `summary_word_count(draft) -> int` (overview + chapter contents + key idea texts, `count_words`)
- `drop_invalid_key_ideas(draft, positions: set[int]) -> SummaryDraft`
- `validate_summary(draft, positions: set[int], length: SummaryLength) -> None` (raises `PipelineContractViolation`)
- `fake_write_book_summary(title, chapters, *, length, language, llm, model) -> SummaryDraft`
- `summary_implementation() -> str`, `get_write_book_summary() -> WriteBookSummaryFn` (env `BOOKCRUNCH_SUMMARY`)

- [ ] Tests: fake output passes `validate_summary` for both lengths and 1 and 40 chapters; word
  count within range; key idea count = lower bound; validate rejects empty overview, no chapters,
  unknown position, duplicate position, empty content, too short / too long, too few / too many
  key ideas; `drop_invalid_key_ideas` drops unknown positions and empty texts;
  `BOOKCRUNCH_SUMMARY=real` raises ValueError.
- [ ] Run, see fail; implement; run, see pass; commit.

### Task 2: generate_summary worker task

**Files:** Modify `backend/app/worker/tasks.py`, `backend/app/worker/settings.py`; Test
`backend/tests/worker/test_generate_summary.py`

**Interfaces — Consumes:** Task 1. **Produces:** `generate_summary(ctx, job_id) -> None`,
codes `NO_CHAPTER_NOTES`, `SUMMARY_INVALID`, `SUMMARY_INTERNAL_ERROR`; job params
`{"summary_id": str, "length": str, "language": str}`.

- [ ] Tests (fixture: ready book with chapters 1–4 with notes and 5 back matter without, a
  `queued` summary and job): stores overview, word_count, model `"fake"`, status `ready`, chapter
  summaries for 1–4, key ideas positions 1..n; job `done`, stage `reduce`, progress 100; ready
  summary is not rewritten; invalid then valid → stored after 2 calls; invalid twice →
  `SUMMARY_INVALID`, summary `failed`, book still `ready`; key idea for chapter 99 dropped;
  no notes → `NO_CHAPTER_NOTES`; COST_LIMIT → fails at once with cost message; LLM_UNAVAILABLE
  on try < 3 → `arq.Retry`, summary stays `running`; on try 3 → `LLM_UNAVAILABLE`; unexpected
  exception → `SUMMARY_INTERNAL_ERROR`; `fail_stale_jobs` fails the summary of a stale summary job;
  `generate_summary` is in `WorkerSettings.functions`.
- [ ] Extract `_llm_failure(ctx, job, error, where) -> Exception` shared by notes and summary
  mapping (returns JobFailed or re-raises retryable).
- [ ] Implement, run all worker tests, commit.

### Task 3: Summary endpoints

**Files:** Create `backend/app/api/summaries.py`; Modify `backend/app/api/router.py`,
`backend/app/api/library.py` (active_job only process_book), `backend/app/api/books.py`
(existing_upload only process_book); Test `backend/tests/api/test_summaries.py`

**Interfaces — Consumes:** `ReadableBook`, `QueueDep`, `enqueue_job`, models.

- [ ] Tests: POST new → 202 `{summary:{id,status:"queued"}, job:{id,status:"queued"}}`, one row
  each, job params, enqueued `generate_summary`; POST again while queued → 202 same ids, no new
  rows; POST ready → 200 full body (chapters in position order with titles, key ideas in order),
  nothing enqueued; POST failed → 202 new job, summary `queued`; book not ready → 409
  BOOK_NOT_READY; other user's book → 404; demo book of another user → 202; invalid length → 422;
  Redis down on new → 503 and no summary row; Redis down on failed → 503 and summary `failed`;
  unauthenticated → 401; GET ready → 200, `job` null; GET queued → `job` with stage/progress;
  GET failed → `job.error_code`; GET unknown → 404 SUMMARY_NOT_FOUND; GET missing query → 422;
  book's `active_job` ignores summary jobs; re-upload answer's job is the process_book job.
- [ ] Implement, run, commit.

### Task 4: Documentation

**Files:** `docs/specification.md` (7.2 GET/POST summary details, Appendix D decision "Summary
generation"), `README.md` (API list, background jobs, BOOKCRUNCH_SUMMARY), `.env.example`.

- [ ] Write, run ruff + full pytest, commit.
