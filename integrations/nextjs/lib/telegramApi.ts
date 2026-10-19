/**
 * Typed client for the Telegram Relay backend (/api/v1).
 *
 * Copy this file into your Next.js app as `lib/telegramApi.ts`.
 * Configure in .env.local:
 *   NEXT_PUBLIC_TELEGRAM_API_BASE_URL=https://<api-host>/api/v1
 */

export type ConnectionStatus =
  | "code_sent"
  | "password_required"
  | "complete"
  | "failed"
  | "expired";

export type AccountStatus =
  | "pending"
  | "connected"
  | "disconnected"
  | "invalid"
  | "error";

export type ConnectionEventKind =
  | "auth_started"
  | "code_sent"
  | "password_required"
  | "connected"
  | "disconnected"
  | "revoked"
  | "error";

export interface Account {
  id: string;
  phone_masked: string;
  telegram_user_id: number | null;
  display_name: string;
  username: string;
  status: AccountStatus;
  last_connected_at: string | null;
  last_activity_at: string | null;
  last_error: string;
  created_at: string;
}

export interface AccountHealth {
  status: AccountStatus;
  session_present: boolean;
  worker_active: boolean | null;
  last_connected_at: string | null;
  last_activity_at: string | null;
  last_error: string;
  checked_at: string;
}

export interface ConnectionEvent {
  kind: ConnectionEventKind;
  detail: string;
  created_at: string;
}

export interface Paginated<T> {
  count: number;
  next: string | null;
  previous: string | null;
  results: T[];
}

export interface StartConnectionResult {
  challenge_id: string;
  status: ConnectionStatus;
  expires_at: string;
}

export interface VerifyResult {
  challenge_id: string;
  status: ConnectionStatus;
}

export interface Dialog {
  peer_id: number;
  entity_type: string;
  username: string;
  title: string;
  source_id: string;
  destination_id: string;
}

export interface AuthUser {
  id: number | string;
  email: string;
}

export interface AuthTokens {
  access: string;
  refresh: string;
  access_expires_seconds?: number;
}

export interface LoginResult extends AuthTokens {
  user: AuthUser;
}

interface ApiErrorBody {
  success?: boolean;
  message?: string;
  error?: { code?: string; details?: Record<string, string[]> | null };
}

export class ApiError extends Error {
  readonly status: number;
  readonly code: string;
  readonly details: Record<string, string[]> | null;

  constructor(
    message: string,
    status: number,
    code = "REQUEST_ERROR",
    details: Record<string, string[]> | null = null,
  ) {
    super(message);
    this.name = "ApiError";
    this.status = status;
    this.code = code;
    this.details = details;
  }

  /** First validation message for a field, if any. */
  fieldError(field: string): string | undefined {
    return this.details?.[field]?.[0];
  }
}

export interface TokenStore {
  getAccess(): string | null;
  getRefresh(): string | null;
  setTokens(tokens: { access: string; refresh?: string | null }): void;
  clear(): void;
}

const ACCESS_KEY = "telegram_relay_access";
const REFRESH_KEY = "telegram_relay_refresh";

export const browserTokenStore: TokenStore = {
  getAccess: () =>
    typeof window === "undefined" ? null : window.localStorage.getItem(ACCESS_KEY),
  getRefresh: () =>
    typeof window === "undefined" ? null : window.localStorage.getItem(REFRESH_KEY),
  setTokens: ({ access, refresh }) => {
    if (typeof window === "undefined") return;
    window.localStorage.setItem(ACCESS_KEY, access);
    if (refresh !== undefined) {
      if (refresh) window.localStorage.setItem(REFRESH_KEY, refresh);
      else window.localStorage.removeItem(REFRESH_KEY);
    }
  },
  clear: () => {
    if (typeof window === "undefined") return;
    window.localStorage.removeItem(ACCESS_KEY);
    window.localStorage.removeItem(REFRESH_KEY);
  },
};

/** In-memory store for SSR or if you keep tokens elsewhere. */
export function memoryTokenStore(): TokenStore {
  let access: string | null = null;
  let refresh: string | null = null;
  return {
    getAccess: () => access,
    getRefresh: () => refresh,
    setTokens: (t) => {
      access = t.access;
      if (t.refresh !== undefined) refresh = t.refresh;
    },
    clear: () => {
      access = null;
      refresh = null;
    },
  };
}

export interface TelegramApiOptions {
  baseUrl?: string;
  tokens?: TokenStore;
  fetchImpl?: typeof fetch;
  /** Called when the session cannot be recovered; typically redirect to login. */
  onUnauthorized?: () => void;
}

export function createTelegramApi(options: TelegramApiOptions = {}) {
  const baseUrl = (
    options.baseUrl ??
    process.env.NEXT_PUBLIC_TELEGRAM_API_BASE_URL ??
    ""
  ).replace(/\/$/, "");
  const tokens = options.tokens ?? browserTokenStore;
  const doFetch = options.fetchImpl ?? fetch;

  let refreshInFlight: Promise<string | null> | null = null;

  async function parse(res: Response): Promise<unknown> {
    const text = await res.text();
    if (!text) return null;
    try {
      return JSON.parse(text);
    } catch {
      return null;
    }
  }

  function toError(res: Response, body: unknown): ApiError {
    const b = (body ?? {}) as ApiErrorBody;
    return new ApiError(
      b.message || res.statusText || "Request failed",
      res.status,
      b.error?.code ?? "REQUEST_ERROR",
      b.error?.details ?? null,
    );
  }

  async function refreshAccessToken(): Promise<string | null> {
    const refresh = tokens.getRefresh();
    if (!refresh) return null;
    if (!refreshInFlight) {
      refreshInFlight = (async () => {
        try {
          const res = await doFetch(`${baseUrl}/auth/refresh`, {
            method: "POST",
            headers: { "Content-Type": "application/json" },
            body: JSON.stringify({ refresh }),
          });
          const body = (await parse(res)) as
            | { access?: string; refresh?: string }
            | ApiErrorBody
            | null;
          if (!res.ok || !body || !("access" in body) || !body.access) {
            return null;
          }
          tokens.setTokens({ access: body.access, refresh: body.refresh });
          return body.access;
        } catch {
          return null;
        } finally {
          refreshInFlight = null;
        }
      })();
    }
    return refreshInFlight;
  }

  async function request<T>(
    path: string,
    init: RequestInit = {},
    retry = true,
  ): Promise<T> {
    const access = tokens.getAccess();
    const res = await doFetch(`${baseUrl}${path}`, {
      ...init,
      headers: {
        "Content-Type": "application/json",
        ...(access ? { Authorization: `Bearer ${access}` } : {}),
        ...(init.headers as Record<string, string> | undefined),
      },
    });

    if (res.status === 401 && retry) {
      const newAccess = await refreshAccessToken();
      if (newAccess) return request<T>(path, init, false);
      tokens.clear();
      options.onUnauthorized?.();
    }

    const body = await parse(res);
    if (!res.ok || (body as ApiErrorBody | null)?.success === false) {
      throw toError(res, body);
    }
    return (body as { data: T }).data;
  }

  const json = (payload: unknown): RequestInit => ({
    method: "POST",
    body: JSON.stringify(payload),
  });

  return {
    tokens,

    // ---- application auth -------------------------------------------------
    async register(email: string, password: string): Promise<LoginResult> {
      const data = await request<LoginResult>("/auth/register", json({ email, password }));
      tokens.setTokens({ access: data.access, refresh: data.refresh });
      return data;
    },
    async login(email: string, password: string): Promise<LoginResult> {
      const data = await request<LoginResult>("/auth/login", json({ email, password }));
      tokens.setTokens({ access: data.access, refresh: data.refresh });
      return data;
    },
    me(): Promise<AuthUser> {
      return request<AuthUser>("/auth/me");
    },
    async logout(): Promise<void> {
      const refresh = tokens.getRefresh();
      try {
        if (refresh) await request<null>("/auth/logout", json({ refresh }));
      } finally {
        tokens.clear();
      }
    },

    // ---- Telegram connection flow ----------------------------------------
    startConnection(phoneNumber: string): Promise<StartConnectionResult> {
      return request<StartConnectionResult>(
        "/telegram/auth/start",
        json({ phone_number: phoneNumber }),
      );
    },
    verifyCode(challengeId: string, code: string): Promise<VerifyResult> {
      return request<VerifyResult>(
        "/telegram/auth/verify",
        json({ challenge_id: challengeId, code }),
      );
    },
    verifyPassword(challengeId: string, password: string): Promise<VerifyResult> {
      return request<VerifyResult>(
        "/telegram/auth/2fa",
        json({ challenge_id: challengeId, password }),
      );
    },

    // ---- accounts ---------------------------------------------------------
    listAccounts(): Promise<Paginated<Account>> {
      return request<Paginated<Account>>("/telegram/accounts/");
    },
    getAccount(id: string): Promise<Account> {
      return request<Account>(`/telegram/accounts/${id}/`);
    },
    getAccountHealth(id: string): Promise<AccountHealth> {
      return request<AccountHealth>(`/telegram/accounts/${id}/health/`);
    },
    getAccountEvents(id: string): Promise<ConnectionEvent[]> {
      return request<ConnectionEvent[]>(`/telegram/accounts/${id}/events/`);
    },
    reconnectAccount(id: string): Promise<Account> {
      return request<Account>(`/telegram/accounts/${id}/reconnect/`, { method: "POST" });
    },
    disconnectAccount(id: string): Promise<Account> {
      return request<Account>(`/telegram/accounts/${id}/disconnect/`, { method: "POST" });
    },
    listDialogs(id: string): Promise<Dialog[]> {
      return request<Dialog[]>(`/telegram/accounts/${id}/dialogs/`);
    },
  };
}

export type TelegramApi = ReturnType<typeof createTelegramApi>;

export const telegramApi = createTelegramApi();
