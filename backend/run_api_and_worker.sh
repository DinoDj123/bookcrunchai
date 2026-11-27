#!/usr/bin/env bash
# The API and the worker in one container, for Railway (docs/deploy.md). A Railway volume can be
# attached to one service only, and both processes need the uploaded book files, so they run
# together. Locally docker-compose.yml runs them as two services instead.
#
# If either process stops, the other is stopped too and the container exits, so Railway restarts
# the whole service instead of leaving it half running.
set -euo pipefail

arq app.worker.settings.WorkerSettings &
worker=$!

uvicorn app.main:app --host 0.0.0.0 --port "${PORT:-8000}" \
    --proxy-headers --forwarded-allow-ips="*" &
api=$!

stop() {
    kill -TERM "$worker" "$api" 2>/dev/null || true
}
trap stop TERM INT

# Wait for the first process to exit, then stop the other one and wait for it as well.
status=0
wait -n "$worker" "$api" || status=$?
stop
wait || true
exit "$status"
