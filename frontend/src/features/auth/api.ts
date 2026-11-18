import { ApiError, apiClient } from "@/services/api-client";
import type { AuthSessionResponse } from "@/types/auth";

export type LoginInput = {
  email: string;
  password: string;
};

export type ForgotPasswordInput = {
  email: string;
};

export type AcceptInviteInput = {
  name: string;
  password: string;
  token: string;
};

export type SwitchOrgInput = {
  orgId: string;
};

let sessionRefreshPromise: Promise<AuthSessionResponse> | null = null;
const REFRESH_LOCK_DATABASE = "papergen-session-coordination";
const REFRESH_LOCK_STORE = "leases";
const REFRESH_LEASE_KEY = "session-refresh";
const REFRESH_LEASE_MS = 60_000;

type RefreshLease = {
  key: string;
  expiresAt: number;
  owner: string;
};

export function login(input: LoginInput) {
  return apiClient<AuthSessionResponse>("/api/auth/login", {
    body: input,
    method: "POST",
    skipAuth: true,
    skipAuthRefresh: true,
    timeoutMs: 45_000,
  });
}

export function refreshSession() {
  sessionRefreshPromise ??= runSerializedSessionRefresh().finally(() => {
    sessionRefreshPromise = null;
  });
  return sessionRefreshPromise;
}

export function logout() {
  return apiClient<{ ok: true }>("/api/auth/logout", {
    method: "POST",
    skipAuthRefresh: true,
    timeoutMs: 25_000,
  });
}

export function requestPasswordReset(input: ForgotPasswordInput) {
  return apiClient<{ submitted: true }>("/api/auth/password-reset", {
    body: input,
    method: "POST",
    skipAuth: true,
    skipAuthRefresh: true,
  });
}

export function acceptInvite(input: AcceptInviteInput) {
  return apiClient<{ accepted: true; email: string }>("/api/auth/accept-invite", {
    body: input,
    method: "POST",
    skipAuth: true,
    skipAuthRefresh: true,
    timeoutMs: 25_000,
  });
}

export function switchOrg(input: SwitchOrgInput) {
  return apiClient<AuthSessionResponse>("/api/auth/switch-org", {
    body: input,
    method: "POST",
    timeoutMs: 45_000,
  });
}

async function runSerializedSessionRefresh() {
  const refresh = () =>
    apiClient<AuthSessionResponse>("/api/auth/refresh", {
      method: "POST",
      skipAuth: true,
      skipAuthRefresh: true,
      timeoutMs: 45_000,
    });

  // Refresh tokens rotate after one use. The Web Lock serializes refreshes
  // across same-origin tabs; the module promise handles concurrent callers in
  // this tab, including React Strict Mode's development remount.
  if (typeof navigator !== "undefined" && navigator.locks) {
    return navigator.locks.request("papergen-session-refresh", refresh);
  }

  if (typeof window !== "undefined") {
    return withIndexedDbRefreshLease(refresh);
  }

  return refresh();
}

async function withIndexedDbRefreshLease<T>(task: () => Promise<T>): Promise<T> {
  if (typeof indexedDB === "undefined") {
    throw coordinationUnavailable();
  }

  const owner =
    globalThis.crypto?.randomUUID?.() ??
    `refresh_${Date.now()}_${Math.random().toString(36).slice(2)}`;

  for (;;) {
    let acquired: boolean;
    try {
      acquired = await tryAcquireRefreshLease(owner);
    } catch {
      throw coordinationUnavailable();
    }

    if (acquired) {
      const heartbeat = window.setInterval(() => {
        void renewRefreshLease(owner);
      }, 5_000);
      try {
        return await task();
      } finally {
        window.clearInterval(heartbeat);
        await releaseRefreshLease(owner).catch(() => undefined);
      }
    }

    await delay(100 + Math.floor(Math.random() * 100));
  }
}

async function tryAcquireRefreshLease(owner: string) {
  return runLeaseTransaction<boolean>((store, finish) => {
    const request = store.get(REFRESH_LEASE_KEY);
    request.onsuccess = () => {
      const current = validRefreshLease(request.result) ? request.result : null;
      if (!current || current.expiresAt <= Date.now()) {
        store.put({
          expiresAt: Date.now() + REFRESH_LEASE_MS,
          key: REFRESH_LEASE_KEY,
          owner,
        } satisfies RefreshLease);
        finish(true);
      } else {
        finish(false);
      }
    };
  });
}

async function renewRefreshLease(owner: string) {
  await runLeaseTransaction<void>((store, finish) => {
    const request = store.get(REFRESH_LEASE_KEY);
    request.onsuccess = () => {
      const current = validRefreshLease(request.result) ? request.result : null;
      if (current?.owner === owner) {
        store.put({ ...current, expiresAt: Date.now() + REFRESH_LEASE_MS });
      }
      finish();
    };
  }).catch(() => undefined);
}

async function releaseRefreshLease(owner: string) {
  await runLeaseTransaction<void>((store, finish) => {
    const request = store.get(REFRESH_LEASE_KEY);
    request.onsuccess = () => {
      const current = validRefreshLease(request.result) ? request.result : null;
      if (current?.owner === owner) store.delete(REFRESH_LEASE_KEY);
      finish();
    };
  });
}

async function runLeaseTransaction<T>(
  operation: (store: IDBObjectStore, finish: (value: T) => void) => void,
) {
  const database = await openRefreshLockDatabase();
  return new Promise<T>((resolve, reject) => {
    const transaction = database.transaction(REFRESH_LOCK_STORE, "readwrite");
    const store = transaction.objectStore(REFRESH_LOCK_STORE);
    let result: T;
    operation(store, (value) => {
      result = value;
    });
    transaction.oncomplete = () => {
      database.close();
      resolve(result);
    };
    transaction.onerror = () => {
      database.close();
      reject(transaction.error ?? new Error("Refresh lock transaction failed."));
    };
    transaction.onabort = () => {
      database.close();
      reject(transaction.error ?? new Error("Refresh lock transaction was aborted."));
    };
  });
}

function openRefreshLockDatabase() {
  return new Promise<IDBDatabase>((resolve, reject) => {
    const request = indexedDB.open(REFRESH_LOCK_DATABASE, 1);
    request.onupgradeneeded = () => {
      if (!request.result.objectStoreNames.contains(REFRESH_LOCK_STORE)) {
        request.result.createObjectStore(REFRESH_LOCK_STORE, { keyPath: "key" });
      }
    };
    request.onsuccess = () => resolve(request.result);
    request.onerror = () => reject(request.error ?? new Error("Refresh lock database failed."));
    request.onblocked = () => reject(new Error("Refresh lock database upgrade was blocked."));
  });
}

function validRefreshLease(value: unknown): value is RefreshLease {
  if (!value || typeof value !== "object") return false;
  const candidate = value as Partial<RefreshLease>;
  return (
    candidate.key === REFRESH_LEASE_KEY &&
    typeof candidate.expiresAt === "number" &&
    typeof candidate.owner === "string"
  );
}

function coordinationUnavailable() {
  return new ApiError({
    code: "refresh_coordination_unavailable",
    message: "This browser could not safely coordinate the session refresh. Please try again.",
    retryable: true,
    status: 0,
  });
}

function delay(milliseconds: number) {
  return new Promise<void>((resolve) => window.setTimeout(resolve, milliseconds));
}
