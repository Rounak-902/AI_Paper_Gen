"use client";

import { useQueryClient } from "@tanstack/react-query";
import { usePathname, useRouter, useSearchParams } from "next/navigation";
import { useCallback, useEffect, useMemo, type ReactNode } from "react";
import { refreshSession } from "@/features/auth/api";
import { ApiError, configureApiAuth } from "@/services/api-client";
import { useSessionStore } from "@/stores/session";

export function SessionProvider({ children }: { children: ReactNode }) {
  const router = useRouter();
  const pathname = usePathname();
  const searchParams = useSearchParams();
  const queryClient = useQueryClient();
  const status = useSessionStore((state) => state.status);
  const activeOrgId = useSessionStore((state) => state.activeOrgId);
  const expiresAt = useSessionStore((state) => state.expiresAt);
  const setAuthenticated = useSessionStore((state) => state.setAuthenticated);
  const setBootstrapError = useSessionStore((state) => state.setBootstrapError);
  const setLoading = useSessionStore((state) => state.setLoading);
  const setUnauthenticated = useSessionStore((state) => state.setUnauthenticated);

  const currentPath = useMemo(() => {
    const query = searchParams.toString();
    return `${pathname}${query ? `?${query}` : ""}`;
  }, [pathname, searchParams]);

  const hardLogout = useCallback(
    (nextPath = currentPath) => {
      setUnauthenticated();
      queryClient.clear();

      const next = nextPath && !nextPath.startsWith("/login") ? `?next=${encodeURIComponent(nextPath)}` : "";
      router.replace(`/login${next}`);
    },
    [currentPath, queryClient, router, setUnauthenticated],
  );

  const refresh = useCallback(async () => {
    const session = await refreshSession();
    setAuthenticated(session);
    return session.accessToken;
  }, [setAuthenticated]);

  useEffect(() => {
    configureApiAuth({
      getToken: () => useSessionStore.getState().accessToken,
      handleUnauthenticated: () => hardLogout(),
      refresh,
    });
  }, [hardLogout, refresh]);

  useEffect(() => {
    let cancelled = false;
    let retryTimeout: number | null = null;

    async function bootstrap() {
      const current = useSessionStore.getState();

      if (current.status === "authenticated" && current.accessToken) {
        return;
      }

      setLoading();

      try {
        const session = await refreshSession();

        if (cancelled) {
          return;
        }

        setAuthenticated(session);
      } catch (error) {
        if (cancelled) return;
        if (isRejectedSession(error)) {
          hardLogout(currentPath);
          return;
        }
        setBootstrapError(
          error instanceof ApiError
            ? error.message
            : "PaperGen could not restore your session. Check that its services are running.",
        );
        retryTimeout = window.setTimeout(() => void bootstrap(), 5_000);
      }
    }

    void bootstrap();

    return () => {
      cancelled = true;
      if (retryTimeout !== null) window.clearTimeout(retryTimeout);
    };
  }, [currentPath, hardLogout, setAuthenticated, setBootstrapError, setLoading]);

  useEffect(() => {
    if (status === "authenticated" && !activeOrgId && pathname !== "/select-org") {
      router.replace(`/select-org?next=${encodeURIComponent(currentPath)}`);
    }
  }, [activeOrgId, currentPath, pathname, router, status]);

  useEffect(() => {
    if (status !== "authenticated" || !expiresAt) {
      return;
    }

    let cancelled = false;
    let timeout: number;
    const schedule = (delay: number) => {
      timeout = window.setTimeout(async () => {
        try {
          await refresh();
        } catch (error) {
          if (cancelled) return;
          if (isRejectedSession(error)) {
            hardLogout(currentPath);
          } else {
            schedule(30_000);
          }
        }
      }, delay);
    };

    schedule(Math.max(expiresAt - Date.now() - 60 * 1000, 5 * 1000));

    return () => {
      cancelled = true;
      window.clearTimeout(timeout);
    };
  }, [currentPath, expiresAt, hardLogout, refresh, status]);

  return children;
}

function isRejectedSession(error: unknown) {
  return error instanceof ApiError && error.status === 401;
}
