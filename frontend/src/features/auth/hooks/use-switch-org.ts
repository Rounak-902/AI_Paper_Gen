"use client";

import { useQueryClient } from "@tanstack/react-query";
import { useRouter } from "next/navigation";
import { useCallback, useState } from "react";
import { switchOrg } from "@/features/auth/api";
import { clearPaperBuilderLocalArtifacts } from "@/features/paper-builder/use-paper-draft-autosave";
import { usePaperBuilderStore } from "@/features/paper-builder/store";
import { stopRealtimeHub } from "@/providers/realtime-hub-provider";
import { useSessionStore } from "@/stores/session";

function safeInternalPath(path: string | null) {
  if (!path || !path.startsWith("/") || path.startsWith("//")) {
    return "/select-org";
  }

  if (path.startsWith("/login") || path.startsWith("/forgot-password") || path.startsWith("/accept-invite")) {
    return "/select-org";
  }

  return path;
}

export function useSwitchOrg() {
  const router = useRouter();
  const queryClient = useQueryClient();
  const setAuthenticated = useSessionStore((state) => state.setAuthenticated);
  const activeOrgId = useSessionStore((state) => state.activeOrgId);
  const [pendingOrgId, setPendingOrgId] = useState<string | null>(null);
  const [error, setError] = useState<string | null>(null);

  const switchToOrg = useCallback(
    async (orgId: string, nextPath?: string | null) => {
      setPendingOrgId(orgId);
      setError(null);

      try {
        stopRealtimeHub();
        await queryClient.cancelQueries();
        queryClient.clear();
        usePaperBuilderStore.getState().reset();
        if (activeOrgId) clearPaperBuilderLocalArtifacts(activeOrgId);
        const session = await switchOrg({ orgId });
        setAuthenticated(session);
        router.replace(safeInternalPath(nextPath ?? "/select-org"));
      } catch (caught) {
        setError(caught instanceof Error ? caught.message : "PaperGen could not switch organizations.");
      } finally {
        setPendingOrgId(null);
      }
    },
    [activeOrgId, queryClient, router, setAuthenticated],
  );

  return {
    error,
    pendingOrgId,
    switchToOrg,
  };
}
