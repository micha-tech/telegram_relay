#!/usr/bin/env bash
# PostgreSQL backup. Compose mode runs pg_dump inside a disposable
# postgres:16-alpine container; bare-metal mode uses the local pg_dump.
#
# Usage:
#   ./deploy/backup.sh [target-dir] [retention-days]
#
# Cron example (03:15 nightly, keep 14 days):
#   15 3 * * * cd /opt/telegram_relay && ./deploy/backup.sh /var/backups/telegram_relay 14
set -euo pipefail
cd "$(dirname "$0")/.."

TARGET_DIR="${1:-./backups}"
RETENTION_DAYS="${2:-14}"
STAMP="$(date +%Y%m%d-%H%M%S)"
mkdir -p "$TARGET_DIR"

# shellcheck disable=SC1091
set -a; source .env; set +a
[[ -n "${DATABASE_URL:-}" ]] || { echo "ERROR: DATABASE_URL is not set" >&2; exit 1; }

if command -v docker >/dev/null 2>&1 && docker compose -f docker-compose.prod.yml ps --status running api 2>/dev/null | grep -q api; then
  docker run --rm -v "$(realpath "$TARGET_DIR"):/backups" -e DATABASE_URL \
    postgres:16-alpine sh -c "pg_dump --no-owner \"\$DATABASE_URL\" | gzip > /backups/relay-${STAMP}.sql.gz"
else
  pg_dump --no-owner "$DATABASE_URL" | gzip > "${TARGET_DIR}/relay-${STAMP}.sql.gz"
fi

echo "Wrote ${TARGET_DIR}/relay-${STAMP}.sql.gz ($(du -h "${TARGET_DIR}/relay-${STAMP}.sql.gz" | cut -f1))"
find "$TARGET_DIR" -name 'relay-*.sql.gz' -mtime "+${RETENTION_DAYS}" -delete
