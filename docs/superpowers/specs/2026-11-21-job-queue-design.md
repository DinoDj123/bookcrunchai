# Job queue and worker (task B3) — design

Approved by Daris Velović on 21.11.2026. Track B.

## Intent

Week 1 milestone item "job queue" (specification 11.1/11.2). The worker gets a real
`process_book` task so that a queued job turns an uploaded file into a parsed book with chapters,
with status visible in PostgreSQL (FR-012) and no book ever stuck in `processing` (section 4.5).

## Goals

- `enqueue_job(job_id)`: the API side of "write a job row, enqueue its ID in Redis" (section 5.4).
- `process_book(ctx, job_id)`: runs the parsing stage and stores the result as the parse_book
  contract, section 5, prescribes.
- Job and book status transitions as in Appendix C.

## Non-goals

- Upload endpoint and `GET /api/v1/jobs/{id}` (next task, "upload and status").
- Chunking, embedding, chapter notes, `generate_summary` (Week 2+).
- Any change to the shared contract files.

## Anti-goals

- A book left in `processing` after its job stopped.
- Duplicate chapters after a retry or re-processing.
- Worker code that depends on anything outside `app.parsing`'s public names.

## Approaches

1. **Chosen: ARQ task that reads and writes PostgreSQL; ARQ retries only infrastructure errors.**
   Parser outcomes (ParseError, ContractViolation, any exception from the parser) fail the job at
   once: the same file would fail the same way. Other exceptions (database, Redis) are re-raised so
   ARQ retries, up to 3 tries; the last try marks job and book failed.
2. Retry everything 3 times, as Appendix C literally says. Rejected: three slow, identical parser
   failures before the user sees the error.
3. Own retry loop inside the task instead of ARQ's. Rejected: re-implements what ARQ already does
   and does not survive a worker restart.

## Design

- `app/worker/queue.py` — `enqueue_job(redis, job_id)`. The ARQ job ID is our job UUID, so the same
  job cannot be queued twice.
- `app/worker/tasks.py` — `process_book(ctx, job_id)`:
  1. Load job and book. Missing job, or job already `done`: return without doing anything.
  2. Job → `running`, stage `parsing`, progress 0, `attempts` + 1, `started_at`; book →
     `processing`, `error_code` NULL. Commit, so polling sees it.
  3. `parse = get_parse_book()`, run with `asyncio.to_thread` (the parser is synchronous and may
     take up to 60 s), then `validate_parsed_book()`.
  4. One transaction: delete the book's chapters, insert the new ones (`position`, `title`, `kind`,
     `word_count`), set book `title`, `author`, `source_language`, `word_count`,
     `chapters_detected`, status `ready`; job → `done`, progress 100, `finished_at`.
- Failure mapping (contract section 5.3):

  | Outcome | `books.error_code` / `jobs.error_code` | `jobs.error_message` |
  |---|---|---|
  | `ParseError` | `error.code` | `error.user_message` |
  | `ContractViolation` | `PARSER_CONTRACT_VIOLATION` | generic message |
  | Other exception from the parser | `PARSER_INTERNAL_ERROR` | generic message |
  | Other exception elsewhere, last try | `INTERNAL_ERROR` | generic message |

  The last three are logged with the full traceback.
- `WorkerSettings`: `functions = [process_book]` (plus `ping`), `max_tries = 3`.
- Tests call the task directly against the test database with the fake parser; no Redis. Covered:
  success, each failure row above, re-processing replaces chapters, retry then success, third
  failed try.

## Deviation from the specification

Appendix C says "failed after 3 attempts". Parser failures fail on the first attempt (approach 1).
The specification is updated in the same pull request.

## Left to the builder

- How the task gets its database session (ARQ `ctx` or a session factory argument) and how tests
  inject theirs.
- Exact wording of the generic messages.
