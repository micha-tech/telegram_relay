# Multi-tenancy (workspaces)

Every user belongs to exactly one **workspace** (`Tenant`). A workspace owns its
Telegram accounts, scraped entities, relays, signal sources, and signals. Nothing
is shared between workspaces.

## Roles

| Role | Can do |
| --- | --- |
| `owner` | Everything an admin can, plus: create/remove admins, change roles, rename the workspace, transfer ownership safeguards |
| `admin` | Connect/disconnect Telegram accounts, list entities, manage signal sources and relays, invite/add members |
| `member` | Read the workspace signal feed, relays and processed messages; manage their own profile |

The first user of a workspace is its `owner` — that is the "super admin" of that
workspace. Global Django superusers (`is_superuser`) remain platform operators:
a superuser **with** a membership is confined to that membership's workspace, a
superuser **without** one sees every workspace through the API and is intended
for support/operations.

## Self-serve signup

`POST /api/v1/auth/register`

```json
{ "email": "founder@example.com", "password": "...", "tenant_name": "Acme Signals" }
```

Creates the user, creates the workspace (slug derived from the name, falling
back to `"<email>'s workspace"`), and makes the user its `owner`. The response is
the usual token pair plus a user object:

```json
{
  "success": true,
  "data": {
    "user": {
      "id": 1,
      "email": "founder@example.com",
      "is_superuser": false,
      "role": "owner",
      "tenant": { "id": "…", "name": "Acme Signals", "slug": "acme-signals" }
    },
    "access": "…",
    "refresh": "…"
  }
}
```

`GET /api/v1/auth/me` returns the same shape, so the UI can gate admin screens on
`role !== "member"` rather than `is_superuser`.

## Adding people to a workspace

### Invite codes (no email infrastructure required)

1. An owner/admin calls `POST /api/v1/tenant/invites/` with an optional `role`
   (`member` by default, `admin` by owner only), optional `email` lock, and
   `expires_in_days` (1–30, default 7).
2. The response contains a single-use `code`.
3. Anyone can preview it at `GET /api/v1/auth/invites/{code}` (public) to show
   the workspace name and the invited role.
4. The invitee registers with `invite_code` instead of `tenant_name`:

```json
{ "email": "friend@example.com", "password": "...", "invite_code": "…" }
```

The code is consumed atomically, bound to the created membership, and cannot be
reused. Expired, used, or wrong-email codes are rejected.

### Admin-created accounts

`POST /api/v1/tenant/members/` `{email, password, role}` creates the user and the
membership directly (owner may create admins; admins may create members only).
The member can then log in normally.

## Endpoints

| Method | Path | Access |
| --- | --- | --- |
| `GET` | `/tenant/current/` | admin |
| `PATCH` | `/tenant/rename/` `{name}` | owner |
| `GET` | `/tenant/members/` | admin |
| `POST` | `/tenant/members/` `{email, password, role}` | admin |
| `PATCH` | `/tenant/members/{user_id}/` `{role}` | owner |
| `DELETE` | `/tenant/members/{user_id}/` | admin (members) / owner (admins) |
| `GET` | `/tenant/invites/` | admin |
| `POST` | `/tenant/invites/` | admin |
| `DELETE` | `/tenant/invites/{id}/` | admin |
| `GET` | `/auth/invites/{code}` | public |

Constraints enforced by the API:

- a workspace always keeps at least one owner;
- you cannot change your own role or remove yourself;
- admins cannot create, promote, or remove admins — only owners can.

## Data isolation

All Telegram data hangs off `TelegramAccount.tenant`. Querysets are scoped with
`relay.tenancy.apply_scope`, which returns the user's workspace only (or every
workspace for a membership-less platform superuser). `Signal`, `SignalSource`,
`RelayConfiguration`, `ProcessedMessage`, and `TelegramEntity` are reached
through the account, so there is a single source of truth for the tenant.

Foreign keys supplied by clients are re-validated: an admin cannot create a
signal source or relay that points at another workspace's account or entity.

## Migrating an existing installation

Migration `relay/migrations/0008_tenancy.py`:

1. creates a `Default` workspace (`slug="default"`),
2. adds every existing user to it (first superuser → `owner`, everyone else →
   `member`),
3. assigns every existing Telegram account to its owner's workspace.

Existing data therefore stays visible to the same people after upgrade.
