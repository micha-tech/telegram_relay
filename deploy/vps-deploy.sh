#!/usr/bin/env bash
# Deploy telegram_relay on a VPS.
#
# Usage:
#   ./deploy/vps-deploy.sh compose    # Docker Compose (API + worker + Caddy)
#   ./deploy/vps-deploy.sh systemd   # bare-metal venv under systemd
#
# Run from the release directory (typically /opt/telegram_relay). Requires .env
# to be fully populated. Idempotent: safe to re-run for every release.
set -euo pipefail
cd "$(dirname "$0")/.."

MODE="${1:-compose}"

require_env_file() {
  [[ -f .env ]] || { echo "ERROR: .env is missing" >&2; exit 1; }
  for key in APP_SECRET_KEY JWT_SECRET_KEY DATABASE_URL REDIS_URL TELEGRAM_API_ID TELEGRAM_API_HASH TELEGRAM_SESSION_ENCRYPTION_KEY ALLOWED_HOSTS CORS_ORIGINS; do
    grep -q "^${key}=..*" .env || { echo "ERROR: ${key} is not set in .env" >&2; exit 1; }
  done
}

migrate() {
  echo "==> Applying database migrations"
  if [[ "$MODE" == "compose" ]]; then
    docker compose -f docker-compose.prod.yml run --rm --no-deps api python manage.py migrate --noinput
  else
    /opt/telegram_relay/.venv/bin/python manage.py migrate --noinput
  fi
}

deploy_compose() {
  echo "==> Building image"
  docker compose -f docker-compose.prod.yml build
  migrate
  echo "==> Starting api, worker, caddy"
  docker compose -f docker-compose.prod.yml up -d
  echo "==> Waiting for API health"
  for _ in $(seq 1 30); do
    if curl -fsS http://127.0.0.1:8000/health/live/ >/dev/null 2>&1 \
      || curl -fsSk https://127.0.0.1/health/live/ >/dev/null 2>&1; then
      echo "API is healthy"
      break
    fi
    sleep 2
  done
  docker compose -f docker-compose.prod.yml ps
}

deploy_systemd() {
  echo "==> Installing Python dependencies"
  /opt/telegram_relay/.venv/bin/pip install --upgrade -e .
  migrate
  echo "==> Installing systemd units"
  sudo cp deploy/systemd/telegram-relay-api.service /etc/systemd/system/
  sudo cp deploy/systemd/telegram-relay-worker.service /etc/systemd/system/
  sudo systemctl daemon-reload
  sudo systemctl enable --now telegram-relay-api telegram-relay-worker
  sudo systemctl restart telegram-relay-api telegram-relay-worker
  echo "==> Status"
  systemctl --no-pager status telegram-relay-api telegram-relay-worker | head -30
}

require_env_file
case "$MODE" in
  compose) deploy_compose ;;
  systemd) deploy_systemd ;;
  *) echo "Usage: $0 [compose|systemd]" >&2; exit 1 ;;
esac
echo "==> Deploy complete"
