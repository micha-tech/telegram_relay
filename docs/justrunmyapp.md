# JustRunMy.App

## Platform capabilities (verified against platform docs, October 2026)

- Runs one Docker container per app, 24/7, with auto-restart on crash. There is no sleep/idle policy.
- Deploy by Git push, Docker image push, or Zip upload. Startup command, environment variables, ports, and quotas are set per app in the panel or via the MCP server.
- Persistent volumes can be attached per app, but a writable container directory must not be assumed to survive redeploys.
- PostgreSQL, Valkey/Redis, and other databases are available as templates, but this deployment uses external Aiven PostgreSQL and Upstash Redis so the API and worker share one state store.
- No docker-compose and no multi-service units: each app is exactly one container with one foreground process.

## Deployment topology

JustRunMy.App expects a single foreground process per container, so the API and the Telethon worker ship as **two separate apps** built from the same image:

| App | Startup command | Port |
| --- | --- | --- |
| `telegram-relay-api` | Dockerfile default (gunicorn + uvicorn workers, `config.asgi:application`) | honors `$PORT` (default 8000); set the app's port to match, HTTPS provided |
| `telegram-relay-worker` | `python manage.py runtelegramworker` | none |

The image binds `0.0.0.0:${PORT:-8000}`, so if the panel injects a `PORT` such as `3000`, the container listens there automatically. Make the app's configured/expected port equal to that value (or set `PORT` yourself) — a mismatch is the usual cause of a `502 Bad Gateway` from the proxy.

Both apps share the same environment variables (`DATABASE_URL`, `REDIS_URL`, `TELEGRAM_*`, secrets). The worker never listens for HTTP; the API never holds long-lived Telegram connections. Account coordination uses renewable Redis locks, so scaling the worker to multiple replicas later is safe — at most one worker controls an account at a time.

Sessions are encrypted in PostgreSQL, never written to local disk, so container redeploys and volume loss cannot destroy account state.

## Deployment steps

1. Create the two apps in the JustRunMy.App panel (Git push or Docker image deploy from the same repository/image).
2. Attach the same environment variables to both apps from `.env.example`. Generate independent values for `APP_SECRET_KEY`, `JWT_SECRET_KEY`, and `TELEGRAM_SESSION_ENCRYPTION_KEY` (Fernet key). Set `CORS_ORIGINS` to the Vercel website origin and `ALLOWED_HOSTS` to the app's hostname.
3. Run migrations once per release before restarting processes. Easiest with the web shell or `jrnm_execute_command_in_app` on the API app: `python manage.py migrate --noinput`. Migrations are never replaced by automatic table creation at startup.
4. Start the API app and verify `GET /health/live/` and `GET /health/ready/`.
5. Start the worker app and check logs for `worker_started` and per-account `account_connected`.
6. Smoke-test from the website: register/login, start a Telegram connection, verify the code, list accounts.

## Graceful shutdown and recovery

- The API uses gunicorn's graceful timeout; in-flight requests finish on SIGTERM.
- The worker installs SIGINT/SIGTERM handlers, cancels account tasks, disconnects clients, and releases Redis locks before exiting.
- After a crash or redeploy, both processes rebuild state from PostgreSQL: the worker reloads every `connected` account with a stored session, and per-account exponential backoff (capped, configurable via `WORKER_MAX_BACKOFF_SECONDS`) prevents reconnect storms.
- Pending authentication challenges are expired in place by the worker every few minutes and by `python manage.py cleanuptelegramchallenges` as a scheduled job if desired.

## Platform limitations and alternatives

- **No multi-process units**: solved by two apps (above). Do not run the API and worker under one shell/supervisor in a single container — an unmanaged background process can be reaped silently.
- **No durable container disk guarantee**: sessions live encrypted in PostgreSQL; no local SQLite/session files are used anywhere.
- **No native cron**: run `cleanuptelegramchallenges` from the worker (already periodic) or schedule it from an external scheduler hitting the web shell.
- **Health checks**: map port 8000 on the API app only; use `/health/live/` for liveness and `/health/ready/` for dependency readiness.
