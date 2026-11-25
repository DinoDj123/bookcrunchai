#!/usr/bin/env bash
# Deploy the current branch on the demo host (docs/deploy.md). Run on the host, from anywhere:
#
#   ./scripts/deploy.sh
#
# Steps: git pull, build the images, apply database migrations, start the new containers, wait
# until the API answers. It stops at the first step that fails; the containers that were running
# keep running until the last step, so a failed build or migration does not take the site down.
set -euo pipefail

cd "$(dirname "$0")/.."

if [ ! -f .env ]; then
  echo "No .env in $(pwd). Copy .env.example to .env and fill it in (docs/deploy.md)." >&2
  exit 1
fi

echo "==> git pull"
# --ff-only: the host never has commits of its own; if it does, stop instead of merging.
git pull --ff-only

echo "==> docker compose build"
docker compose build

echo "==> alembic upgrade head"
# Before the new code starts, so it never runs against an old schema. Starts postgres if needed.
docker compose run --rm api alembic upgrade head

echo "==> docker compose up -d"
docker compose up -d --remove-orphans

echo "==> waiting for the API"
for _ in $(seq 1 30); do
  if docker compose exec -T api python -c \
    "import urllib.request; urllib.request.urlopen('http://localhost:8000/api/v1/health')" \
    2>/dev/null; then
    echo "Deployed: $(git log -1 --format='%h %s')"
    docker compose ps
    exit 0
  fi
  sleep 2
done

echo "The API did not answer within 60 seconds. Look at: docker compose logs api" >&2
docker compose ps
exit 1
