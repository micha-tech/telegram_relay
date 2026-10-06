# Telegram authentication

Create Telegram API credentials at `my.telegram.org` and set `TELEGRAM_API_ID` and `TELEGRAM_API_HASH` only on the backend. Authenticate with:

1. `POST /api/telegram/auth/start` with `phone_number`.
2. `POST /api/telegram/auth/verify` with returned `challenge_id` and the code.
3. If status is `password_required`, call `POST /api/telegram/auth/2fa` with the challenge and password.

Challenges expire after ten minutes, are user-bound, attempt-limited, and persisted rather than held in process memory. The temporary and final `StringSession`, phone-code hash, access hashes, codes, API hash, and 2FA password are never returned. Session and challenge credentials are encrypted with `TELEGRAM_SESSION_ENCRYPTION_KEY`; key rotation requires an explicit data migration before retiring an old key.

