#!/usr/bin/env bash
# Dump the database to a file and keep the newest 7 dumps (specification 4.5). Run daily by cron
# on the demo host (docs/deploy.md):
#
#   ./scripts/backup_db.sh                 # writes to ./backups
#   BACKUP_DIR=/some/dir ./scripts/backup_db.sh
#
# Uploaded book files are not in the dump; they live on the book_files Docker volume.
# Restore: docs/deploy.md, "Restoring a dump".
set -euo pipefail

cd "$(dirname "$0")/.."

KEEP=7
BACKUP_DIR="${BACKUP_DIR:-$(pwd)/backups}"
mkdir -p "$BACKUP_DIR"

name="bookcrunch_$(date -u +%Y%m%d_%H%M%S).dump"
# Written under a temporary name first: a dump that failed half-way must never look like a good
# one, and must not push a good one out of the newest 7.
partial="$BACKUP_DIR/$name.partial"
trap 'rm -f "$partial"' EXIT

# pg_dump runs inside the postgres container with the container's own user and database name, so
# this script does not need to read .env. -Fc: compressed, restored with pg_restore.
docker compose exec -T postgres sh -c 'pg_dump -U "$POSTGRES_USER" -Fc "$POSTGRES_DB"' > "$partial"

if [ ! -s "$partial" ]; then
  echo "The dump is empty; keeping the old dumps." >&2
  exit 1
fi
mv "$partial" "$BACKUP_DIR/$name"

# Names sort by time, so everything after the newest $KEEP is old.
ls -1 "$BACKUP_DIR"/bookcrunch_*.dump | sort -r | tail -n +$((KEEP + 1)) | while read -r old; do
  rm -f "$old"
done

echo "Saved $BACKUP_DIR/$name ($(du -h "$BACKUP_DIR/$name" | cut -f1))"
