"use client";

import { useMemo } from "react";
import { usePaperRealtimeSubscription } from "@/providers/realtime-hub-provider";
import type { PaperEvent } from "./realtime-cache";
import { usePaperBuilderStore } from "./store";
import type { PaperRealtimeState } from "./types";

/** Subscribe the Paper Builder to the tab-wide hub; components never own a stream. */
export function usePaperEvents(paperId: string | null) {
  const listener = useMemo(() => ({
    onEvent: applyPaperEventToStore,
    onSnapshot: applyPaperSnapshotToStore,
  }), []);
  return usePaperRealtimeSubscription(paperId, listener);
}

export function applyPaperSnapshotToStore(snapshot: PaperRealtimeState) {
  usePaperBuilderStore.getState().reconcileGeneration(snapshot);
}

export function applyPaperEventToStore(event: PaperEvent) {
  const store = usePaperBuilderStore.getState();
  const payload = event.payload;
  const runId = stringValue(payload.runId);
  if (store.document.generation.runId && runId !== store.document.generation.runId) return;

  if (event.type === "paper.generation.progress" && runId) {
    store.mergeGenerationProgress({
      sequence: event.sequence,
      runId,
      filledSlots: numberValue(payload.filledSlots),
      totalSlots: numberValue(payload.totalSlots),
      status: stringValue(payload.status) ?? "running",
    });
  }
  if (event.type === "paper.question.ready") {
    const question = recordValue(payload.question);
    const sectionId = stringValue(payload.sectionId);
    const slotId = stringValue(payload.slotId);
    if (!question || !sectionId || !slotId) return;
    const source = stringValue(question.source);
    store.mergeExternalSlotPatch({
      sequence: event.sequence,
      sectionId,
      slotId,
      patch: {
        state: "ready",
        origin: source === "ai_generated" ? "ai_generated" : "bank",
        questionId: stringValue(question.id),
        question,
        marks: numberValue(question.marks),
        difficulty: stringValue(question.difficulty),
        bloomLevel: stringValue(question.bloom),
        chapterId: stringValue(question.chapterId ?? question.chapter_id),
        topicId: stringValue(question.topicId ?? question.topic_id),
        error: null,
      },
    });
  }
  if (event.type === "paper.generation.failed" && runId) {
    const sectionId = stringValue(payload.sectionId);
    const slotId = stringValue(payload.slotId);
    if (!sectionId || !slotId) return;
    store.failGeneration(
      event.sequence,
      runId,
      sectionId,
      slotId,
      stringValue(payload.message) ?? "Question generation failed. Retry generation.",
    );
  }
  if (event.type === "paper.generated" && runId) {
    store.finishGeneration(event.sequence, runId, stringValue(payload.version));
  }
}

function recordValue(value: unknown): Record<string, unknown> | null {
  return value && typeof value === "object" && !Array.isArray(value) ? value as Record<string, unknown> : null;
}

function stringValue(value: unknown): string | null {
  return typeof value === "string" && value ? value : null;
}

function numberValue(value: unknown): number {
  const parsed = Number(value);
  return Number.isFinite(parsed) ? parsed : 0;
}
