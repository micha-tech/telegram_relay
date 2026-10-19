# Telegram authentication

Create Telegram API credentials at `my.telegram.org` and set `TELEGRAM_API_ID` and `TELEGRAM_API_HASH` only on the backend. Authenticate with:

1. `POST /api/v1/telegram/auth/start` with `phone_number`.
2. `POST /api/v1/telegram/auth/verify` with returned `challenge_id` and the code.
3. If status is `password_required`, call `POST /api/v1/telegram/auth/2fa` with the challenge and password.

Challenges expire after ten minutes, are user-bound, attempt-limited, and persisted rather than held in process memory. Flood waits and network failures return sanitized 400 responses with retry guidance. The temporary and final `StringSession`, phone-code hash, access hashes, codes, API hash, and 2FA password are never returned. Session and challenge credentials are encrypted with `TELEGRAM_SESSION_ENCRYPTION_KEY`; rotate by setting `TELEGRAM_SESSION_ENCRYPTION_KEYS` to the retired key(s), switching the primary key, re-encrypting via normal session saves, then dropping the old key. Reconnecting a known Telegram identity under a new phone number merges onto the existing account row (unique `(user, telegram_user_id)` constraint). Each state change is recorded as an account connection event.
