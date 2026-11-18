import assert from "node:assert/strict";
import { beforeEach, describe, it } from "node:test";
import { usePaperBuilderStore } from "./store";
import { coverageSubmissionGate } from "./document";

describe("paper builder store invariants", () => {
  beforeEach(() => usePaperBuilderStore.getState().reset());

  it("starts initial generation and adopts the durable run from SSE progress", () => {
    usePaperBuilderStore.getState().startInitialGeneration({
      paperId: "paper-1",
      jobId: "outbox-1",
      outboxId: "outbox-1",
      status: "queued",
    });

    let document = usePaperBuilderStore.getState().document;
    assert.equal(document.paperId, "paper-1");
    assert.equal(document.status, "generating");
    assert.equal(document.generation.jobId, "outbox-1");
    assert.equal(document.generation.runId, null);
    assert.ok(document.sections.flatMap((section) => section.slots).every((slot) => slot.state === "generating"));

    usePaperBuilderStore.getState().mergeGenerationProgress({
      sequence: 1,
      runId: "run-1",
      filledSlots: 0,
      totalSlots: 5,
      status: "running",
    });
    document = usePaperBuilderStore.getState().document;
    assert.equal(document.generation.runId, "run-1");

    usePaperBuilderStore.getState().finishGeneration(2, "run-1", "revision-1");
    document = usePaperBuilderStore.getState().document;
    assert.equal(document.status, "ready");
    assert.equal(document.revisionId, "revision-1");
  });

  it("surfaces a terminal generation failure and releases untouched cards for retry", () => {
    usePaperBuilderStore.getState().startInitialGeneration({
      paperId: "paper-failed",
      jobId: "outbox-failed",
      outboxId: "outbox-failed",
      status: "queued",
    });

    usePaperBuilderStore.getState().failGeneration(
      4,
      "run-failed",
      "A",
      "A:2",
      "Question generation stopped after all retry attempts. Retry generation.",
    );

    const document = usePaperBuilderStore.getState().document;
    assert.equal(document.status, "failed");
    assert.equal(document.generation.runId, "run-failed");
    assert.equal(document.generation.lastSequence, 4);
    assert.equal(document.sections[0].slots[1].state, "failed");
    assert.match(document.sections[0].slots[1].error ?? "", /Retry generation/);
    assert.ok(document.sections[0].slots.filter((slot) => slot.slotId !== "A:2").every((slot) => slot.state === "empty"));
  });

  it("keeps stable, sparse, section-qualified slot addresses while sections change", () => {
    const initial = usePaperBuilderStore.getState().document;
    assert.deepEqual(initial.sections[0].slots.map(({ slotId, order }) => ({ slotId, order })), [
      { slotId: "A:1", order: 1000 },
      { slotId: "A:2", order: 2000 },
      { slotId: "A:3", order: 3000 },
      { slotId: "A:4", order: 4000 },
      { slotId: "A:5", order: 5000 },
    ]);

    usePaperBuilderStore.getState().addSection();
    let document = usePaperBuilderStore.getState().document;
    assert.deepEqual(document.sections.map(({ sectionId, order }) => ({ sectionId, order })), [
      { sectionId: "A", order: 1000 },
      { sectionId: "B", order: 2000 },
    ]);
    assert.deepEqual(document.sections[1].slots.map((slot) => slot.slotId), ["B:1", "B:2", "B:3", "B:4", "B:5"]);

    usePaperBuilderStore.getState().updateSection("A", { questionCount: 3 });
    document = usePaperBuilderStore.getState().document;
    assert.deepEqual(document.sections[0].slots.map(({ slotId, order }) => ({ slotId, order })), [
      { slotId: "A:1", order: 1000 },
      { slotId: "A:2", order: 2000 },
      { slotId: "A:3", order: 3000 },
    ]);
    assert.deepEqual(document.sections[1].slots.map((slot) => slot.slotId), ["B:1", "B:2", "B:3", "B:4", "B:5"]);

    usePaperBuilderStore.getState().removeSection("A");
    usePaperBuilderStore.getState().addSection();
    document = usePaperBuilderStore.getState().document;
    assert.deepEqual(document.sections.map((section) => section.sectionId), ["B", "A"]);
    assert.equal(new Set(document.sections.flatMap((section) => section.slots.map((slot) => slot.slotId))).size, 10);
  });

  it("keeps displayed question numbers derived from sequence across add, reorder, and remove", () => {
    usePaperBuilderStore.getState().updateSection("A", { questionCount: 6 });
    const before = usePaperBuilderStore.getState().document.sections[0].slots;
    const untouchedOrders = new Map(before.slice(1).map((slot) => [slot.slotId, slot.order]));

    const result = usePaperBuilderStore.getState().moveSlot("A", "A:1", "A", 3);
    assert.equal(result.moved, true);

    let section = usePaperBuilderStore.getState().document.sections[0];
    assert.deepEqual(section.slots.map((slot, index) => ({ slotId: slot.slotId, displayNumber: index + 1 })), [
      { slotId: "A:2", displayNumber: 1 },
      { slotId: "A:3", displayNumber: 2 },
      { slotId: "A:4", displayNumber: 3 },
      { slotId: "A:1", displayNumber: 4 },
      { slotId: "A:5", displayNumber: 5 },
      { slotId: "A:6", displayNumber: 6 },
    ]);
    assert.equal(section.slots.find((slot) => slot.slotId === "A:1")?.order, 4500);
    for (const [slotId, order] of untouchedOrders) {
      assert.equal(section.slots.find((slot) => slot.slotId === slotId)?.order, order);
    }
    assert.deepEqual(usePaperBuilderStore.getState().document.meta.dirtyPaths, ["blueprint.sections.A", "sections.A.slots.A:1.order"]);

    usePaperBuilderStore.getState().updateSection("A", { questionCount: 7 });
    section = usePaperBuilderStore.getState().document.sections[0];
    assert.equal(section.slots.at(-1)?.slotId, "A:7");
    assert.equal(section.slots.at(-1)?.order, 7000);
    usePaperBuilderStore.getState().updateSection("A", { questionCount: 6 });
    section = usePaperBuilderStore.getState().document.sections[0];
    assert.deepEqual(section.slots.map((slot, index) => ({ slotId: slot.slotId, displayNumber: index + 1 })), [
      { slotId: "A:2", displayNumber: 1 },
      { slotId: "A:3", displayNumber: 2 },
      { slotId: "A:4", displayNumber: 3 },
      { slotId: "A:1", displayNumber: 4 },
      { slotId: "A:5", displayNumber: 5 },
      { slotId: "A:6", displayNumber: 6 },
    ]);
    assert.equal(section.slots.find((slot) => slot.slotId === "A:1")?.order, 4500);
  });

  it("moves compatible slots across sections without changing their stable address", () => {
    usePaperBuilderStore.getState().addSection();
    usePaperBuilderStore.getState().updateSection("B", { questionCount: 2, marksPerQuestion: 3 });

    const result = usePaperBuilderStore.getState().moveSlot("A", "A:1", "B", 1);
    assert.equal(result.moved, true);
    assert.equal(result.warning, "Question marks changed to 3 to match Section B.");

    const document = usePaperBuilderStore.getState().document;
    assert.deepEqual(document.sections.find((section) => section.sectionId === "A")?.slots.map((slot) => slot.slotId), ["A:2", "A:3", "A:4", "A:5"]);
    const destination = document.sections.find((section) => section.sectionId === "B")!;
    assert.deepEqual(destination.slots.map((slot) => slot.slotId), ["B:1", "A:1", "B:2"]);
    assert.equal(destination.slots[1].order, 1500);
    assert.equal(destination.slots[1].marks, 3);
    assert.equal(document.blueprint.sections.find((section) => section.id === "A")?.questionCount, 4);
    assert.equal(document.blueprint.sections.find((section) => section.id === "B")?.questionCount, 3);
    assert.deepEqual(document.meta.dirtyPaths.slice(-4), [
      "sections.A.slots",
      "sections.B.slots",
      "blueprint.sections.A.questionCount",
      "blueprint.sections.B.questionCount",
    ]);
  });

  it("refuses an incompatible cross-section move without changing either section", () => {
    usePaperBuilderStore.getState().addSection();
    usePaperBuilderStore.getState().updateSection("B", { allowedQuestionTypes: ["mcq"] });

    const result = usePaperBuilderStore.getState().moveSlot("A", "A:1", "B", 0);

    assert.equal(result.moved, false);
    assert.equal(result.reason, "Section B does not accept this question type.");
    const document = usePaperBuilderStore.getState().document;
    assert.deepEqual(document.sections.find((section) => section.sectionId === "A")?.slots.map((slot) => slot.slotId), ["A:1", "A:2", "A:3", "A:4", "A:5"]);
    assert.deepEqual(document.sections.find((section) => section.sectionId === "B")?.slots.map((slot) => slot.slotId), ["B:1", "B:2", "B:3", "B:4", "B:5"]);
  });

  it("deduplicates dirty paths and records the smallest edited subtree", () => {
    const store = usePaperBuilderStore.getState();
    store.renameSection("A", "Foundations");
    usePaperBuilderStore.getState().renameSection("A", "Foundations and applications");
    usePaperBuilderStore.getState().updateSection("A", { questionCount: 4 });

    assert.deepEqual(usePaperBuilderStore.getState().document.meta.dirtyPaths, [
      "sections.A.title",
      "blueprint.sections.A",
    ]);
    assert.equal(usePaperBuilderStore.getState().document.meta.saveState, "dirty");
  });

  it("merges an ordered external slot patch when another subtree is dirty", () => {
    usePaperBuilderStore.getState().markDirty("sections.A.slots.A:2.question");
    usePaperBuilderStore.getState().mergeExternalSlotPatch({
      sequence: 7,
      sectionId: "A",
      slotId: "A:1",
      patch: { state: "ready", questionId: "question-1", question: { stem: "Server question" } },
    });

    const document = usePaperBuilderStore.getState().document;
    assert.equal(document.sections[0].slots[0].state, "ready");
    assert.equal(document.sections[0].slots[0].questionId, "question-1");
    assert.equal(document.generation.lastSequence, 7);
    assert.equal(document.generation.filledSlots, 1);
  });

  it("advances the SSE checkpoint but preserves a locally dirty slot", () => {
    usePaperBuilderStore.getState().markDirty("sections.A.slots.A:1.question");
    usePaperBuilderStore.getState().mergeExternalSlotPatch({
      sequence: 8,
      sectionId: "A",
      slotId: "A:1",
      patch: { state: "ready", questionId: "server-question", question: { stem: "Do not overwrite" } },
    });

    let document = usePaperBuilderStore.getState().document;
    assert.equal(document.sections[0].slots[0].state, "empty");
    assert.equal(document.sections[0].slots[0].questionId, null);
    assert.equal(document.generation.lastSequence, 8);

    usePaperBuilderStore.getState().mergeExternalSlotPatch({
      sequence: 8,
      sectionId: "A",
      slotId: "A:2",
      patch: { state: "ready", questionId: "duplicate-event" },
    });
    document = usePaperBuilderStore.getState().document;
    assert.equal(document.sections[0].slots[1].state, "empty");
  });

  it("keeps a locked slot byte-identical when section regeneration completes around it", () => {
    usePaperBuilderStore.getState().mergeExternalSlotPatch({
      sequence: 1,
      sectionId: "A",
      slotId: "A:1",
      patch: {
        state: "ready",
        questionId: "locked-question",
        question: { stem: "Keep this exact question", answer: { text: "Exact answer" } },
      },
    });
    usePaperBuilderStore.getState().mergeExternalSlotPatch({
      sequence: 2,
      sectionId: "A",
      slotId: "A:2",
      patch: { state: "ready", questionId: "old-question", question: { stem: "Replace me" } },
    });
    usePaperBuilderStore.getState().toggleSlotLock("A", "A:1", "teacher-1");
    const before = JSON.stringify(usePaperBuilderStore.getState().document.sections[0].slots[0]);

    usePaperBuilderStore.getState().startRegeneration({
      jobId: "section-job",
      runId: "section-run",
      paperId: "paper-1",
      sourceVersionId: "version-1",
      targetSlotIds: ["A:2", "A:3", "A:4", "A:5"],
      lockedSlotIds: ["A:1"],
      status: "running",
    });
    usePaperBuilderStore.getState().mergeExternalSlotPatch({
      sequence: 3,
      sectionId: "A",
      slotId: "A:2",
      patch: { state: "ready", questionId: "new-question", question: { stem: "New assignment" } },
    });

    const document = usePaperBuilderStore.getState().document;
    assert.equal(JSON.stringify(document.sections[0].slots[0]), before);
    assert.equal(document.sections[0].slots[1].questionId, "new-question");
  });

  it("rejects even a newer whole-paper SSE patch for a locked slot", () => {
    usePaperBuilderStore.getState().mergeExternalSlotPatch({
      sequence: 1,
      sectionId: "A",
      slotId: "A:1",
      patch: { state: "ready", questionId: "locked-question", question: { stem: "Immutable" } },
    });
    usePaperBuilderStore.getState().toggleSlotLock("A", "A:1", "teacher-1");
    const before = JSON.stringify(usePaperBuilderStore.getState().document.sections[0].slots[0]);

    usePaperBuilderStore.getState().mergeExternalSlotPatch({
      sequence: 99,
      sectionId: "A",
      slotId: "A:1",
      patch: { state: "ready", questionId: "unexpected", question: { stem: "Must not land" } },
    });

    const document = usePaperBuilderStore.getState().document;
    assert.equal(JSON.stringify(document.sections[0].slots[0]), before);
    assert.equal(document.generation.lastSequence, 99);
  });

  it("undoes and redoes reorder, lock, and section-title edits in order", () => {
    const store = usePaperBuilderStore.getState();
    const originalOrder = store.document.sections[0].slots.map((slot) => slot.slotId);
    store.moveSlot("A", "A:1", "A", 3);
    usePaperBuilderStore.getState().toggleSlotLock("A", "A:1", "teacher-1");
    usePaperBuilderStore.getState().renameSection("A", "Edited section");

    let state = usePaperBuilderStore.getState();
    assert.deepEqual(state.document.sections[0].slots.map((slot) => slot.slotId), ["A:2", "A:3", "A:4", "A:1", "A:5"]);
    assert.equal(state.document.sections[0].slots[3].locked, true);
    assert.equal(state.document.sections[0].title, "Edited section");

    state.undo();
    state = usePaperBuilderStore.getState();
    assert.equal(state.document.sections[0].title, "Section A");
    state.undo();
    state = usePaperBuilderStore.getState();
    assert.equal(state.document.sections[0].slots[3].locked, false);
    state.undo();
    state = usePaperBuilderStore.getState();
    assert.deepEqual(state.document.sections[0].slots.map((slot) => slot.slotId), originalOrder);

    state.redo();
    state.redo();
    state.redo();
    state = usePaperBuilderStore.getState();
    assert.deepEqual(state.document.sections[0].slots.map((slot) => slot.slotId), ["A:2", "A:3", "A:4", "A:1", "A:5"]);
    assert.equal(state.document.sections[0].slots[3].locked, true);
    assert.equal(state.document.sections[0].title, "Edited section");
    assert.equal(state.document.meta.lastEditKind, "redo");
  });

  it("never puts server-driven slot patches in history or removes them during undo", () => {
    usePaperBuilderStore.getState().renameSection("A", "User title");
    const historyLength = usePaperBuilderStore.getState().past.length;
    usePaperBuilderStore.getState().mergeExternalSlotPatch({
      sequence: 1,
      sectionId: "A",
      slotId: "A:1",
      patch: { state: "ready", questionId: "server-question", question: { stem: "Server result" } },
    });
    assert.equal(usePaperBuilderStore.getState().past.length, historyLength);

    usePaperBuilderStore.getState().undo();
    const state = usePaperBuilderStore.getState();
    assert.equal(state.document.sections[0].title, "Section A");
    assert.equal(state.document.sections[0].slots[0].questionId, "server-question");
    assert.equal(state.document.sections[0].slots[0].state, "ready");
  });

  it("does not let a slow autosave acknowledgement mark newer edits clean", () => {
    usePaperBuilderStore.getState().initializeDraft("draft-1");
    usePaperBuilderStore.getState().updatePaper({ title: "First edit" });
    const firstSequence = usePaperBuilderStore.getState().persistence.editSequence;
    usePaperBuilderStore.getState().beginSave(firstSequence);
    usePaperBuilderStore.getState().updatePaper({ title: "Newer edit" });
    usePaperBuilderStore.getState().acknowledgeSave({
      draftId: "draft-1",
      paperId: null,
      revision: 1,
      document: {},
      updatedAt: "2026-09-25T10:00:00Z",
    }, firstSequence);

    let state = usePaperBuilderStore.getState();
    assert.equal(state.document.title, "Newer edit");
    assert.equal(state.document.meta.saveState, "dirty");
    const secondSequence = state.persistence.editSequence;
    state.acknowledgeSave({
      draftId: "draft-1",
      paperId: null,
      revision: 2,
      document: {},
      updatedAt: "2026-09-25T10:00:01Z",
    }, secondSequence);
    state = usePaperBuilderStore.getState();
    assert.equal(state.document.meta.saveState, "clean");
    assert.equal(state.persistence.revision, 2);
  });

  it("blocks structural undo while generated slots are in flight", () => {
    usePaperBuilderStore.getState().updateSection("A", { questionCount: 6 });
    usePaperBuilderStore.getState().startInitialGeneration({
      paperId: "paper-generating",
      jobId: "job-generating",
      outboxId: "job-generating",
      status: "queued",
    });
    const before = usePaperBuilderStore.getState().document.sections[0].slots.length;
    usePaperBuilderStore.getState().undo();
    const state = usePaperBuilderStore.getState();
    assert.equal(before, 6);
    assert.equal(state.document.sections[0].slots.length, 6);
    assert.equal(state.past.length, 1);
  });

  it("gates later submission on the exact generated version's accepted solver report", () => {
    const store = usePaperBuilderStore.getState();
    store.startInitialGeneration({
      paperId: "paper-coverage",
      jobId: "job-coverage",
      outboxId: "job-coverage",
      status: "queued",
    });
    for (let index = 1; index <= 5; index += 1) {
      usePaperBuilderStore.getState().mergeExternalSlotPatch({
        sequence: index,
        sectionId: "A",
        slotId: `A:${index}`,
        patch: { state: "ready", questionId: `q-${index}` },
      });
    }
    usePaperBuilderStore.getState().finishGeneration(6, "run-coverage", "version-coverage");
    const document = usePaperBuilderStore.getState().document;
    const report = {
      paperId: "paper-coverage",
      versionId: "version-coverage",
      solverOutcome: "feasible-with-compromise",
      source: "persisted-final-exact-selection" as const,
      coverage: [],
      compromises: [],
    };

    assert.equal(coverageSubmissionGate(document, report, null).ready, false);
    assert.equal(coverageSubmissionGate(document, report, "version-coverage").ready, true);
    assert.equal(
      coverageSubmissionGate(document, { ...report, versionId: "stale-version" }, "version-coverage").ready,
      false,
    );
  });
});
