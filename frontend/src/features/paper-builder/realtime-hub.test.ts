import assert from "node:assert/strict";
import { beforeEach, describe, it } from "node:test";
import { patchRealtimeSnapshot, type PaperEvent } from "./realtime-cache";
import { usePaperBuilderStore } from "./store";
import type { PaperRealtimeState } from "./types";

const event = (sequence: number, type: string, payload: Record<string, unknown>): PaperEvent => ({
  eventId: `event-${sequence}`,
  type,
  orgId: "org-1",
  resourceId: "paper-1",
  sequence,
  payload,
});

describe("P5-T6 realtime hub", () => {
  beforeEach(() => usePaperBuilderStore.getState().reset());

  it("patches cache snapshots monotonically and materializes terminal state", () => {
    const running = patchRealtimeSnapshot(undefined, event(4, "paper.generation.progress", {
      runId: "run-1", sectionId: "A", slotId: "A:1", position: 1,
      status: "running", filledSlots: 0, totalSlots: 1,
    }), "paper-1");
    const ready = patchRealtimeSnapshot(running, event(5, "paper.question.ready", {
      runId: "run-1", sectionId: "A", slotId: "A:1", position: 1,
      question: { id: "question-1", stem: "Durable question" },
    }), "paper-1");
    const stale = patchRealtimeSnapshot(ready, event(3, "paper.generation.progress", {
      runId: "run-1", sectionId: "A", slotId: "A:1", status: "running",
    }), "paper-1");
    const completed = patchRealtimeSnapshot(ready, event(6, "paper.generated", {
      runId: "run-1", version: "version-1",
    }), "paper-1");

    assert.equal(ready.slots[0].question?.stem, "Durable question");
    assert.equal(stale, ready);
    assert.equal(completed.status, "completed");
    assert.equal(completed.versionId, "version-1");
  });

  it("reconciles missed ready and generated events from durable checkpoints", () => {
    usePaperBuilderStore.getState().startInitialGeneration({
      paperId: "paper-1", jobId: "job-1", outboxId: "job-1", status: "queued",
    });
    const slot = usePaperBuilderStore.getState().document.sections[0].slots[0];
    const snapshot: PaperRealtimeState = {
      paperId: "paper-1",
      runId: "run-1",
      status: "completed",
      versionId: "version-1",
      sequence: 9,
      filledSlots: 1,
      totalSlots: 1,
      slots: [{
        slotId: slot.slotId,
        sectionId: "A",
        position: 1,
        status: "completed",
        attemptCount: 1,
        question: { id: "question-1", stem: "Recovered while disconnected", marks: 2 },
        error: null,
      }],
    };

    usePaperBuilderStore.getState().reconcileGeneration(snapshot);
    const document = usePaperBuilderStore.getState().document;
    assert.equal(document.status, "ready");
    assert.equal(document.revisionId, "version-1");
    assert.equal(document.sections[0].slots[0].question?.stem, "Recovered while disconnected");
    assert.equal(document.generation.lastSequence, 9);
  });

  it("keeps a locked question byte-identical during snapshot reconciliation", () => {
    usePaperBuilderStore.getState().startInitialGeneration({
      paperId: "paper-1", jobId: "job-1", outboxId: "job-1", status: "queued",
    });
    const first = usePaperBuilderStore.getState().document.sections[0].slots[0];
    usePaperBuilderStore.getState().mergeExternalSlotPatch({
      sequence: 1,
      sectionId: "A",
      slotId: first.slotId,
      patch: { state: "ready", questionId: "old", question: { id: "old", stem: "Keep me" } },
    });
    usePaperBuilderStore.getState().toggleSlotLock("A", first.slotId, "user-1");
    const before = JSON.stringify(usePaperBuilderStore.getState().document.sections[0].slots[0]);
    usePaperBuilderStore.getState().reconcileGeneration({
      paperId: "paper-1", runId: "run-1", status: "completed", versionId: "version-2",
      sequence: 8, filledSlots: 1, totalSlots: 1,
      slots: [{ slotId: first.slotId, sectionId: "A", position: 1, status: "completed", attemptCount: 1,
        question: { id: "new", stem: "Replace me" }, error: null }],
    });
    const after = usePaperBuilderStore.getState().document.sections[0].slots[0];
    assert.equal(after.question?.stem, "Keep me");
    assert.equal(JSON.parse(before).question.stem, after.question?.stem);
  });
});
