# Deploying bookcrunch.ai on Railway

The mentors proposed [Railway](https://railway.com) for the demo (specification 5.5, Appendix E).
Railway builds the services from the Dockerfiles in this repository and deploys `main` on every
push. Docker Compose (`docker-compose.yml`) is for local development only.

> **Status (27.11.2026): not deployed yet.** Nothing in this document has been run on Railway.
> Who pays for the Railway project is open (specification Appendix E, question #9). The service
> names below (`proxy`, `frontend`, `backend`, `Postgres`, `Redis`) are the ones this guide
> assumes; if a service gets another name, change the variables that refer to it.

## The services

| Service | Built from | Public | Notes |
|---|---|---|---|
| `proxy` | `proxy/Dockerfile` (Caddy) | **yes**, the only one | `/api/*` to `backend`, everything else to `frontend`. One origin for the browser, so the session cookie works as locally. |
| `frontend` | `frontend/Dockerfile` (Next.js) | no | |
| `backend` | `backend/Dockerfile` | no | **The API and the worker in one service** (`backend/run_api_and_worker.sh`), with the volume for the uploaded book files. |
| `Postgres` | Railway's pgvector template | no | PostgreSQL with the `vector` extension. |
| `Redis` | Railway's Redis | no | The job queue. |

**Why the API and the worker are one service:** a Railway volume can be attached to one service
only. The API stores the uploaded file and the worker reads it, so both must run where the volume
is. The script starts both processes and stops the container when either of them stops, so
Railway restarts the whole service. Rejected alternatives: the files in PostgreSQL or in object
storage (S3), which would change the data model and the upload; and a separate worker with its own
copy of the file, which is impossible without shared storage. Consequence: the API and the worker
are restarted and scaled together, which is fine for the demo.

**Why Caddy stays in front:** without it the frontend and the API would be on two Railway
domains, and the session cookie and the API calls would need cross-origin settings (CORS,
`SameSite=None`). With Caddy everything stays as it is locally.

**Why the dashboard and not a config file:** Railway's `railway.json` / `railway.toml` (Config as
Code) is deprecated, new services cannot use it, and it stops being read on 2027-01-01. Its
replacement (`.railway/railway.ts`, applied with the Railway CLI) is newer than this project
needs. The settings below are few, so they are set by hand once and written down here.

## Setting up the project (once)

1. Create a project on Railway and connect the GitHub repository `DinoDj123/bookcrunchai`
   (Railway's GitHub app needs access to it).
2. **Postgres:** add the **pgvector** template ("Deploy pgvector" in the template list), not the
   plain PostgreSQL database: the first migration runs `CREATE EXTENSION vector`. Name the service
   `Postgres`.
3. **Redis:** add a Redis database. Name it `Redis`.
4. **backend:** a new service from the GitHub repository.
   - Settings → Source: **root directory `/backend`**, branch `main`.
   - Settings → Deploy: **custom start command** `bash run_api_and_worker.sh`, **pre-deploy
     command** `alembic upgrade head`, **healthcheck path** `/api/v1/health`.
   - A **volume** mounted at **`/data/storage`**.
   - Variables: see the table below.
5. **frontend:** a new service from the repository, **root directory `/frontend`**. Variable
   `PORT=3000`.
6. **proxy:** a new service from the repository, **root directory `/proxy`**. Variables below.
   Settings → Networking: **Generate domain** with target port **8080**. That address
   (`https://<name>.up.railway.app`) is the application.
7. Optional: in each of the three services from the repository set **watch paths**
   (`/backend/**`, `/frontend/**`, `/proxy/**`), so a push rebuilds only the services whose files
   changed.

### Variables

`${{Service.VARIABLE}}` is Railway's reference to another service's variable; Railway fills in the
value. Secrets are set only in Railway, never in the repository.

**backend**

| Variable | Value |
|---|---|
| `ENVIRONMENT` | `production`: the session cookie becomes Secure and a real `JWT_SECRET` is required. |
| `JWT_SECRET` | A new random string: `python -c "import secrets; print(secrets.token_urlsafe(48))"` |
| `DATABASE_URL` | `${{Postgres.DATABASE_URL}}`. Railway gives `postgresql://...`; the backend switches it to the asyncpg driver itself. |
| `REDIS_URL` | `${{Redis.REDIS_URL}}` |
| `STORAGE_DIR` | `/data/storage` (the volume) |
| `PORT` | `8000`. The proxy expects the API on this port. |
| `RAILWAY_RUN_UID` | `0`. Railway mounts volumes as root and the image runs as a non-root user, which could not write to the volume otherwise. |
| `BOOKCRUNCH_PARSER` | `real` |
| `LLM_BASE_URL`, `LLM_API_KEY`, `LLM_MODEL_FAST`, `LLM_MODEL_STRONG` | The provider of the open-weight models (`.env.example`, specification 5.5). Railway has no GPU, so this is a hosted provider, not Ollama. Not decided yet (Appendix E, question #8). |

**proxy**

| Variable | Value |
|---|---|
| `SITE_ADDRESS` | `:8080`. Railway's edge handles HTTPS and sends plain HTTP to this port. |
| `PORT` | `8080` |
| `API_UPSTREAM` | `backend.railway.internal:8000` |
| `FRONTEND_UPSTREAM` | `frontend.railway.internal:3000` |
| `TRUSTED_PROXIES` | `0.0.0.0/0 ::/0` |
| `CLIENT_IP_HEADER` | `X-Real-IP` |

The last two give the API the client's real address for the rate limits. Every request reaches
the proxy through Railway's edge, which replaces `X-Real-IP` with the client's address; without
them all users would share the edge's address and one limit.

**frontend:** `PORT=3000`.

## Deploying

A merge to `main` deploys by itself. For the `backend` service Railway builds the image, runs
`alembic upgrade head` (pre-deploy), and only if that succeeds starts the new version and waits
for `/api/v1/health`. A failed build, migration or health check leaves the previous version
running.

Check afterwards:

```bash
curl -i https://<name>.up.railway.app/api/v1/health      # 200 {"status":"ok"}
```

Then open the address in a browser, register and upload a small EPUB.

## If something goes wrong

Each service has its logs in the dashboard (Deployments → View logs). The `backend` logs show the
API and the worker together.

- **`backend` restarts in a loop with a `JWT_SECRET` error:** the variable is missing or too short.
- **Upload fails with "Permission denied" under `/data/storage`:** `RAILWAY_RUN_UID=0` is missing.
- **Pre-deploy fails with `extension "vector" is not available`:** the database is the plain
  PostgreSQL template, not pgvector.
- **The proxy answers 502:** `API_UPSTREAM` / `FRONTEND_UPSTREAM` do not match the service names,
  or `PORT` is missing on `backend` or `frontend`. The private network works only between services
  of the same project and environment, and only at runtime, not during a build.
- **Going back:** Deployments → an earlier deployment → Redeploy. This does not undo database
  migrations; a migration is undone from a shell in the service (`railway ssh`, then
  `alembic downgrade -1`), and only after checking what that migration's downgrade deletes.

## Backups

Specification 4.5 asks for a daily database backup. On Railway, this is the **Backups** tab of a
service with a volume: add a **daily** schedule to the `Postgres` service (Railway keeps daily
backups for 6 days) and to `backend` (the uploaded book files, which the earlier `pg_dump`
script did not cover). A restore is staged as a new volume and applied with Deploy; backups can be
restored only within the same project and environment. Check that the plan the project is on
includes backups, and do one trial restore after the first deployment.

## Before the presentation (14.12.2026)

- The demo books are processed in advance (specification 5.5), so the presentation does not
  depend on the provider's rate limits.
- Feature freeze on 11.12.2026: after it, only fixes are merged to `main`, because every merge
  deploys.
