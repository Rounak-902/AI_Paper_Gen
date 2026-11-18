import type { PaperRealtimeState } from "./types";

export type PaperEvent = {
  eventId: string;
  type: string;
  orgId: string;
  resourceId: string;
  sequence: number;
  payload: Record<string, unknown>;
};

/** Pure cache reducer shared by the hub and its deterministic tests. */
export function patchRealtimeSnapshot(
  current: PaperRealtimeState | undefined,
  event: PaperEvent,
  paperId: string,
): PaperRealtimeState {
  const payload = event.payload;
  const runId = stringValue(payload.runId);
  const base: PaperRealtimeState = current ?? {
    paperId,
    runId,
    status: "queued",
    versionId: null,
    sequence: 0,
    filledSlots: 0,
    totalSlots: 0,
    slots: [],
  };
  if (event.sequence <= base.sequence) return base;
  let next = { ...base, sequence: event.sequence, runId: runId ?? base.runId };
  if (event.type === "paper.generation.progress") {
    next = {
      ...next,
      status: "running",
      filledSlots: numberValue(payload.filledSlots),
      totalSlots: numberValue(payload.totalSlots),
      slots: upsertSlot(next.slots, payload, stringValue(payload.status) ?? "running"),
    };
  } else if (event.type === "paper.question.ready") {
    next = { ...next, slots: upsertSlot(next.slots, payload, "completed") };
  } else if (event.type === "paper.generation.failed") {
    next = { ...next, status: "failed", slots: upsertSlot(next.slots, payload, "failed") };
  } else if (event.type === "paper.generated") {
    next = { ...next, status: "completed", versionId: stringValue(payload.version) };
  }
  return next;
}

function upsertSlot(
  slots: PaperRealtimeState["slots"],
  payload: Record<string, unknown>,
  status: string,
) {
  const slotId = stringValue(payload.slotId);
  const sectionId = stringValue(payload.sectionId);
  if (!slotId || !sectionId || !["pending", "running", "completed", "failed"].includes(status)) return slots;
  const existing = slots.find((slot) => slot.slotId === slotId);
  const slot: PaperRealtimeState["slots"][number] = {
    slotId,
    sectionId,
    position: numberValue(payload.position) || existing?.position || 1,
    status: status as PaperRealtimeState["slots"][number]["status"],
    attemptCount: numberValue(payload.attemptCount),
    question: recordValue(payload.question) ?? existing?.question ?? null,
    error: stringValue(payload.message) ?? existing?.error ?? null,
  };
  return [...slots.filter((item) => item.slotId !== slotId), slot];
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
