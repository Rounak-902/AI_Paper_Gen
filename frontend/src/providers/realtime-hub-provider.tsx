"use client";

import { useQueryClient } from "@tanstack/react-query";
import {
  createContext,
  useCallback,
  useContext,
  useEffect,
  useMemo,
  useRef,
  useState,
  type ReactNode,
} from "react";
import { getPaperRealtimeState, paperBuilderKeys } from "@/features/paper-builder/api";
import { patchRealtimeSnapshot, type PaperEvent } from "@/features/paper-builder/realtime-cache";
import type { PaperRealtimeState } from "@/features/paper-builder/types";
import { resolveApiUrl } from "@/services/api-client";
import { useSessionStore } from "@/stores/session";

export const SSE_DEAD_CONNECTION_MS = 40_000;
export const SSE_FAILURES_BEFORE_POLLING = 3;
export const REALTIME_POLL_INTERVAL_MS = 2_000;

export type RealtimeMode = "idle" | "connecting" | "open" | "reconnecting" | "polling";

type PaperListener = {
  onEvent: (event: PaperEvent) => void;
  onSnapshot: (snapshot: PaperRealtimeState) => void;
};

type HubContextValue = {
  mode: RealtimeMode;
  subscribe: (paperId: string, listener: PaperListener) => () => void;
};

const HubContext = createContext<HubContextValue | null>(null);
let abortActiveHub: (() => void) | null = null;

/** Close the old-tenant stream before cache/store/token replacement. */
export function stopRealtimeHub() {
  abortActiveHub?.();
  abortActiveHub = null;
}

export function RealtimeHubProvider({ children }: { children: ReactNode }) {
  const queryClient = useQueryClient();
  const accessToken = useSessionStore((state) => state.accessToken);
  const activeOrgId = useSessionStore((state) => state.activeOrgId);
  const [paperId, setPaperId] = useState<string | null>(null);
  const [mode, setMode] = useState<RealtimeMode>("idle");
  const listeners = useRef(new Map<string, Set<PaperListener>>());
  const lastEventIds = useRef(new Map<string, string>());
  const seenEventIds = useRef(new Map<string, Set<string>>());

  const subscribe = useCallback((nextPaperId: string, listener: PaperListener) => {
    const group = listeners.current.get(nextPaperId) ?? new Set<PaperListener>();
    group.add(listener);
    listeners.current.set(nextPaperId, group);
    setPaperId(nextPaperId);
    return () => {
      const current = listeners.current.get(nextPaperId);
      current?.delete(listener);
      if (current?.size === 0) listeners.current.delete(nextPaperId);
      const remaining = Array.from(listeners.current.keys());
      setPaperId(remaining.at(-1) ?? null);
    };
  }, []);

  useEffect(() => {
    if (!activeOrgId || !accessToken || !paperId) {
      setMode("idle");
      return;
    }

    const controller = new AbortController();
    const resourceKey = `${activeOrgId}:${paperId}`;
    let consecutiveFailures = 0;
    let pollingCycles = 0;
    abortActiveHub = () => controller.abort();

    const publishSnapshot = (snapshot: PaperRealtimeState) => {
      queryClient.setQueryData(paperBuilderKeys.realtime(activeOrgId, paperId), snapshot);
      for (const listener of listeners.current.get(paperId) ?? []) listener.onSnapshot(snapshot);
      if (snapshot.versionId) {
        void queryClient.invalidateQueries({
          queryKey: paperBuilderKeys.coverageReport(activeOrgId, paperId, snapshot.versionId),
        });
      }
    };

    const reconcile = async () => {
      const snapshot = await getPaperRealtimeState(paperId, controller.signal);
      if (!controller.signal.aborted) publishSnapshot(snapshot);
    };

    const publishEvent = (event: PaperEvent) => {
      if (event.orgId !== activeOrgId || event.resourceId !== paperId) return;
      const seen = seenEventIds.current.get(resourceKey) ?? new Set<string>();
      if (seen.has(event.eventId)) return;
      seen.add(event.eventId);
      while (seen.size > 512) seen.delete(seen.values().next().value!);
      seenEventIds.current.set(resourceKey, seen);
      lastEventIds.current.set(resourceKey, event.eventId);
      queryClient.setQueryData<PaperRealtimeState>(
        paperBuilderKeys.realtime(activeOrgId, paperId),
        (current) => patchRealtimeSnapshot(current, event, paperId),
      );
      for (const listener of listeners.current.get(paperId) ?? []) listener.onEvent(event);
      if (event.type === "paper.generated") void reconcile();
    };

    const consumeOnce = async () => {
      setMode(consecutiveFailures === 0 ? "connecting" : "reconnecting");
      const headers = new Headers({
        Accept: "text/event-stream",
        Authorization: `Bearer ${accessToken}`,
      });
      const lastEventId = lastEventIds.current.get(resourceKey);
      if (lastEventId) headers.set("Last-Event-ID", lastEventId);
      const response = await fetch(resolveApiUrl(`/api/v1/papers/${paperId}/events`), {
        headers,
        signal: controller.signal,
      });
      if (!response.ok || !response.body) throw new Error(`SSE unavailable (${response.status})`);
      await reconcile();
      setMode("open");
      const reader = response.body.getReader();
      const decoder = new TextDecoder();
      let buffer = "";
      while (!controller.signal.aborted) {
        const { done, value } = await readWithDeadline(reader, controller.signal);
        if (done) throw new Error("SSE stream ended");
        buffer += decoder.decode(value, { stream: true });
        const frames = buffer.split(/\r?\n\r?\n/);
        buffer = frames.pop() ?? "";
        for (const frame of frames) {
          if (frame.includes(": heartbeat")) consecutiveFailures = 0;
          const event = parsePaperEvent(frame);
          if (event) {
            consecutiveFailures = 0;
            publishEvent(event);
          }
        }
      }
    };

    const run = async () => {
      while (!controller.signal.aborted) {
        if (consecutiveFailures >= SSE_FAILURES_BEFORE_POLLING) {
          setMode("polling");
          try {
            await reconcile();
          } catch {
            // Polling remains active and retries; the last durable snapshot stays visible.
          }
          pollingCycles += 1;
          await delay(REALTIME_POLL_INTERVAL_MS, controller.signal);
          if (pollingCycles >= 5) {
            pollingCycles = 0;
            consecutiveFailures = 0;
          }
          continue;
        }
        try {
          await consumeOnce();
        } catch {
          if (controller.signal.aborted) break;
          consecutiveFailures += 1;
          setMode("reconnecting");
          const ceiling = Math.min(30_000, 1_000 * 2 ** (consecutiveFailures - 1));
          await delay(Math.max(250, Math.random() * ceiling), controller.signal);
        }
      }
    };

    void run();
    return () => {
      controller.abort();
      if (abortActiveHub) abortActiveHub = null;
      setMode("idle");
    };
  }, [accessToken, activeOrgId, paperId, queryClient]);

  const value = useMemo(() => ({ mode, subscribe }), [mode, subscribe]);
  return <HubContext.Provider value={value}>{children}</HubContext.Provider>;
}

export function usePaperRealtimeSubscription(paperId: string | null, listener: PaperListener) {
  const hub = useContext(HubContext);
  if (!hub) throw new Error("usePaperRealtimeSubscription requires RealtimeHubProvider");
  const { mode, subscribe } = hub;
  const listenerRef = useRef(listener);
  listenerRef.current = listener;
  useEffect(() => {
    if (!paperId) return;
    return subscribe(paperId, {
      onEvent: (event) => listenerRef.current.onEvent(event),
      onSnapshot: (snapshot) => listenerRef.current.onSnapshot(snapshot),
    });
  }, [paperId, subscribe]);
  return mode;
}

function parsePaperEvent(frame: string): PaperEvent | null {
  const data = frame
    .split(/\r?\n/)
    .filter((line) => line.startsWith("data:"))
    .map((line) => line.slice(5).trimStart())
    .join("\n");
  if (!data) return null;
  try {
    const event = JSON.parse(data) as PaperEvent;
    return event.eventId && event.orgId && event.resourceId && typeof event.sequence === "number" ? event : null;
  } catch {
    return null;
  }
}

async function readWithDeadline(reader: ReadableStreamDefaultReader<Uint8Array>, signal: AbortSignal) {
  return new Promise<ReadableStreamReadResult<Uint8Array>>((resolve, reject) => {
    const timeout = window.setTimeout(() => {
      void reader.cancel();
      reject(new Error("SSE heartbeat deadline exceeded"));
    }, SSE_DEAD_CONNECTION_MS);
    const aborted = () => reject(new DOMException("Aborted", "AbortError"));
    signal.addEventListener("abort", aborted, { once: true });
    void reader.read().then(resolve, reject).finally(() => {
      window.clearTimeout(timeout);
      signal.removeEventListener("abort", aborted);
    });
  });
}

function delay(milliseconds: number, signal: AbortSignal) {
  return new Promise<void>((resolve) => {
    if (signal.aborted) return resolve();
    const timeout = window.setTimeout(resolve, milliseconds);
    signal.addEventListener("abort", () => {
      window.clearTimeout(timeout);
      resolve();
    }, { once: true });
  });
}

