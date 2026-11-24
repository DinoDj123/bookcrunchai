# bookcrunch.ai

Upload an EPUB or a text-based PDF, get a faithful summary (5 or 15 minutes, English or
Montenegrin) and ask questions with answers that cite the exact passage.

Four-week team project, AI-DEV 2026. Requirements, data model and API: [`docs/specification.md`](docs/specification.md).
Parser contract between the two tracks: [`docs/contracts/parse_book.md`](docs/contracts/parse_book.md).

## Architecture

```
browser ──► Caddy (proxy) ──┬── /api/* ──► FastAPI (api) ──┬──► PostgreSQL + pgvector
                            │                              └──► Redis (job queue)
                            └── /*     ──► Next.js (frontend)        │
                                                                     ▼
                                         ARQ worker (same code as api) ──► PostgreSQL
```

A modular monolith with a separate background worker (specification section 5). The API and the
worker share one Python code base and one Docker image. The browser sees one origin, so the session
cookie needs no cross-site configuration.

| Folder | Contents |
|---|---|
| `backend/app/api/` | HTTP routes, all under `/api/v1` |
| `backend/app/core/` | Settings and the common error format |
| `backend/app/db/` | SQLAlchemy base and sessions |
| `backend/app/worker/` | ARQ worker and pipeline tasks |
| `backend/app/parsing/` | `parse_book()`: EPUB and PDF readers, chapter detection, and the shared contract |
| `backend/migrations/` | Alembic migrations |
| `frontend/` | Next.js (App Router), TypeScript, Tailwind CSS |

## Run locally with Docker

Requirements: Docker Desktop.

```bash
cp .env.example .env
docker compose up --build -d
docker compose run --rm api alembic upgrade head
```

| URL | What |
|---|---|
| http://localhost | Frontend |
| http://localhost/api/v1/health | Health check |
| http://localhost/api/v1/docs | OpenAPI documentation |

Stop with `docker compose down` (add `-v` to delete the database and uploaded files).

## Develop without rebuilding images

Run only PostgreSQL and Redis in Docker and the rest on your machine with hot reload:

```bash
docker compose up -d postgres redis

# backend (Python 3.12), from backend/
python -m venv .venv && source .venv/bin/activate   # Windows: .venv\Scripts\activate
pip install -e ".[dev]"
alembic upgrade head
uvicorn app.main:app --reload                        # http://localhost:8000/api/v1/docs
arq app.worker.settings.WorkerSettings               # in a second terminal

# frontend, from frontend/
npm install
npm run dev                                          # http://localhost:3000, /api is proxied to :8000
```

The backend reads `.env` from the folder it runs in; copy the root `.env` to `backend/.env`, or
export the variables.

## Checks (same as CI)

```bash
# backend/
ruff check . && ruff format --check .
pytest                                   # includes tests/parsing/test_contract.py

# frontend/
npm run lint && npm run format:check && npm run typecheck && npm run build
```

Automated tests never call paid APIs (Claude, Voyage); they use fakes. The API and worker tests
set `BOOKCRUNCH_PARSER=fake`, so they do not depend on the real parser; the parser has its own
tests in `tests/parsing`, which run on the three test books in `tests/fixtures/books`.

Database tests (`tests/db`) run only when `TEST_DATABASE_URL` is set; they drop and recreate the
schema, so point it at a separate database, never the development one:

```bash
docker compose exec postgres createdb -U bookcrunch bookcrunch_test
export TEST_DATABASE_URL=postgresql+asyncpg://bookcrunch:bookcrunch@localhost:5432/bookcrunch_test
pytest
```

## API so far

All under `/api/v1`; the full reference is the OpenAPI page at `/api/v1/docs`.

| Endpoint | What |
|---|---|
| `POST /auth/register`, `POST /auth/login` | Create an account or log in; both set the `session` cookie (JWT, 24 hours, httpOnly) |
| `POST /auth/logout`, `GET /auth/me` | End the session; current user |
| `POST /books` | Upload an EPUB or PDF (multipart field `file`, at most 50 MB): 202 with the book and its job, or 200 with the existing book if this user already uploaded the same file |
| `GET /jobs/{id}` | Status, stage and progress of a job; poll it while the job runs |

Rate limits (429 `RATE_LIMITED`, with `details.retry_after_seconds`): 10 login attempts per minute
per client address (`LOGIN_RATE_LIMIT`) and 20 uploads per hour per user (`UPLOAD_RATE_LIMIT`).

Try it with curl (the cookie is kept in `jar`):

```bash
curl -c jar -H 'Content-Type: application/json' \
  -d '{"email":"me@example.com","password":"correct horse"}' http://localhost/api/v1/auth/register
curl -b jar -F 'file=@book.epub' http://localhost/api/v1/books      # note the job id
curl -b jar http://localhost/api/v1/jobs/<job id>
```

Uploaded files are stored in `STORAGE_DIR` as `<book id>.epub` or `<book id>.pdf`; the name the user gave is used only for the first title. If Redis cannot be reached the upload is refused with 503 and the book is removed again. Uploading a file whose job is still `queued` hands that job to the queue again, which rescues a job that was saved but never enqueued. The session and the declared size are checked before the request body is read; files over 51 MB are already refused by Caddy, with its own (non-JSON) 413.

## Background jobs

The API saves a row in `jobs` and puts only its ID on the Redis queue (`app/worker/queue.py`); the
worker (`app/worker/tasks.py`) reads the job and the book from PostgreSQL, runs the parser and
writes status, chapters and errors back. PostgreSQL is the only place where job status lives.

- A file the parser rejects (damaged, protected, scanned, not a book) fails the job at once with
  the parser's error code; the book is marked `failed` too.
- Other errors (database, network) are retried by ARQ, 3 attempts in total, then the job and the
  book are marked `failed` with `INTERNAL_ERROR`. An attempt that died with the worker (killed,
  out of memory) counts too.
- Every 5 minutes the worker fails jobs that are still `running` 12 minutes after they started
  (`fail_stale_jobs`), so a book never stays in `processing`. After a worker is killed, its job
  is started again only when ARQ's lock on it expires, about 10 minutes later.
- With `BOOKCRUNCH_PARSER=fake`, set `FAKE_PARSER_DELAY=3` or `FAKE_PARSER_ERROR=NO_TEXT_LAYER` on
  the worker to see a slow or a failing job (`docs/contracts/parse_book.md`, section 6).

## Parsing

`parse_book()` (`backend/app/parsing/`) turns a stored EPUB or text-based PDF into a `ParsedBook`:
title, author, language and an ordered list of entries (front matter, chapters, back matter), each
with its paragraphs. The format is detected by file signature, not by extension. EPUB files are
read with `zipfile` and BeautifulSoup, PDF files with PyMuPDF. Chapters come from the table of
contents of the file (EPUB navigation, PDF outline), otherwise from heading patterns such as
"Chapter 7" or "Glava 3"; when nothing is found the book is split into parts of about 5,000 words.
The rules are in [`docs/contracts/parse_book.md`](docs/contracts/parse_book.md), the design in
[`docs/superpowers/specs/2026-11-23-parser-design.md`](docs/superpowers/specs/2026-11-23-parser-design.md).

`BOOKCRUNCH_PARSER=real` (the default) uses this parser; `fake` returns a small fixed book and is
meant for tests and for working on the API or the UI.

Result of uploading the three test books through the API and the worker (23.11.2026):

| Book | Format | Chapters | Front matter | Back matter | Words |
|---|---|---|---|---|---|
| Pride and Prejudice | EPUB | 61 | 2 | 0 | 125,920 |
| The Prince | PDF | 26 | 3 | 2 | 47,574 |
| Meditations | EPUB | 12 | 2 | 3 | 71,819 |

### Known limitations

- Scanned PDFs (pictures of pages without text) are not supported; there is no OCR. The job fails
  with `NO_TEXT_LAYER`.
- PDFs with several columns per page may be read in the wrong order.
- Poetry line breaks are lost: a poem may come out as one paragraph (or one paragraph per indented
  line in PDFs).
- A hyphen at the end of a line, followed by a lower-case letter, is treated as a word break and
  removed ("well-" + "known" becomes "wellknown").
- Footnotes are dropped, so they are not part of the summary or the answers.
- In PDFs, paragraphs without an indent or blank space between them can be merged into one.
- Tables are turned into plain text.
- PDFs without a chapter outline are split by heading patterns and font size, so chapters may be
  wrong or fall back to "Part N".
- When the table of contents has parts with named chapters under them ("Part I" with "The Storm",
  "The Calm"), each part can become one long entry.
- An unnumbered section after the last numbered chapter, such as "Conclusion", is marked as back
  matter.
- The printed table of contents is detected heuristically: an unusual one may remain in the text,
  and a list of short lines that end with numbers (a chronology) may be dropped with it.
- A "Prologue" or "Epilogue" that is not in the book's table of contents does not start its own
  section.
- A damaged PDF that PyMuPDF can repair is parsed without an error, possibly with pages missing.
- The language is taken from the file's metadata; PDFs often have none, and then it is unknown.
- A file without a title in its metadata gets its title from the file name, not from the text of
  the book.

## Deployment

One Linux VM with Docker. On the host: `git pull`, set `ENVIRONMENT=production`, a random `JWT_SECRET` (the API, the worker and the migrations refuse to start with the example value once `SITE_ADDRESS` is a domain) and `SITE_ADDRESS` to the domain in `.env`
(Caddy then gets an HTTPS certificate), `docker compose up --build -d`,
`docker compose run --rm api alembic upgrade head`.

## Design decisions

- **Health check does not touch the database.** It tells whether the API process is alive; database
  and Redis readiness are checked by their own Compose health checks.
- **Code is not pip-installed in the image.** Only the dependencies are installed (from
  `pyproject.toml`); the code runs from `/app` via `PYTHONPATH`, so a code change rebuilds one
  small layer.
- **`next.config.ts` rewrites `/api` to `localhost:8000`.** Only used by `npm run dev`; in Docker
  Caddy sends `/api` to the backend before Next.js sees it.
- **Shared contract files are excluded from Ruff** until both developers agree to reformat them.
- **Data model in one module** (`backend/app/db/models.py`), one class per table of specification
  section 6.2. Allowed values (statuses, lengths, languages) are Python `StrEnum`s and VARCHAR
  columns with CHECK constraints, not PostgreSQL ENUM types, so adding a value does not need
  `ALTER TYPE`. Chapter kind and file format reuse the enums of the parser contract.
- **Everything derived from a book is deleted with it** by `ON DELETE CASCADE`, including the links
  between derived rows (chapter summaries, key ideas, citations). `llm_usage.job_id` is
  `SET NULL`, so cost records outlive jobs.
- **Links can never cross books.** Chunks, chapter summaries, key ideas and citations store
  `book_id` and point to their chapter, summary, question or chunk with a composite foreign key
  such as `(chapter_id, book_id) → chapters (id, book_id)` (`same_book()` in `models.py`). A bug
  that mixes IDs fails loudly instead of showing a passage of another user's book. Emails have a
  lower-case CHECK. See specification 1.2, Appendix D.
- **Rate limits are counters in Redis** (`backend/app/api/rate_limit.py`): `INCR` with an expiry
  set by the first request of a window, in one transaction. No new library, and the limit holds
  across several API processes. Login is counted per client address, not per email, so nobody
  can lock another user out by guessing at their address. If Redis cannot be reached the request
  is let through: a Redis outage must not stop people from logging in.
- **Migrations are checked in CI.** `tests/db` applies all migrations to an empty database, runs
  `alembic check` (fails if a model changed without a migration) and checks the constraints.

## Known limitations

Written as features land.

- Registration is not rate-limited (the specification asks for limits on login, upload and
  questions only).
- The rate limit windows are fixed, so a client can send up to twice the limit around the moment
  a window ends.
- No frontend pages yet; the API is used through `/api/v1/docs` or curl.
