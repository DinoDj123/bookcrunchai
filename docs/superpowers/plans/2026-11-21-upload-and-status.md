# Authentication, Upload and Job Status (B4) Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** A registered user can upload a book, which queues a processing job, and read that job's status.

**Architecture:** FastAPI routers under `app/api/`, one `get_current_user` dependency reading a JWT
cookie, upload streamed to the storage directory, job enqueued through `app.worker.queue`.

**Tech Stack:** FastAPI, Pydantic 2, SQLAlchemy 2 async, argon2-cffi, PyJWT, python-multipart, ARQ.

**Spec:** `docs/superpowers/specs/2026-11-21-upload-and-status-design.md`; `docs/specification.md`
FR-001, FR-002, FR-012, FR-014, sections 4.2, 7.1–7.3.

## Global Constraints

- Track B files only; never edit `app/parsing/*`.
- Error format of 7.1 through `AppError`; codes: `UNAUTHENTICATED` 401, `INVALID_CREDENTIALS` 401,
  `EMAIL_TAKEN` 409, `FILE_TOO_LARGE` 413, `UNSUPPORTED_FILE_TYPE` 415, `JOB_NOT_FOUND` 404,
  `QUEUE_UNAVAILABLE` 503, `VALIDATION_ERROR` 422.
- Cookie name `session`; JWT HS256, 24 hours; password 8–128 characters; upload limit 50 MB.
- Tests use the test database, a temporary storage directory and a fake queue: no Redis.
- `ruff check .`, `ruff format --check .` and `pytest` pass (run with `.superpowers/t.sh`).

## Review Focus

1. Two uploads of the same file by one user at the same moment: one book, no 500.
2. A file named `book.pdf` whose content is an EPUB (or the reverse): 415.
3. A token signed with another secret, expired, or for a deleted user: 401, never 500.
4. Email with capitals or spaces at registration and login: one account.
5. An empty file, and a file name with path separators or no extension: clean 415, nothing stored.

---

### Task 1: Authentication

**Files:** create `app/core/security.py`, `app/api/deps.py`, `app/api/auth.py`,
`tests/api/__init__.py`, `tests/api/conftest.py`, `tests/api/test_auth.py`,
`tests/test_security.py`, `tests/test_config.py`; modify `app/api/router.py`,
`app/core/config.py`, `pyproject.toml`.

**Interfaces — produces:**
- `hash_password(password: str) -> str`, `verify_password(password: str, password_hash: str) -> bool`,
  `create_session_token(user_id: UUID, secret: str) -> str`,
  `read_session_token(token: str, secret: str) -> UUID | None` in `app/core/security.py`.
- `get_current_user` → `User`; `SESSION_COOKIE = "session"` in `app/api/deps.py`.
- Test fixtures (`tests/api/conftest.py`): `client` (httpx `AsyncClient` on the app with the
  database session, settings (`storage_dir=tmp_path`, `max_upload_bytes=1024`) and queue
  overridden), `queue` (fake recording enqueued jobs, with a `fail` switch), `registered`
  (registers `reader@example.com` / `correct horse` and leaves the client logged in).

- [ ] Tests first: register (201, cookie httpOnly + SameSite=Lax, stored lower-case, hash is
  Argon2id and not the password), duplicate email in another case → 409, short password and bad
  email → 422, login ok / wrong password / unknown email (same 401 body), `me` with and without
  cookie, with a token signed by another secret, an expired token, a deleted user → 401, logout
  clears the cookie, `Secure` set when environment is not local; settings reject a default or
  short secret in production.
- [ ] Run: FAIL. Implement. Run: PASS. Commit `Add registration, login and the session cookie`.

### Task 2: Upload

**Files:** create `app/api/books.py`, `tests/api/test_upload.py`; modify `app/api/router.py`,
`app/api/deps.py` (`get_queue`), `app/main.py` (lifespan creating and closing the ARQ pool),
`app/core/config.py` (`max_upload_bytes`).

- [ ] Tests first: EPUB and PDF upload → 202 `{book: {id, title, status: "uploaded"}, job: {id,
  status: "queued"}}`, file stored as `<book id>.<ext>` with the same bytes, row values (owner,
  hash, size, format, title from the file name), job enqueued once; same file again → 200, same
  book, no second file, job or enqueue; same file by another user → 202 and a second book; too
  large → 413 and nothing stored; wrong extension, wrong signature, extension/signature
  mismatch, empty file, no extension → 415 and nothing stored; no cookie → 401; no file → 422;
  queue failure → 503 and no book, job or file left; two simultaneous uploads of one file → one
  book.
- [ ] Run: FAIL. Implement. Run: PASS. Commit `Add book upload with de-duplication and queuing`.

### Task 3: Job status and documentation

**Files:** create `app/api/jobs.py`, `tests/api/test_jobs.py`; modify `app/api/router.py`,
`docs/specification.md` (v1.4), `README.md`, `.env.example`.

- [ ] Tests first: owner reads `{id, book_id, type, status, stage, progress, error_code,
  error_message, started_at, finished_at}`; another user's job → 404 `JOB_NOT_FOUND`; unknown ID
  → 404; malformed ID → 422; no cookie → 401; job of a demo book readable by anyone logged in;
  after running `process_book` on an uploaded book the endpoint reports `done`.
- [ ] Run: FAIL. Implement. Run: PASS.
- [ ] Hand check on the Compose stack with curl: register → upload → poll job → `done`.
- [ ] Update specification, README, `.env.example`. Commit.
