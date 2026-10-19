# VPS deployment

Two supported layouts on a plain VPS (Ubuntu 22.04+/Debian 12+): **Docker Compose with Caddy** (default, TLS handled automatically) or **bare metal under systemd** (venv + units, Caddy/nginx on the host). Both share the same `.env` and are deployed with `deploy/vps-deploy.sh`.

## Prerequisites

1. A VPS with a public IP and DNS `A`/`AAAA` record pointing at it (for example `relay.example.com`).
2. External **PostgreSQL** (Aiven or the VPS-local Postgres template) and **Redis** (Upstash or local Valkey/Redis). Copy the TLS URIs into `DATABASE_URL` and `REDIS_URL` (Upstash: use the Redis URI, not the REST URL; keep any required `sslmode` params).
3. Telegram API credentials from `my.telegram.org`.
4. `.env` populated from `.env.example` with strong independent `APP_SECRET_KEY`, `JWT_SECRET_KEY`, and a Fernet `TELEGRAM_SESSION_ENCRYPTION_KEY`. Set `CORS_ORIGINS` to the Vercel website origin, `ALLOWED_HOSTS` to the API hostname, `SITE_ADDRESS`/`CADDY_EMAIL` for TLS.

## Option A — Docker Compose + Caddy (recommended)

```bash
sudo apt install -y docker.io docker-compose-plugin
git clone <repo> /opt/telegram_relay && cd /opt/telegram_relay
cp .env.example .env && nano .env
./deploy/vps-deploy.sh compose
```

`docker-compose.prod.yml` runs three containers:

- **api** — gunicorn/uvicorn on internal port 8000, read-only root filesystem, healthchecked, not published to the host.
- **worker** — `runtelegramworker`, same hardening, starts after the API is healthy.
- **caddy** — publishes 80/443, terminates TLS with automatic Let's Encrypt certificates (set `SITE_ADDRESS` and `CADDY_EMAIL`), applies security headers, and reverse-proxies to the API. Caddy forwards `X-Forwarded-Proto`, which Django trusts via `SECURE_PROXY_SSL_HEADER`.

Migrations run as an explicit release step inside the deploy script (`docker compose -f docker-compose.prod.yml run --rm --no-deps api python manage.py migrate --noinput`) — never at container startup.

To run without Caddy (for example behind an existing nginx or Cloudflare Tunnel), delete the `caddy` service, replace `expose` with `ports: ["8000:8000"]` on `api`, and terminate TLS upstream.

## Option B — bare metal + systemd

```bash
sudo useradd --system --home /opt/telegram_relay --shell /usr/sbin/nologin relay
sudo apt install -y python3.11-venv postgresql-client caddy
git clone <repo> /opt/telegram_relay && cd /opt/telegram_relay
sudo chown -R relay:relay /opt/telegram_relay
sudo -u relay python3 -m venv .venv
cp .env.example .env && nano .env
./deploy/vps-deploy.sh systemd
```

Installs two hardened units from `deploy/systemd/`:

- `telegram-relay-api.service` — gunicorn on `127.0.0.1:8000` (put Caddy/nginx in front; a minimal site config lives in `deploy/Caddyfile`).
- `telegram-relay-worker.service` — the Telethon worker; `Restart=always` with SIGTERM graceful shutdown.

Both units run as the unprivileged `relay` user with `ProtectSystem=strict`, `NoNewPrivileges`, and no writable paths. Logs go to the journal: `journalctl -u telegram-relay-api -u telegram-relay-worker -f`.

## Operations

- **Health**: `GET /health/live/` (process) and `GET /health/ready/` (Postgres + Redis). Only the API serves HTTP; the worker is monitored via `systemctl`/Compose restart policy and its JSON logs (`worker_started`, `account_connected`, `account_task_failed`).
- **Backups**: `./deploy/backup.sh /var/backups/telegram_relay 14` — pg_dump through a disposable postgres container (Compose) or local `pg_dump`; schedule it in cron. Encrypted sessions live in PostgreSQL, so backups are credential material: restrict file permissions and encrypt at rest.
- **Scaling**: keep exactly one worker replica unless you understand the renewable Redis account locks; the API can scale freely behind the proxy.
- **Secrets**: never commit `.env`; rotate `TELEGRAM_SESSION_ENCRYPTION_KEY` by moving the old key to `TELEGRAM_SESSION_ENCRYPTION_KEYS` before switching.
- **Upgrades**: `git pull && ./deploy/vps-deploy.sh compose` (or `systemd`) — migrations apply before services restart.
