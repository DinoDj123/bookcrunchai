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
| `backend/app/parsing/` | `parse_book()` and the shared contract |
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

Automated tests never call paid APIs (Claude, Voyage); they use fakes. `BOOKCRUNCH_PARSER=fake`
selects the fake parser until the real one is merged.

Database tests (`tests/db`) run only when `TEST_DATABASE_URL` is set; they drop and recreate the
schema, so point it at a separate database, never the development one:

```bash
docker compose exec postgres createdb -U bookcrunch bookcrunch_test
export TEST_DATABASE_URL=postgresql+asyncpg://bookcrunch:bookcrunch@localhost:5432/bookcrunch_test
pytest
```

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

## Deployment

One Linux VM with Docker. On the host: `git pull`, set `SITE_ADDRESS` to the domain in `.env`
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
- **Migrations are checked in CI.** `tests/db` applies all migrations to an empty database, runs
  `alembic check` (fails if a model changed without a migration) and checks the constraints.

## Known limitations

Written as features land. No authentication or upload yet.
