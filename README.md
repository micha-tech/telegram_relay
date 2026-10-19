# Telegram MTProto Relay

A Django REST API plus an independent Telethon worker that republishes transformed messages using an authenticated Telegram **user account**. PostgreSQL is durable state; Redis is transient coordination. Telegram session strings are encrypted at rest and never returned by the API.

## Quick start

1. Copy `.env.example` to `.env` and fill every secret.
2. Generate the session key with `python -c "from cryptography.fernet import Fernet; print(Fernet.generate_key().decode())"`.
3. Run `docker compose build`, then `docker compose run --rm api python manage.py migrate`.
4. Start the API and worker with `docker compose up`.
5. Open `http://localhost:8000/health/`.

API endpoints are versioned under `/api/v1/` (legacy `/api/` alias kept): application auth, Telegram auth (phone → code → 2FA), account management (list, health, events, reconnect, disconnect), dialogs, relays, and processed messages. See [architecture](docs/architecture.md), [website integration](docs/frontend-integration.md), [JustRunMy.App deployment](docs/justrunmyapp.md), [VPS deployment](docs/vps-docker.md), and [local development](docs/local-development.md).
