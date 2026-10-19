# Next.js integration

Drop-in typed client and React hooks for connecting this backend to the Vercel website.

## Install into your frontend

1. Copy `lib/telegramApi.ts` and `lib/useTelegram.ts` into your Next.js app's `lib/` (or `src/lib/`) directory.
2. Add to `.env.local` (and to Vercel project env vars):

   ```bash
   NEXT_PUBLIC_TELEGRAM_API_BASE_URL=https://<api-host>/api/v1
   ```

3. On the backend, set `CORS_ORIGINS=https://<your-site>.vercel.app`.

No extra npm dependencies — the files use only `fetch` and React.

## What you get

- `telegramApi` — pre-configured client (uses `NEXT_PUBLIC_TELEGRAM_API_BASE_URL` and `localStorage` tokens).
- `createTelegramApi(options)` — build a client with a custom base URL, token store, `fetch`, or `onUnauthorized` handler (e.g. redirect to login).
- `ApiError` — carries `status`, `code`, and `details` (validation field errors via `err.fieldError("phone_number")`).
- `useTelegramConnect` — phone → code → optional 2FA password state machine.
- `useTelegramAccounts` — list/refresh/reconnect/disconnect accounts.
- Full TypeScript types for accounts, health, events, pagination, and the connection flow.

Access tokens auto-refresh once on `401` using the stored refresh token; if refresh fails, `onUnauthorized` fires and tokens are cleared.

## Example component

```tsx
"use client";

import { useTelegramAccounts, useTelegramConnect } from "@/lib/useTelegram";

export function TelegramAccounts() {
  const { accounts, refresh, reconnect, disconnect, error } = useTelegramAccounts();
  const connect = useTelegramConnect({ onConnected: () => void refresh() });

  return (
    <section>
      <h2>Telegram accounts</h2>

      {connect.step === "idle" && (
        <button onClick={connect.begin}>Connect Telegram</button>
      )}

      {connect.step !== "idle" && connect.step !== "done" && (
        <form
          onSubmit={(e) => {
            e.preventDefault();
            const value = new FormData(e.currentTarget).get("value") as string;
            if (connect.step === "phone") void connect.submitPhone(value);
            if (connect.step === "code") void connect.submitCode(value);
            if (connect.step === "password") void connect.submitPassword(value);
          }}
        >
          <input
            name="value"
            placeholder={
              connect.step === "phone"
                ? "+15551234567"
                : connect.step === "code"
                  ? "Login code"
                  : "2FA password"
            }
            type={connect.step === "password" ? "password" : "text"}
            required
          />
          <button disabled={connect.loading}>
            {connect.loading ? "Working…" : "Continue"}
          </button>
          {connect.error && <p role="alert">{connect.error}</p>}
        </form>
      )}

      {connect.step === "done" && <p>Connected.</p>}

      {error && <p role="alert">{error}</p>}
      <ul>
        {accounts.map((a) => (
          <li key={a.id}>
            <span>{a.display_name || a.username || a.phone_masked}</span>{" "}
            <span>{a.phone_masked}</span> <span>{a.status}</span>
            <button onClick={() => void reconnect(a.id)}>Reconnect</button>
            <button onClick={() => void disconnect(a.id)}>Disconnect</button>
          </li>
        ))}
      </ul>
    </section>
  );
}
```

## Notes

- The API wraps every response as `{ success, data, message }` or `{ success: false, message, error }`; the client unwraps `data` and throws `ApiError` on failure. `POST /auth/refresh` is the one raw SimpleJWT response and is handled internally.
- Never render session strings; the API never returns them. Phone numbers arrive masked as `phone_masked`.
- Prefer `onUnauthorized` to clear app state and route to login when refresh fails.
