# Security

Passwords use Django's adaptive password hashers. JWT access tokens expire after 15 minutes and refresh tokens after seven days. Logout requires the refresh token and adds it to the server-side blacklist. Use HTTPS and avoid browser storage accessible to injected scripts when a secure same-site integration is possible.

CORS is an explicit allow-list. Ownership filters and serializer checks prevent cross-user object access. API responses expose masked phone numbers only and never include session material. ORM queries are parameterized. Authentication endpoints are throttled. Logs must contain identifiers and error classes, never passwords, Telegram API hashes/codes/session strings, JWT keys, or infrastructure URLs.

Telegram sessions are encrypted with Fernet (authenticated encryption); keys live only in environment variables, support rotation through `TELEGRAM_SESSION_ENCRYPTION_KEYS`, and each stored row records the key version used. Disconnect revokes the Telegram auth key best-effort and always deletes the stored session. Rotate secrets, restrict database roles, keep audit retention policy, and alert on invalid sessions, repeated login attempts, FloodWait, and publish failures.
