# Website integration (Vercel)

The existing website on Vercel is the only user-facing application. This backend is reached over HTTPS at a single base URL and never serves HTML.

## API base URL

The backend exposes versioned JSON under `/api/v1` (the unversioned `/api` prefix is kept as an alias for existing callers). Configure one environment variable in the Vercel project:

```
NEXT_PUBLIC_TELEGRAM_API_BASE_URL=https://<your-api-host>/api/v1
```

Read it through a single API-client module (for example `lib/telegramApi.ts`) so no route hardcodes an environment-specific URL.

## Authentication

The backend is the source of user identity. The browser signs up or signs in via
`POST /api/v1/auth/register` or `/auth/login` and receives a short-lived access
token (15 minutes) plus a refresh token inside the success envelope. Send the
access token as `Authorization: Bearer <token>`; `POST /api/v1/auth/refresh` is
raw SimpleJWT (`{ "refresh": "…" }` → `{ "access": "…" }`, not enveloped), and
`POST /api/v1/auth/logout` blacklists the refresh token. `GET /api/v1/auth/me`
returns the current user object as the envelope's `data`.

Never pass user IDs or tenant IDs in request bodies or query strings; every
endpoint derives them from the verified token. The backend scopes every queryset
to the caller's workspace and re-checks foreign keys supplied by clients.
Cross-workspace access returns empty results or a `400`/`403`, not another
workspace's data.

## Connection flow endpoints

| Step | Endpoint | Notes |
| --- | --- | --- |
| Start | `POST /telegram/auth/start` `{phone_number}` | E.164; returns `challenge_id`, `status`, `expires_at` (202) |
| Code | `POST /telegram/auth/verify` `{challenge_id, code}` | `status` becomes `complete` or `password_required` |
| 2FA | `POST /telegram/auth/2fa` `{challenge_id, password}` | only when `password_required` |
| Status | `GET` accounts list / detail | safe fields only |

Errors are sanitized and enveloped as
`{ success: false, data: null, message, error: { code, details } }`. Telegram
FloodWait uses `400`, `error.code = "flood_wait"` and
`error.details.retry_after_seconds`; throttle errors use `429` and
`error.code = "throttled"`. Codes, passwords, and session strings never appear in
responses or logs. All three challenge endpoints return
`{ challenge_id, status, expires_at }`, so advancing the wizard does not lose the
expiry hint.

## Account management endpoints

- `GET /telegram/accounts/` — paginated list: `id`, `phone_masked`, `telegram_user_id`, `display_name`, `username`, `status`, `last_connected_at`, `last_activity_at`, `last_error`.
- `GET /telegram/accounts/{id}/` — single account.
- `GET /telegram/accounts/{id}/health/` — `status`, `session_present`, `worker_active`, timestamps.
- `GET /telegram/accounts/{id}/events/` — recent connection events (`connected`, `code_sent`, `password_required`, `error`, `revoked`, `disconnected`).
- `POST /telegram/accounts/{id}/reconnect/` — re-attach the worker to a stored session.
- `POST /telegram/accounts/{id}/disconnect/` — revoke the Telegram auth key (best effort), delete the stored session, mark `disconnected`.
- `GET /telegram/accounts/{id}/dialogs/` — sync dialogs into saved entities for relay configuration.

Router-based routes (`/telegram/accounts/`, `/telegram/entities/`, `/relays/`, `/messages/`, `/logs/`) **require a trailing slash**; calling one without it returns `301`. Keep the slash — browsers convert a `301` on `POST` into `GET`, which silently breaks write actions.

## UI requirements

Use the website's existing design system. A Telegram Accounts section needs: Connect button → phone → code → optional 2FA password → success; account list with status badges, masked phone, last activity, reconnect and disconnect actions; loading, empty, and error states; recovery from expired challenges (offer "start again" once `expires_at` passes). Gate these controls on an admin/owner role or platform superuser. Never render session strings or raw phone numbers.

The signal feed is readable by all members. Watched chat management and relay writes require an admin role. When offering forwarding, send the destination **entity UUID** from `dialogs[].destination_id` as `forward_to`; raw peer IDs are rejected.

### Signal forwarding templates

Admins can create reusable, workspace-scoped formatting templates and assign one to a `SignalSource` via the `template` field (a template UUID; omit or send `null` for raw text). Sources also return `template_name`. Templates never change stored `Signal.text` — they only format the copy sent to `forward_to`, for both live messages and backfills.

| Method | Endpoint                         | Purpose                               |
| ------ | -------------------------------- | ------------------------------------- |
| GET    | `/signal-templates/`             | List workspace templates              |
| POST   | `/signal-templates/`             | Create a template                     |
| PATCH  | `/signal-templates/{id}/`        | Update a template                     |
| DELETE | `/signal-templates/{id}/`        | Delete a template                     |
| POST   | `/signal-templates/preview/`     | Dry-run a config against sample text  |

Template fields: `name`, `body`, `header`, `footer`, `strip_header_lines`, `remove_urls`, `remove_patterns` (array of regex strings), `replacements` (array of `{ "from", "to" }`), `drop_empty`. The body is Django template syntax rendered with `autoescape=False`; placeholders are `{{ text }}`, `{{ source }}`, `{{ account }}`, `{{ author }}`, `{{ sent_at }}`, `{{ date }}`, `{{ message_id }}` and `{{ peer_id }}`. Order of operations: strip header lines → regex removals → URL removal → literal replacements → render body → prepend header / append footer → trim → cap at 4000 chars → skip if empty and `drop_empty` is set. The preview endpoint accepts the same config plus `text`, `source`, `account`, `author`, `sent_at`, `message_id`, `peer_id` and returns `{ formatted, skipped }` without sending anything. Invalid regexes, template syntax and oversized configs are rejected with `400`. A template must belong to the source account's workspace or the source write fails validation.

## Workspaces (multi-tenancy)

Every account belongs to a workspace. Signup creates one by default; joining an
existing workspace requires an invite code. See
[`docs/multi-tenancy.md`](multi-tenancy.md) for the full model.

```ts
// New workspace: the caller becomes its owner ("super admin" of that workspace)
await apiRequest("/auth/register", {
  method: "POST",
  body: { email: "founder@example.com", password: "…", tenant_name: "Acme" },
});

// Join an existing workspace with an invite code (optionally email-locked)
await apiRequest("/auth/register", {
  method: "POST",
  body: { email: "friend@example.com", password: "…", invite_code: code },
});
```

`auth/me` and the register/login payload include `role` and `tenant`:

```json
{ "id": 1, "email": "…", "is_superuser": false,
  "role": "owner",
  "tenant": { "id": "…", "name": "Acme", "slug": "acme" } }
```

Gate administrative UI on `role === "owner" || role === "admin" ||
is_superuser`: the backend treats platform superusers as workspace owners too.
Members see only their workspace's signal feed. Workspace management lives under
`/tenant/…` — see the endpoint table in `docs/multi-tenancy.md`.
