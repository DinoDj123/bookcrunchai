# Authentication, upload and job status (task B4) — design

Approved by Daris Velović on 21.11.2026 (option 1: authentication is part of this task). Track B.

## Intent

Week 1 item "upload and status" (specification 11.2). A logged-in user uploads a book and can
follow its processing job. Both endpoints need a session (FR-002 depends on FR-001), so
registration and login are built first.

## Goals

- `POST /auth/register`, `POST /auth/login`, `POST /auth/logout`, `GET /auth/me` (FR-001, 7.3).
- `POST /books` (FR-002, FR-014): validate, de-duplicate, store, create book and job, enqueue.
- `GET /jobs/{id}` (FR-012).

## Non-goals

- Frontend pages; the other book endpoints (list, details, delete, retry); rate limits (4.2) —
  each is a later task.
- Any change to the shared contract files or to Track A code.

## Anti-goals

- A book row without a queued job, or a queued job the worker never hears about.
- One user seeing that another user's job or book exists (must be 404).
- A password or token in a log line or a response body.

## Approaches

1. **Chosen: authentication in this task**, as its own commit before upload. Upload works as
   specified from the first day and nothing is thrown away.
2. Authentication as a separate task first: smaller pull requests, upload one step later.
3. A stand-in `get_current_user` returning a fixed user: fastest, but the endpoint is unprotected
   until replaced and must not reach the demo host.

## Design

- `app/core/security.py`: Argon2id hashing (argon2-cffi), JWT HS256 with `sub` = user ID and a
  24-hour expiry (PyJWT).
- `app/api/deps.py`: `get_current_user` (the one dependency of 7.3: cookie → verified token →
  user; otherwise 401 `UNAUTHENTICATED`), `get_queue` (the ARQ pool created at API start-up).
- Cookie `session`: httpOnly, SameSite=Lax, 24 hours, `Secure` except when `ENVIRONMENT=local`
  (local development runs on plain HTTP).
- Register: email lower-cased, password 8–128 characters; 201 `{id, email}` and the cookie;
  409 `EMAIL_TAKEN`. Login: 200 `{id, email}`; 401 `INVALID_CREDENTIALS` with the same message
  for an unknown email and a wrong password, and the same work done in both cases.
- Upload: the file is copied to the storage directory in pieces while its size and SHA-256 are
  computed. Checks, in order: extension (`.epub`, `.pdf`) → 415; size over 50 MB → 413;
  signature (`PK\x03\x04` for EPUB, `%PDF` for PDF) matching the extension → 415. Same owner and
  hash → 200 with the existing book and its latest job. Otherwise the file is kept as
  `<book id>.<extension>` (`books.file_path`, relative to the storage directory), book
  `uploaded` and job `queued` are committed, then the job is enqueued → 202.
- If enqueuing fails, the book, the job and the file are removed and the API answers 503
  `QUEUE_UNAVAILABLE`, so the user can simply upload again.
- Job status: the job joined to its book; 404 `JOB_NOT_FOUND` unless the user owns the book or
  it is a demo book.
- Settings refuse to start with the default or a short `JWT_SECRET` unless the application is local (`ENVIRONMENT=local` and no domain in `SITE_ADDRESS`).
- After review: the handler reads the form itself, after the session and the declared size are checked, because FastAPI would otherwise receive the whole body first. A repeated upload whose job is still `queued` enqueues that job again.

## Left to the builder

- Pydantic schema names; how tests replace the database session, settings and queue.
