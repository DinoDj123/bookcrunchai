# Book summaries: generate_summary, caching and endpoints (B6) — design

Status: approved by Daris on 27.11.2026. Track B. Requirements: FR-008, FR-009, FR-013, FR-014,
specification 7.2 and Appendix C (summary states).

## Intent

A reader picks a length (5 or 15 minutes) and a language (en or cnr) and gets a book summary built
from the chapter notes: an overview, a summary per chapter and key ideas linked to chapters. A
summary that exists is returned at once and never paid for twice (cache). This is the last part of
the M2 happy path on the backend.

Non-goals: the real reduce prompt (Track A: "prompts, summaries"), the summary UI, anything beyond a
per-user limit on started summary jobs.

## Approaches

1. **Queue a generate_summary job, as process_book does (chosen).** The API creates a `summaries`
   row (`queued`) and a `jobs` row and enqueues the job; the worker calls the reduce function,
   validates, stores. The UI polls `GET /jobs/{id}`. Same retries, cost limit, stale-job cleanup.
2. Generate inside the request. Rejected: a reduce call on a local 12B model takes minutes and would
   be lost on a timeout or restart (Decision Log: "Pipeline inside the request").
3. Generate all four summaries at the end of process_book. Rejected: four times the reduce cost
   for combinations nobody may ask for, and the user cannot choose.

## Design

### Draft contract (app/worker/pipeline_draft.py, temporary like the chunking draft)

- `ChapterForSummary(position, title, notes)` — the input, one per chapter that has notes.
- `SummaryDraft(overview, chapters: tuple[ChapterSummaryDraft(chapter_position, content)],
  key_ideas: tuple[KeyIdeaDraft(text, chapter_position)])` — the output.
- `write_book_summary(title, chapters, *, length, language, llm, model) -> SummaryDraft` — Track A
  writes the real one (prompt, JSON parsing); until then `fake_write_book_summary` makes no LLM
  call and returns a summary of exactly the target length. Chosen by `BOOKCRUNCH_SUMMARY=fake`
  (only value until Track A's code is merged). `FAKE_SUMMARY_DELAY` slows it for UI work.
- `validate_summary(draft, positions, length)` raises `PipelineContractViolation` for: empty
  overview; no chapter summaries; a chapter summary with an unknown or repeated chapter position
  or empty content; total words (overview + chapter summaries + key ideas) outside ±15 % of
  1,100 / 3,300; a key idea count outside 5–10 / 10–15.
- Before validation the worker drops key ideas whose chapter position is not one of the given
  chapters (FR-009 "discarded"), and key ideas with empty text.

### Worker: generate_summary (app/worker/tasks.py)

- `generate_summary(ctx, job_id)` = `run_job(..., _summarise, on_failure=_fail_summary)`,
  registered in `WorkerSettings.functions`.
- `job.params = {"summary_id", "length", "language"}`.
- `_summarise`: a summary that is already `ready` is left alone (cache). Otherwise the summary
  becomes `running`, the job stage `reduce`; the chapters with notes (front matter and chapters,
  as for notes) are read in order. None → `JobFailed("NO_CHAPTER_NOTES")`.
- The reduce function is called with the strong model and `UsageStep.REDUCE`. Invalid result →
  one more call; invalid again → `JobFailed("SUMMARY_INVALID")`. LLM errors are mapped exactly as
  for chapter notes (shared helper): cost limit and refusals fail at once, unavailable is retried by
  run_job and fails as `LLM_UNAVAILABLE` on the last try. Any other exception from the reduce
  function → `JobFailed("SUMMARY_INTERNAL_ERROR")`.
- Stored in one commit with the job becoming `done`: `summaries.overview, word_count, model,
  status=ready`, `chapter_summaries`, `key_ideas` (position 1..n). Old rows of that summary are
  deleted first, so nothing is duplicated.
- `_fail_summary` sets the summary `failed`; the book stays `ready`.
- `fail_stale_jobs` also marks the summary of a stale generate_summary job `failed`.
- Only the newest job of a summary may write or fail it; the summary row is locked and checked
  again before storing (added after review).
- The reduce step has its own time limit, 480 s, below the 600 s ARQ job timeout →
  `SUMMARY_TIMEOUT` (added after review).

### API (new app/api/summaries.py)

`POST /api/v1/books/{id}/summaries` with `{ length: "5min"|"15min", language: "en"|"cnr" }`:

- Book through `get_readable_book` (own or demo; demo books are allowed — the summary is a cached
  result, not a change to the book, and every reader of the demo book shares it). Not `ready` →
  409 BOOK_NOT_READY.
- The summary row is read with `FOR UPDATE`.
  - `ready` → **200** with the full summary (no job, no LLM cost).
  - `queued`/`running` → **202** with the summary and its latest job; a `queued` job is enqueued
    again (no-op if already queued; rescues a job the API saved but never enqueued).
  - `failed` → back to `queued` with a **new** job → 202.
  - none → new summary (`queued`) and job → 202. A concurrent POST that loses the unique-key race
    reads the winner's row and answers as above.
- Enqueue after commit. If Redis is down: **503 QUEUE_UNAVAILABLE**; the summary and its job stay
  `queued` and the next POST hands the job to the queue (changed after review: deleting raced with
  a second request that had already enqueued the same job).
- A new job counts against `SUMMARY_RATE_LIMIT` (20 per user per hour); a ready summary does not
  (added after review: a failing summary could be requested without end, and a demo book's cost
  limit is shared).
- 200 body (spec 7.2): `{ id, status, length, language, overview, chapters: [{ chapter_position,
  title, content }], key_ideas: [{ text, chapter_position }], word_count, job }`. 202 body:
  `{ summary: { id, status }, job: { id, status } }`.

`GET /api/v1/books/{id}/summaries?length=&language=`: the same 200 body for any status; `job` is
the latest job of that summary (`id, status, stage, progress, error_code, error_message`) so the
UI can poll after a reload or show why it failed; `null` for a ready summary. **404
SUMMARY_NOT_FOUND** if it was never requested. Invalid query → 422.

### Small fixes the new job type needs

- `GET /books/{id}` `active_job` and the repeated-upload answer use only `process_book` jobs, so a
  running summary does not look like the book being processed.

## Calls made by the builder (not discussed)

- Missing chapter summaries are allowed (small models skip chapters); unknown or duplicate ones are
  not.
- `summaries.model` is the model name used, `"fake"` for the fake implementation.
- A failed summary gets a new job rather than reusing the failed one (no ARQ "job ID still held"
  case, failed jobs stay as history).

## Open (for the report, not built)

- A 15-minute summary of a short book (under ~3,300 words) cannot meet the ±15 % rule. To agree with
  Track A: cap the target at a share of the book's length.
- The `chunk_chapter`/summary draft contract must be agreed with Track A before the real reduce
  function replaces the fake.
