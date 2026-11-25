# Deploying bookcrunch.ai

One Linux virtual machine with Docker Compose (specification 5.5). The same `docker-compose.yml`
runs locally and on the host; only `.env` differs.

> **Status (25.11.2026): not deployed yet.** The VM and the domain are not available, so nothing
> in this document has been run on a real host. `scripts/backup_db.sh` was run against the local
> stack; `scripts/deploy.sh` was only syntax-checked. Values in `<ANGLE_BRACKETS>` are not known
> yet and must be filled in when the VM and the domain exist.

## What the host needs

- A Linux VM reachable over SSH.
- Docker Engine with the Compose plugin (`docker compose version` works), and git.
- Ports 80 and 443 open to the internet. Caddy needs both: 80 for the certificate check and the
  redirect to HTTPS, 443 for the site. PostgreSQL (5432) and Redis (6379) are bound to
  `127.0.0.1` in `docker-compose.yml` and must stay closed from outside.
- A DNS A record for `<DOMAIN>` that points to the VM's public address. It must resolve before
  the first start, or Caddy cannot get a certificate.

Not decided yet: the VM provider and its size, the domain, and who has SSH access.

## First deployment

On the host:

```bash
git clone https://github.com/DinoDj123/bookcrunchai.git <REPO_DIR>
cd <REPO_DIR>
cp .env.example .env
```

Edit `.env`:

| Variable | Value on the host |
|---|---|
| `ENVIRONMENT` | `production` |
| `SITE_ADDRESS` | `<DOMAIN>`, without `https://`. Caddy then gets an HTTPS certificate by itself and renews it. |
| `JWT_SECRET` | A new random string: `python3 -c "import secrets; print(secrets.token_urlsafe(48))"`. The API, the worker and the migrations refuse to start with the example value once `SITE_ADDRESS` is a domain. |
| `POSTGRES_PASSWORD` | A new random password. Set it before the first start: PostgreSQL stores it when the database volume is created, and changing `.env` later does not change it. |
| `BOOKCRUNCH_PARSER` | `real` |

`.env` holds secrets. It is git-ignored; never commit it, and keep it readable only by the
deploying user (`chmod 600 .env`).

Then:

```bash
./scripts/deploy.sh
```

## Every later deployment

```bash
cd <REPO_DIR>
./scripts/deploy.sh
```

What the script does, in this order:

1. `git pull --ff-only`: the code of the branch that is checked out (normally `main`).
2. `docker compose build`: new images. The running containers are not touched yet.
3. `docker compose run --rm api alembic upgrade head`: database migrations, before the new code
   starts.
4. `docker compose up -d --remove-orphans`: replaces the containers whose image or settings
   changed.
5. Waits up to 60 seconds for `GET /api/v1/health` inside the `api` container and prints
   `docker compose ps`.

It stops at the first step that fails. A failed build or migration leaves the old containers
running.

Check from outside afterwards:

```bash
curl -i https://<DOMAIN>/api/v1/health      # 200 {"status":"ok"}
```

Then open `https://<DOMAIN>` in a browser, register and upload a small EPUB.

## If something goes wrong

```bash
docker compose ps                 # which containers run, which are unhealthy
docker compose logs --tail 100 api
docker compose logs --tail 100 worker
docker compose logs --tail 100 proxy     # certificate problems show here
```

- **No HTTPS certificate:** the DNS record does not point to the VM yet, or port 80 or 443 is
  closed. Caddy keeps trying; fix the cause and watch `docker compose logs -f proxy`.
- **The API or the worker restarts in a loop with a `JWT_SECRET` error:** `.env` still has the
  example secret.
- **Going back to the previous version:** `git checkout <COMMIT>` and then
  `docker compose build && docker compose up -d`. This does not undo database migrations. A
  migration is undone with `docker compose run --rm api alembic downgrade -1`, and only after
  checking what that migration's downgrade deletes.

## Database backups

Specification 4.5: a daily database dump on the demo host. `scripts/backup_db.sh` runs `pg_dump`
inside the `postgres` container, writes `bookcrunch_<UTC date>_<time>.dump` and keeps the newest
7 dumps. A dump that fails half-way is deleted and does not replace a good one.

Run it once by hand:

```bash
BACKUP_DIR=<BACKUP_DIR> ./scripts/backup_db.sh
```

Then add it to the crontab of the deploying user (`crontab -e`), every day at 03:00:

```cron
0 3 * * * cd <REPO_DIR> && BACKUP_DIR=<BACKUP_DIR> ./scripts/backup_db.sh >> <BACKUP_DIR>/backup.log 2>&1
```

The user must be allowed to run `docker` (member of the `docker` group). Without `BACKUP_DIR`
the dumps go to `<REPO_DIR>/backups`, which is git-ignored.

What is not backed up:

- **Uploaded book files.** They are on the `book_files` Docker volume, which survives restarts
  and deployments but not the loss of the VM. The dump holds the books' rows, not the files.
- **Anything off the host.** The dumps are on the same VM as the database. They protect against
  a bad migration or a deleted row, not against losing the VM.

### Restoring a dump

This replaces everything in the database with the content of the dump.

```bash
docker compose stop api worker
docker compose exec -T postgres sh -c \
  'pg_restore -U "$POSTGRES_USER" -d "$POSTGRES_DB" --clean --if-exists' < <BACKUP_DIR>/<FILE>.dump
docker compose start api worker
```

A restore has not been tried on a real host yet; do one trial restore after the first deployment.
