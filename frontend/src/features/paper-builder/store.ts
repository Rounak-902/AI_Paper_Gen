import { create } from "zustand";
import {
  applyExternalSlotPatch,
  createBlueprintSection,
  createInitialPaperDraft,
  movePaperSlot,
  nextSectionId,
  pathsOverlap,
  syncDocumentSections,
  type SlotMoveResult,
} from "./document";
import type {
  BlueprintSection,
  ExternalSlotPatch,
  GenerationProgressPatch,
  GenerationAccepted,
  PaperBlueprint,
  PaperDraft,
  PaperDraftResource,
  PaperRealtimeState,
  RegenerationAccepted,
} from "./types";

const HISTORY_LIMIT = 50;

type PersistencePhase = "idle" | "loading" | "ready" | "saving" | "error" | "conflict";

type PaperBuilderState = {
  document: PaperDraft;
  past: PaperDraft[];
  future: PaperDraft[];
  persistence: {
    draftId: string | null;
    revision: number | null;
    phase: PersistencePhase;
    editSequence: number;
  };
  reset: () => void;
  initializeDraft: (draftId: string) => void;
  setDraftLoading: (draftId: string) => void;
  hydrateDraft: (resource: PaperDraftResource) => void;
  beginSave: (sequence: number) => void;
  acknowledgeSave: (resource: PaperDraftResource, sequence: number) => void;
  failSave: (message: string, conflict: boolean) => void;
  undo: () => void;
  redo: () => void;
  markDirty: (path: string) => void;
  mergeExternalSlotPatch: (patch: ExternalSlotPatch) => void;
  mergeGenerationProgress: (patch: GenerationProgressPatch) => void;
  reconcileGeneration: (snapshot: PaperRealtimeState) => void;
  failGeneration: (sequence: number, runId: string, sectionId: string, slotId: string, message: string) => void;
  finishGeneration: (sequence: number, runId: string, revisionId: string | null) => void;
  startInitialGeneration: (accepted: GenerationAccepted) => void;
  toggleSlotLock: (sectionId: string, slotId: string, ownerId: string | null) => void;
  startRegeneration: (accepted: RegenerationAccepted) => void;
  updatePaper: (patch: Partial<Pick<PaperDraft, "title" | "durationMinutes" | "instructions">>) => void;
  updateBlueprint: (patch: Partial<PaperBlueprint>, path?: string) => void;
  addSection: () => void;
  updateSection: (sectionId: string, patch: Partial<BlueprintSection>) => void;
  renameSection: (sectionId: string, title: string) => void;
  removeSection: (sectionId: string) => void;
  moveSlot: (sourceSectionId: string, slotId: string, targetSectionId: string, targetIndex: number) => SlotMoveResult;
};

function dirty(
  document: PaperDraft,
  path: string,
  kind: PaperDraft["meta"]["lastEditKind"] = "edit",
): PaperDraft {
  return {
    ...document,
    meta: {
      ...document.meta,
      dirtyPaths: document.meta.dirtyPaths.includes(path)
        ? document.meta.dirtyPaths
        : [...document.meta.dirtyPaths, path],
      saveError: null,
      saveState: "dirty",
      lastEditKind: kind,
    },
  };
}

function dirtyMany(
  document: PaperDraft,
  paths: string[],
  kind: PaperDraft["meta"]["lastEditKind"] = "edit",
) {
  return paths.reduce((next, path) => dirty(next, path, kind), document);
}

function systemChanged(document: PaperDraft): PaperDraft {
  return {
    ...document,
    meta: {
      ...document.meta,
      saveError: null,
      saveState: "dirty",
      lastEditKind: "system",
    },
  };
}

function recordUserChange(
  state: PaperBuilderState,
  document: PaperDraft,
): Partial<PaperBuilderState> {
  return {
    document,
    future: [],
    past: [...state.past, state.document].slice(-HISTORY_LIMIT),
    persistence: {
      ...state.persistence,
      editSequence: state.persistence.editSequence + 1,
      phase: state.persistence.phase === "conflict" ? "conflict" : "ready",
    },
  };
}

function recordSystemChange(
  state: PaperBuilderState,
  document: PaperDraft,
): Partial<PaperBuilderState> {
  if (document === state.document) return {};
  return {
    document: systemChanged(document),
    persistence: {
      ...state.persistence,
      editSequence: state.persistence.editSequence + 1,
      phase: state.persistence.phase === "conflict" ? "conflict" : "ready",
    },
  };
}

function normalizeHydratedDocument(resource: PaperDraftResource): PaperDraft {
  const incoming = resource.document as unknown as PaperDraft;
  const fallback = createInitialPaperDraft();
  if (!incoming || !Array.isArray(incoming.sections) || !incoming.blueprint) return fallback;
  return {
    ...incoming,
    meta: {
      dirtyPaths: [],
      lastSavedAt: resource.updatedAt,
      saveError: null,
      saveState: "clean",
      lastEditKind: null,
    },
  };
}

function restoreUserSnapshot(current: PaperDraft, target: PaperDraft, kind: "undo" | "redo"): PaperDraft {
  const currentSlots = new Map(
    current.sections.flatMap((section) => section.slots.map((slot) => [slot.slotId, slot] as const)),
  );
  const sections = target.sections.map((section) => ({
    ...section,
    slots: section.slots.map((snapshotSlot) => {
      const liveSlot = currentSlots.get(snapshotSlot.slotId);
      if (!liveSlot) return snapshotSlot;
      return {
        ...snapshotSlot,
        state: liveSlot.state,
        origin: liveSlot.origin,
        questionId: liveSlot.questionId,
        question: liveSlot.question,
        difficulty: liveSlot.difficulty,
        bloomLevel: liveSlot.bloomLevel,
        chapterId: liveSlot.chapterId,
        topicId: liveSlot.topicId,
        error: liveSlot.error,
      };
    }),
  }));

  return dirtyMany(
    {
      ...current,
      title: target.title,
      durationMinutes: target.durationMinutes,
      instructions: target.instructions,
      blueprint: target.blueprint,
      sections,
    },
    ["paper", "blueprint", "sections"],
    kind,
  );
}

export function canRestoreUserSnapshot(current: PaperDraft, target: PaperDraft | undefined) {
  if (!target) return false;
  if (current.status !== "generating") return true;
  const shape = (document: PaperDraft) => document.sections
    .map((section) => `${section.sectionId}:${section.slots.map((slot) => slot.slotId).slice().sort().join(",")}`)
    .sort()
    .join("|");
  return shape(current) === shape(target);
}

const initialPersistence = () => ({
  draftId: null,
  revision: null,
  phase: "idle" as const,
  editSequence: 0,
});

export const usePaperBuilderStore = create<PaperBuilderState>((set) => ({
  document: createInitialPaperDraft(),
  past: [],
  future: [],
  persistence: initialPersistence(),
  reset: () => set({
    document: createInitialPaperDraft(),
    past: [],
    future: [],
    persistence: initialPersistence(),
  }),
  initializeDraft: (draftId) => set({
    document: systemChanged(createInitialPaperDraft()),
    past: [],
    future: [],
    persistence: { draftId, revision: null, phase: "ready", editSequence: 1 },
  }),
  setDraftLoading: (draftId) => set((state) => ({
    persistence: { ...state.persistence, draftId, phase: "loading" },
  })),
  hydrateDraft: (resource) => set({
    document: normalizeHydratedDocument(resource),
    past: [],
    future: [],
    persistence: {
      draftId: resource.draftId,
      revision: resource.revision,
      phase: "ready",
      editSequence: 0,
    },
  }),
  beginSave: (sequence) => set((state) => ({
    document: sequence === state.persistence.editSequence
      ? { ...state.document, meta: { ...state.document.meta, saveState: "saving", saveError: null } }
      : state.document,
    persistence: { ...state.persistence, phase: "saving" },
  })),
  acknowledgeSave: (resource, sequence) => set((state) => {
    const isCurrent = sequence === state.persistence.editSequence;
    return {
      document: {
        ...state.document,
        meta: {
          ...state.document.meta,
          dirtyPaths: isCurrent ? [] : state.document.meta.dirtyPaths,
          lastSavedAt: resource.updatedAt,
          saveError: null,
          saveState: isCurrent ? "clean" : "dirty",
        },
      },
      persistence: {
        ...state.persistence,
        draftId: resource.draftId,
        revision: resource.revision,
        phase: "ready",
      },
    };
  }),
  failSave: (message, conflict) => set((state) => ({
    document: {
      ...state.document,
      meta: { ...state.document.meta, saveError: message, saveState: "error" },
    },
    persistence: { ...state.persistence, phase: conflict ? "conflict" : "error" },
  })),
  undo: () => set((state) => {
    const target = state.past.at(-1);
    if (!target || !canRestoreUserSnapshot(state.document, target)) return {};
    return {
      document: restoreUserSnapshot(state.document, target, "undo"),
      past: state.past.slice(0, -1),
      future: [state.document, ...state.future].slice(0, HISTORY_LIMIT),
      persistence: {
        ...state.persistence,
        editSequence: state.persistence.editSequence + 1,
        phase: state.persistence.phase === "conflict" ? "conflict" : "ready",
      },
    };
  }),
  redo: () => set((state) => {
    const [target, ...future] = state.future;
    if (!target || !canRestoreUserSnapshot(state.document, target)) return {};
    return {
      document: restoreUserSnapshot(state.document, target, "redo"),
      past: [...state.past, state.document].slice(-HISTORY_LIMIT),
      future,
      persistence: {
        ...state.persistence,
        editSequence: state.persistence.editSequence + 1,
        phase: state.persistence.phase === "conflict" ? "conflict" : "ready",
      },
    };
  }),
  markDirty: (path) => set((state) => ({
    document: dirty(state.document, path),
    persistence: { ...state.persistence, editSequence: state.persistence.editSequence + 1 },
  })),
  mergeExternalSlotPatch: (patch) => set((state) =>
    recordSystemChange(state, applyExternalSlotPatch(state.document, patch))),
  mergeGenerationProgress: (patch) => set((state) => {
    if (patch.sequence <= state.document.generation.lastSequence) return {};
    if (state.document.generation.runId && patch.runId !== state.document.generation.runId) return {};
    return recordSystemChange(state, {
      ...state.document,
      status: patch.status === "failed" ? "failed" : state.document.status,
      generation: {
        ...state.document.generation,
        runId: state.document.generation.runId ?? patch.runId,
        lastSequence: patch.sequence,
        filledSlots: patch.filledSlots,
        totalSlots: patch.totalSlots,
      },
    });
  }),
  reconcileGeneration: (snapshot) => set((state) => {
    if (state.document.paperId && snapshot.paperId !== state.document.paperId) return {};
    if (state.document.generation.runId && snapshot.runId && snapshot.runId !== state.document.generation.runId) return {};

    const checkpoints = new Map(snapshot.slots.map((slot) => [slot.slotId, slot]));
    const sections = state.document.sections.map((section) => ({
      ...section,
      slots: section.slots.map((slot) => {
        const checkpoint = checkpoints.get(slot.slotId);
        if (!checkpoint || slot.locked) return slot;
        const targetPath = `sections.${section.sectionId}.slots.${slot.slotId}`;
        if (state.document.meta.dirtyPaths.some((path) => pathsOverlap(path, targetPath))) return slot;
        if (checkpoint.status === "failed") {
          return { ...slot, state: "failed" as const, error: checkpoint.error ?? "Question generation failed. Retry generation." };
        }
        if (checkpoint.status !== "completed" || !checkpoint.question) {
          return { ...slot, state: "generating" as const, error: null };
        }
        const question = checkpoint.question;
        const source = typeof question.source === "string" ? question.source : null;
        return {
          ...slot,
          state: "ready" as const,
          origin: source === "ai_generated" ? "ai_generated" as const : "bank" as const,
          questionId: typeof question.id === "string" ? question.id : slot.questionId,
          question,
          marks: Number.isFinite(Number(question.marks)) ? Number(question.marks) : slot.marks,
          difficulty: typeof question.difficulty === "string" ? question.difficulty : slot.difficulty,
          bloomLevel: typeof question.bloom === "string" ? question.bloom : slot.bloomLevel,
          chapterId: typeof (question.chapterId ?? question.chapter_id) === "string"
            ? String(question.chapterId ?? question.chapter_id)
            : slot.chapterId,
          topicId: typeof (question.topicId ?? question.topic_id) === "string"
            ? String(question.topicId ?? question.topic_id)
            : slot.topicId,
          error: null,
        };
      }),
    }));
    const failed = snapshot.status === "failed" || snapshot.slots.some((slot) => slot.status === "failed");
    const completed = snapshot.status === "completed" && Boolean(snapshot.versionId);
    const revisionChanged = Boolean(snapshot.versionId && snapshot.versionId !== state.document.revisionId);
    return recordSystemChange(state, {
      ...state.document,
      status: failed ? "failed" : completed ? "ready" : "generating",
      revisionId: snapshot.versionId ?? state.document.revisionId,
      version: revisionChanged ? state.document.version + 1 : state.document.version,
      sections,
      generation: {
        ...state.document.generation,
        runId: snapshot.runId ?? state.document.generation.runId,
        lastSequence: Math.max(state.document.generation.lastSequence, snapshot.sequence),
        filledSlots: snapshot.filledSlots,
        totalSlots: snapshot.totalSlots || state.document.generation.totalSlots,
      },
    });
  }),
  failGeneration: (sequence, runId, sectionId, slotId, message) => set((state) => {
    if (sequence <= state.document.generation.lastSequence) return {};
    if (state.document.generation.runId && runId !== state.document.generation.runId) return {};
    const patched = applyExternalSlotPatch(state.document, {
      sequence,
      sectionId,
      slotId,
      patch: { state: "failed", error: message },
    });
    return recordSystemChange(state, {
      ...patched,
      status: "failed",
      sections: patched.sections.map((section) => ({
        ...section,
        slots: section.slots.map((slot) =>
          slot.slotId !== slotId && slot.state === "generating"
            ? { ...slot, state: slot.questionId ? "ready" : "empty" }
            : slot,
        ),
      })),
      generation: { ...patched.generation, runId: patched.generation.runId ?? runId },
    });
  }),
  finishGeneration: (sequence, runId, revisionId) => set((state) => {
    if (sequence <= state.document.generation.lastSequence) return {};
    if (state.document.generation.runId && runId !== state.document.generation.runId) return {};
    return recordSystemChange(state, {
      ...state.document,
      status: "ready",
      revisionId: revisionId ?? state.document.revisionId,
      version: revisionId && revisionId !== state.document.revisionId
        ? state.document.version + 1
        : state.document.version,
      generation: {
        ...state.document.generation,
        runId: state.document.generation.runId ?? runId,
        lastSequence: sequence,
        filledSlots: state.document.sections.reduce(
          (count, section) => count + section.slots.filter((slot) => slot.state === "ready").length,
          0,
        ),
      },
    });
  }),
  toggleSlotLock: (sectionId, slotId, ownerId) => set((state) => {
    let changed = false;
    const sections = state.document.sections.map((section) => {
      if (section.sectionId !== sectionId) return section;
      return {
        ...section,
        slots: section.slots.map((slot) => {
          if (slot.slotId !== slotId) return slot;
          changed = true;
          const locked = !slot.locked;
          return { ...slot, locked, lockOwnerId: locked ? ownerId : null };
        }),
      };
    });
    if (!changed) return {};
    return recordUserChange(
      state,
      dirty(
        { ...state.document, sections },
        `sections.${sectionId}.slots.${slotId}.locked`,
      ),
    );
  }),
  startRegeneration: (accepted) => set((state) => {
    const targets = new Set(accepted.targetSlotIds);
    return recordSystemChange(state, {
      ...state.document,
      status: "generating",
      sections: state.document.sections.map((section) => ({
        ...section,
        slots: section.slots.map((slot) =>
          targets.has(slot.slotId) && !slot.locked
            ? { ...slot, state: "generating", error: null }
            : slot,
        ),
      })),
      generation: {
        ...state.document.generation,
        jobId: accepted.jobId,
        runId: accepted.runId,
        filledSlots: state.document.sections.reduce(
          (count, section) => count + section.slots.filter((slot) => !targets.has(slot.slotId)).length,
          0,
        ),
      },
    });
  }),
  startInitialGeneration: (accepted) => set((state) => recordSystemChange(state, {
    ...state.document,
    paperId: accepted.paperId,
    revisionId: null,
    status: "generating",
    sections: state.document.sections.map((section) => ({
      ...section,
      slots: section.slots.map((slot) => ({ ...slot, state: "generating", error: null })),
    })),
    generation: {
      jobId: accepted.jobId,
      runId: null,
      lastSequence: 0,
      filledSlots: 0,
      totalSlots: state.document.sections.reduce((count, section) => count + section.slots.length, 0),
    },
  })),
  updatePaper: (patch) => set((state) => recordUserChange(
    state,
    dirty({ ...state.document, ...patch }, "paper"),
  )),
  updateBlueprint: (patch, path = "blueprint") => set((state) => {
    const blueprint = { ...state.document.blueprint, ...patch };
    const sections = patch.sections
      ? syncDocumentSections(state.document.sections, blueprint.sections)
      : state.document.sections;
    return recordUserChange(state, dirty({
      ...state.document,
      blueprint,
      sections,
      generation: {
        ...state.document.generation,
        totalSlots: blueprint.sections.reduce((sum, item) => sum + item.questionCount, 0),
      },
    }, path));
  }),
  addSection: () => set((state) => {
    const id = nextSectionId(state.document.blueprint.sections);
    const previous = state.document.blueprint.sections.at(-1);
    const section = {
      ...createBlueprintSection(id),
      marksPerQuestion: previous?.marksPerQuestion ?? 2,
      allowedQuestionTypes: previous?.allowedQuestionTypes ?? ["short_answer"],
      chapterIds: previous?.chapterIds ?? state.document.blueprint.chapterIds,
    };
    const blueprint = {
      ...state.document.blueprint,
      sections: [...state.document.blueprint.sections, section],
    };
    return recordUserChange(state, dirty(
      { ...state.document, blueprint, sections: syncDocumentSections(state.document.sections, blueprint.sections) },
      `blueprint.sections.${id}`,
    ));
  }),
  updateSection: (sectionId, patch) => set((state) => {
    const sections = state.document.blueprint.sections.map((section) =>
      section.id === sectionId ? { ...section, ...patch } : section,
    );
    const blueprint = { ...state.document.blueprint, sections };
    return recordUserChange(state, dirty(
      { ...state.document, blueprint, sections: syncDocumentSections(state.document.sections, sections) },
      `blueprint.sections.${sectionId}`,
    ));
  }),
  renameSection: (sectionId, title) => set((state) => recordUserChange(state, dirty(
    {
      ...state.document,
      sections: state.document.sections.map((section) =>
        section.sectionId === sectionId ? { ...section, title } : section,
      ),
    },
    `sections.${sectionId}.title`,
  ))),
  removeSection: (sectionId) => set((state) => {
    if (state.document.blueprint.sections.length === 1) return {};
    const sections = state.document.blueprint.sections.filter((section) => section.id !== sectionId);
    const blueprint = { ...state.document.blueprint, sections };
    return recordUserChange(state, dirty(
      { ...state.document, blueprint, sections: syncDocumentSections(state.document.sections, sections) },
      `blueprint.sections.${sectionId}`,
    ));
  }),
  moveSlot: (sourceSectionId, slotId, targetSectionId, targetIndex) => {
    let result: SlotMoveResult | undefined;
    set((state) => {
      const move = movePaperSlot(state.document, { sourceSectionId, slotId, targetSectionId, targetIndex });
      if (!move.moved) {
        result = move;
        return {};
      }
      const document = dirtyMany(move.document, move.dirtyPaths, "reorder");
      result = { ...move, document };
      return recordUserChange(state, document);
    });
    return result!;
  },
}));

export type { PaperBuilderState };
