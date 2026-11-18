"use client";

import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { useCallback, useEffect, useRef, useState } from "react";
import { ApiError } from "@/services/api-client";
import { getPaperDraft, paperBuilderKeys, savePaperDraft } from "./api";
import { usePaperBuilderStore } from "./store";
import type { PaperDraft, PaperDraftResource } from "./types";

// Independent from the 650 ms feasibility timer: persistence protects work and must
// remain available even when feasibility is disabled or slow.
export const PAPER_DRAFT_AUTOSAVE_MS = 1_000;

type ConflictState = {
  server: PaperDraftResource;
};

function pointerKey(orgId: string) {
  return `papergen:paper-builder:draft:${orgId}`;
}

function recoveryPrefix(orgId: string) {
  return `papergen:paper-builder:recovery:${orgId}:`;
}

function recoveryKey(orgId: string, draftId: string) {
  return `${recoveryPrefix(orgId)}${draftId}:${Date.now()}`;
}

export function clearPaperBuilderLocalArtifacts(orgId: string) {
  window.localStorage.removeItem(pointerKey(orgId));
  const keys = Array.from({ length: window.localStorage.length }, (_, index) => window.localStorage.key(index));
  for (const key of keys) {
    // Recovery exports are deliberately ephemeral. Purge scoped and legacy
    // unscoped keys so no losing-tab content survives a tenant transition.
    if (key?.startsWith("papergen:paper-builder:recovery:")) window.localStorage.removeItem(key);
  }
}

function newDraftId() {
  return globalThis.crypto?.randomUUID?.() ?? `00000000-0000-4000-8000-${Date.now().toString().padStart(12, "0").slice(-12)}`;
}

function persistenceSnapshot(document: PaperDraft): PaperDraft {
  return {
    ...document,
    meta: {
      ...document.meta,
      dirtyPaths: [],
      lastSavedAt: null,
      saveError: null,
      saveState: "clean",
      lastEditKind: null,
    },
  };
}

export function usePaperDraftAutosave(orgId: string | null) {
  const queryClient = useQueryClient();
  const [draftId, setDraftId] = useState<string | null>(null);
  const [conflict, setConflict] = useState<ConflictState | null>(null);
  const hydratedKey = useRef<string | null>(null);
  const inFlight = useRef(false);
  const document = usePaperBuilderStore((state) => state.document);
  const persistence = usePaperBuilderStore((state) => state.persistence);
  const initializeDraft = usePaperBuilderStore((state) => state.initializeDraft);
  const setDraftLoading = usePaperBuilderStore((state) => state.setDraftLoading);
  const hydrateDraft = usePaperBuilderStore((state) => state.hydrateDraft);
  const reset = usePaperBuilderStore((state) => state.reset);

  useEffect(() => {
    hydratedKey.current = null;
    setConflict(null);
    reset();
    if (!orgId) {
      setDraftId(null);
      return;
    }
    const stored = window.localStorage.getItem(pointerKey(orgId));
    const nextDraftId = stored ?? newDraftId();
    window.localStorage.setItem(pointerKey(orgId), nextDraftId);
    setDraftId(nextDraftId);
    setDraftLoading(nextDraftId);
  }, [orgId, reset, setDraftLoading]);

  const draftQuery = useQuery({
    queryKey: paperBuilderKeys.draft(orgId ?? "", draftId ?? ""),
    queryFn: ({ signal }) => getPaperDraft(orgId!, draftId!, signal),
    enabled: Boolean(orgId && draftId),
    retry: false,
    staleTime: Infinity,
  });

  useEffect(() => {
    if (!orgId || !draftId) return;
    const key = `${orgId}:${draftId}`;
    if (hydratedKey.current === key) return;
    if (draftQuery.data) {
      hydratedKey.current = key;
      hydrateDraft(draftQuery.data);
      return;
    }
    if (draftQuery.error instanceof ApiError && draftQuery.error.status === 404) {
      hydratedKey.current = key;
      initializeDraft(draftId);
    }
  }, [draftId, draftQuery.data, draftQuery.error, hydrateDraft, initializeDraft, orgId]);

  const saveMutation = useMutation({
    mutationFn: ({
      id,
      revision,
      snapshot,
    }: {
      id: string;
      revision: number | null;
      snapshot: PaperDraft;
    }) => savePaperDraft(orgId!, id, {
      expectedRevision: revision,
      document: snapshot,
    }),
  });

  const persist = useCallback(async () => {
    if (!orgId || !draftId || inFlight.current) return;
    const state = usePaperBuilderStore.getState();
    if (state.persistence.phase === "conflict" || state.document.meta.saveState !== "dirty") return;
    inFlight.current = true;
    const sequence = state.persistence.editSequence;
    const revision = state.persistence.revision;
    const snapshot = persistenceSnapshot(state.document);
    state.beginSave(sequence);
    try {
      const resource = await saveMutation.mutateAsync({ id: draftId, revision, snapshot });
      queryClient.setQueryData(paperBuilderKeys.draft(orgId, resource.draftId), resource);
      usePaperBuilderStore.getState().acknowledgeSave(resource, sequence);
    } catch (error) {
      if (error instanceof ApiError && error.status === 409) {
        try {
          const server = await getPaperDraft(orgId, draftId);
          queryClient.setQueryData(paperBuilderKeys.draft(orgId, draftId), server);
          setConflict({ server });
        } catch {
          // Keep the local buffer intact even if the conflict follow-up read fails.
        }
        usePaperBuilderStore.getState().failSave(
          "Another tab saved a newer draft. Choose which copy to continue with.",
          true,
        );
      } else {
        const reorder = state.document.meta.lastEditKind === "reorder";
        usePaperBuilderStore.getState().failSave(
          reorder
            ? "Reorder kept locally, but it was not saved. Retry or Undo to roll it back."
            : "Changes are still in this tab, but autosave failed. Retry when the connection is available.",
          false,
        );
      }
    } finally {
      inFlight.current = false;
    }
  }, [draftId, orgId, queryClient, saveMutation]);

  useEffect(() => {
    if (
      !orgId
      || !draftId
      || persistence.phase === "loading"
      || persistence.phase === "idle"
      || persistence.phase === "conflict"
      || document.meta.saveState !== "dirty"
    ) return;
    const timeout = window.setTimeout(() => void persist(), PAPER_DRAFT_AUTOSAVE_MS);
    return () => window.clearTimeout(timeout);
  }, [document, draftId, orgId, persist, persistence.editSequence, persistence.phase]);

  const retry = useCallback(() => {
    const state = usePaperBuilderStore.getState();
    state.markDirty(state.document.meta.dirtyPaths[0] ?? "autosave.retry");
  }, []);

  const useServerCopy = useCallback(() => {
    if (!conflict || !draftId) return;
    const local = persistenceSnapshot(usePaperBuilderStore.getState().document);
    window.localStorage.setItem(recoveryKey(orgId!, draftId), JSON.stringify(local));
    hydrateDraft(conflict.server);
    setConflict(null);
  }, [conflict, draftId, hydrateDraft, orgId]);

  const saveMineAsCopy = useCallback(async () => {
    if (!orgId) return;
    const newId = newDraftId();
    const state = usePaperBuilderStore.getState();
    const sequence = state.persistence.editSequence;
    const snapshot = persistenceSnapshot(state.document);
    state.beginSave(sequence);
    try {
      const resource = await savePaperDraft(orgId, newId, {
        expectedRevision: null,
        document: snapshot,
      });
      window.localStorage.setItem(pointerKey(orgId), newId);
      setDraftId(newId);
      hydratedKey.current = `${orgId}:${newId}`;
      queryClient.setQueryData(paperBuilderKeys.draft(orgId, newId), resource);
      usePaperBuilderStore.getState().acknowledgeSave(resource, sequence);
      setConflict(null);
    } catch (error) {
      usePaperBuilderStore.getState().failSave(
        error instanceof Error ? error.message : "The separate copy could not be saved.",
        true,
      );
    }
  }, [orgId, queryClient]);

  const downloadLocalCopy = useCallback(() => {
    const snapshot = persistenceSnapshot(usePaperBuilderStore.getState().document);
    const blob = new Blob([JSON.stringify(snapshot, null, 2)], { type: "application/json" });
    const href = URL.createObjectURL(blob);
    const anchor = window.document.createElement("a");
    anchor.href = href;
    anchor.download = `papergen-draft-${draftId ?? "recovery"}.json`;
    anchor.click();
    URL.revokeObjectURL(href);
  }, [draftId]);

  return {
    conflict,
    downloadLocalCopy,
    isHydrated: persistence.phase !== "idle" && persistence.phase !== "loading",
    retry,
    saveMineAsCopy,
    useServerCopy,
  };
}
