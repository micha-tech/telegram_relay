# Security

Passwords use Django's adaptive password hashers. JWT access tokens expire after 15 minutes and refresh tokens after seven days; clients must discard both on logout. Use HTTPS, never browser storage accessible to injected scripts when a secure same-site integration is possible, and consider refresh-token blacklisting for higher-risk deployments.

CORS is an explicit allow-list. Ownership filters and serializer checks prevent cross-user object access. ORM queries are parameterized. Authentication endpoints are throttled. Logs must contain identifiers and error classes, never passwords, Telegram API hashes/codes/session strings, JWT keys, or infrastructure URLs. Rotate secrets, restrict database roles, keep audit retention policy, and alert on invalid sessions, repeated login attempts, FloodWait, and publish failures.
