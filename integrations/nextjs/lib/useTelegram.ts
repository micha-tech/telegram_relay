/**
 * React hooks for the Telegram connect flow and account list.
 *
 * Copy this file into your Next.js app as `lib/useTelegram.ts`.
 * Client components only ("use client").
 */
"use client";

import { useCallback, useEffect, useRef, useState } from "react";

import {
  ApiError,
  type Account,
  type TelegramApi,
  telegramApi,
} from "./telegramApi";

export type ConnectStep =
  | "idle"
  | "phone"
  | "code"
  | "password"
  | "done";

export interface UseTelegramConnectResult {
  step: ConnectStep;
  loading: boolean;
  error: string | null;
  challengeId: string | null;
  expiresAt: string | null;
  accountConnected: boolean;
  begin: () => void;
  submitPhone: (phoneNumber: string) => Promise<void>;
  submitCode: (code: string) => Promise<void>;
  submitPassword: (password: string) => Promise<void>;
  reset: () => void;
}

/**
 * Drives: phone -> code -> (2FA password) -> done.
 * `onConnected` fires after successful completion so you can refresh the list.
 */
export function useTelegramConnect(
  options: { api?: TelegramApi; onConnected?: (account?: Account) => void } = {},
): UseTelegramConnectResult {
  const api = options.api ?? telegramApi;
  const [step, setStep] = useState<ConnectStep>("idle");
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [challengeId, setChallengeId] = useState<string | null>(null);
  const [expiresAt, setExpiresAt] = useState<string | null>(null);
  const [accountConnected, setAccountConnected] = useState(false);
  const mounted = useRef(true);
  const onConnected = useRef(options.onConnected);
  onConnected.current = options.onConnected;

  useEffect(() => {
    mounted.current = true;
    return () => {
      mounted.current = false;
    };
  }, []);

  const fail = useCallback((err: unknown) => {
    if (err instanceof ApiError) setError(err.message);
    else setError("Something went wrong, please try again");
  }, []);

  const begin = useCallback(() => {
    setError(null);
    setStep("phone");
  }, []);

  const submitPhone = useCallback(
    async (phoneNumber: string) => {
      setLoading(true);
      setError(null);
      try {
        const result = await api.startConnection(phoneNumber.trim());
        if (!mounted.current) return;
        setChallengeId(result.challenge_id);
        setExpiresAt(result.expires_at);
        setStep(result.status === "password_required" ? "password" : "code");
      } catch (err) {
        fail(err);
      } finally {
        if (mounted.current) setLoading(false);
      }
    },
    [api, fail],
  );

  const afterVerify = useCallback((status: string) => {
    if (status === "password_required") {
      setStep("password");
      return false;
    }
    setStep("done");
    setAccountConnected(true);
    onConnected.current?.();
    return true;
  }, []);

  const submitCode = useCallback(
    async (code: string) => {
      if (!challengeId) return;
      setLoading(true);
      setError(null);
      try {
        const result = await api.verifyCode(challengeId, code.trim());
        if (!mounted.current) return;
        afterVerify(result.status);
      } catch (err) {
        fail(err);
      } finally {
        if (mounted.current) setLoading(false);
      }
    },
    [api, challengeId, afterVerify, fail],
  );

  const submitPassword = useCallback(
    async (password: string) => {
      if (!challengeId) return;
      setLoading(true);
      setError(null);
      try {
        const result = await api.verifyPassword(challengeId, password);
        if (!mounted.current) return;
        afterVerify(result.status);
      } catch (err) {
        fail(err);
      } finally {
        if (mounted.current) setLoading(false);
      }
    },
    [api, challengeId, afterVerify, fail],
  );

  const reset = useCallback(() => {
    setStep("idle");
    setError(null);
    setChallengeId(null);
    setExpiresAt(null);
    setLoading(false);
    setAccountConnected(false);
  }, []);

  return {
    step,
    loading,
    error,
    challengeId,
    expiresAt,
    accountConnected,
    begin,
    submitPhone,
    submitCode,
    submitPassword,
    reset,
  };
}

export interface UseTelegramAccountsResult {
  accounts: Account[];
  loading: boolean;
  error: string | null;
  refresh: () => Promise<void>;
  reconnect: (id: string) => Promise<void>;
  disconnect: (id: string) => Promise<void>;
}

export function useTelegramAccounts(
  options: { api?: TelegramApi; autoLoad?: boolean } = {},
): UseTelegramAccountsResult {
  const api = options.api ?? telegramApi;
  const [accounts, setAccounts] = useState<Account[]>([]);
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState<string | null>(null);

  const refresh = useCallback(async () => {
    setLoading(true);
    setError(null);
    try {
      const page = await api.listAccounts();
      setAccounts(page.results);
    } catch (err) {
      if (err instanceof ApiError) setError(err.message);
      else setError("Unable to load accounts");
    } finally {
      setLoading(false);
    }
  }, [api]);

  useEffect(() => {
    if (options.autoLoad === false) return;
    void refresh();
  }, [options.autoLoad, refresh]);

  const withRefresh = useCallback(
    async (action: (id: string) => Promise<Account>, id: string) => {
      setError(null);
      try {
        const updated = await action(id);
        setAccounts((prev) => prev.map((a) => (a.id === id ? updated : a)));
      } catch (err) {
        if (err instanceof ApiError) setError(err.message);
        else setError("Action failed");
        throw err;
      }
    },
    [],
  );

  const reconnect = useCallback(
    (id: string) => withRefresh((x) => api.reconnectAccount(x), id),
    [api, withRefresh],
  );
  const disconnect = useCallback(
    (id: string) => withRefresh((x) => api.disconnectAccount(x), id),
    [api, withRefresh],
  );

  return { accounts, loading, error, refresh, reconnect, disconnect };
}
