# Website integration (Vercel)

The existing website on Vercel is the only user-facing application. This backend is reached over HTTPS at a single base URL and never serves HTML.

## API base URL

The backend exposes versioned JSON under `/api/v1` (the unversioned `/api` prefix is kept as an alias for existing callers). Configure one environment variable in the Vercel project:

```
NEXT_PUBLIC_TELEGRAM_API_BASE_URL=https://<your-api-host>/api/v1
```

Read it through a single API-client module (for example `lib/telegramApi.ts`) so no route hardcodes an environment-specific URL.

## Authentication

The website remains the source of user identity. Two supported integration modes — pick one and configure `JWT_SECRET_KEY` on the backend to match:

1. **Shared JWT signing key (recommended if the website already issues JWTs).** The website's access tokens are signed with the same secret configured as `JWT_SECRET_KEY`; the backend verifies them with `Authorization: Bearer <token>` and maps the verified identity to its user table. No token minting on the frontend.
2. **Backend-to-backend exchange.** The website's own backend exchanges a verified session for short-lived backend tokens via `POST /api/v1/auth/login` or `register`; the browser only ever holds short-lived access tokens (15 minutes) plus a refresh token.

Never pass user IDs in request bodies or query strings; every endpoint derives the owner from the verified token. The backend independently enforces that the authenticated user owns every account, challenge, and relay it touches (cross-user access returns 404).

## Connection flow endpoints

| Step | Endpoint | Notes |
| --- | --- | --- |
| Start | `POST /telegram/auth/start` `{phone_number}` | E.164; returns `challenge_id`, `status`, `expires_at` (202) |
| Code | `POST /telegram/auth/verify` `{challenge_id, code}` | `status` becomes `complete` or `password_required` |
| 2FA | `POST /telegram/auth/2fa` `{challenge_id, password}` | only when `password_required` |
| Status | `GET` accounts list / detail | safe fields only |

Errors are sanitized (`400` with a human-readable message, e.g. flood-wait seconds). Codes, passwords, and session strings never appear in responses or logs.

## Account management endpoints

- `GET /telegram/accounts` — paginated list: `id`, `phone_masked`, `telegram_user_id`, `display_name`, `username`, `status`, `last_connected_at`, `last_activity_at`, `last_error`.
- `GET /telegram/accounts/{id}` — single account.
- `GET /telegram/accounts/{id}/health` — `status`, `session_present`, `worker_active`, timestamps.
- `GET /telegram/accounts/{id}/events` — recent connection events (`connected`, `code_sent`, `password_required`, `error`, `revoked`, `disconnected`).
- `POST /telegram/accounts/{id}/reconnect` — re-attach the worker to a stored session.
- `POST /telegram/accounts/{id}/disconnect` — revoke the Telegram auth key (best effort), delete the stored session, mark `disconnected`.
- `GET /telegram/accounts/{id}/dialogs` — sync dialogs into saved entities for relay configuration.

## UI requirements

Use the website's existing design system. A Telegram Accounts section needs: Connect button → phone → code → optional 2FA password → success; account list with status badges, masked phone, last activity, reconnect and disconnect actions; loading, empty, and error states; recovery from expired challenges (offer "start again" once `expires_at` passes). Never render session strings or raw phone numbers.
