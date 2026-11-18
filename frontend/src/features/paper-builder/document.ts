import type { BlueprintSection, ExternalSlotPatch, PaperBlueprint, PaperCoverageReport, PaperDocumentSection, PaperDraft, PaperSlot } from "./types";

const EMPTY_ID = "";
const ORDER_STEP = 1000;

export type SlotMoveInput = {
  sourceSectionId: string;
  targetSectionId: string;
  slotId: string;
  /** The slot's final, zero-based position in the target section. */
  targetIndex: number;
};

export type SlotMoveResult = {
  document: PaperDraft;
  moved: boolean;
  dirtyPaths: string[];
  reason?: string;
  warning?: string;
};

export function createInitialBlueprint(): PaperBlueprint {
  return {
    boardId: EMPTY_ID,
    classId: EMPTY_ID,
    subjectId: EMPTY_ID,
    curriculumVersionId: EMPTY_ID,
    chapterIds: [],
    language: "en",
    totalMarks: 10,
    sections: [createBlueprintSection("A")],
    difficultyMix: mix({ easy: 30, medium: 50, hard: 20 }),
    sourceMix: mix({ textbook: 70, ai_generated: 30 }),
    bloomMix: mix({ remember: 30, understand: 40, apply: 30 }),
    chapterCoverage: [],
    learningOutcomeCoverage: [],
    generationMode: "mixed",
    textbookFloorPercent: 70,
  };
}

export function createBlueprintSection(id: string): BlueprintSection {
  return {
    id,
    questionCount: 5,
    marksPerQuestion: 2,
    allowedQuestionTypes: ["short_answer"],
    chapterIds: [],
  };
}

export function createInitialPaperDraft(): PaperDraft {
  const blueprint = createInitialBlueprint();
  return {
    paperId: null,
    version: 0,
    revisionId: null,
    status: "draft",
    title: "Untitled question paper",
    durationMinutes: 60,
    instructions: "Answer all questions. Show your working where applicable.",
    blueprint,
    sections: syncDocumentSections([], blueprint.sections),
    generation: {
      jobId: null,
      runId: null,
      lastSequence: 0,
      filledSlots: 0,
      totalSlots: blueprint.sections.reduce((sum, section) => sum + section.questionCount, 0),
    },
    meta: {
      dirtyPaths: [],
      lastSavedAt: null,
      saveState: "clean",
      saveError: null,
      lastEditKind: null,
    },
  };
}

export function syncDocumentSections(
  current: PaperDocumentSection[],
  specs: BlueprintSection[],
): PaperDocumentSection[] {
  const currentById = new Map(current.map((section) => [section.sectionId, section]));
  return specs.map((spec, sectionIndex) => {
    const existing = currentById.get(spec.id);
    return {
      sectionId: spec.id,
      order: (sectionIndex + 1) * 1000,
      title: existing?.title ?? `Section ${spec.id}`,
      slots: syncSlots(existing?.slots ?? [], spec),
    };
  });
}

function syncSlots(current: PaperSlot[], spec: BlueprintSection): PaperSlot[] {
  // A section edit must not erase a prior sparse reorder. `order` is the
  // persisted placement field; the array is its current presentation order.
  const retained = current.slice(0, spec.questionCount).map((slot) => ({
    ...slot,
    marks: Number(spec.marksPerQuestion),
  }));
  let nextSlotNumber = nextSlotNumberForSection(current, spec.id);

  while (retained.length < spec.questionCount) {
    retained.push({
      slotId: `${spec.id}:${nextSlotNumber++}`,
      order: nextOrderAfter(retained),
      state: "empty",
      origin: null,
      locked: false,
      lockOwnerId: null,
      questionId: null,
      question: null,
      marks: Number(spec.marksPerQuestion),
      difficulty: null,
      bloomLevel: null,
      chapterId: null,
      topicId: null,
      edited: false,
      error: null,
    });
  }

  return retained;
}

function nextSlotNumberForSection(slots: PaperSlot[], sectionId: string) {
  const prefix = `${sectionId}:`;
  const largest = slots.reduce((maximum, slot) => {
    if (!slot.slotId.startsWith(prefix)) return maximum;
    const value = Number(slot.slotId.slice(prefix.length));
    return Number.isSafeInteger(value) ? Math.max(maximum, value) : maximum;
  }, 0);
  return largest + 1;
}

function nextOrderAfter(slots: PaperSlot[]) {
  const lastOrder = slots.at(-1)?.order;
  return lastOrder === undefined ? ORDER_STEP : lastOrder + ORDER_STEP;
}

function sparseOrderAt(slots: PaperSlot[], index: number) {
  const before = slots[index - 1]?.order;
  const after = slots[index]?.order;
  if (before !== undefined && after !== undefined) return before + (after - before) / 2;
  if (before !== undefined) return before + ORDER_STEP;
  if (after !== undefined) return after - ORDER_STEP;
  return ORDER_STEP;
}

function questionTypesForSlot(slot: PaperSlot, section: BlueprintSection) {
  const question = slot.question;
  const qtype = question && typeof question.qtype === "string" ? question.qtype : null;
  return qtype ? [qtype] : section.allowedQuestionTypes;
}

/**
 * Moves one addressable slot without renumbering the other slots' sparse
 * addresses. The immutable `slotId` remains stable even when its parent
 * section changes; `sectionId` continues to identify the current parent.
 */
export function movePaperSlot(document: PaperDraft, input: SlotMoveInput): SlotMoveResult {
  const sourceSpec = document.blueprint.sections.find((section) => section.id === input.sourceSectionId);
  const targetSpec = document.blueprint.sections.find((section) => section.id === input.targetSectionId);
  const sourceSection = document.sections.find((section) => section.sectionId === input.sourceSectionId);
  const targetSection = document.sections.find((section) => section.sectionId === input.targetSectionId);

  const rejected = (reason: string): SlotMoveResult => ({
    document,
    moved: false,
    dirtyPaths: [],
    reason,
  });

  if (!sourceSpec || !targetSpec || !sourceSection || !targetSection) {
    return rejected("The source or target section is no longer available.");
  }

  const sourceIndex = sourceSection.slots.findIndex((slot) => slot.slotId === input.slotId);
  if (sourceIndex === -1) return rejected("The question slot is no longer available.");

  const slot = sourceSection.slots[sourceIndex];
  const movingAcrossSections = input.sourceSectionId !== input.targetSectionId;
  if (movingAcrossSections && sourceSection.slots.length <= 1) {
    return rejected("A section must retain at least one question slot.");
  }

  if (movingAcrossSections) {
    const compatible = questionTypesForSlot(slot, sourceSpec).some((type) =>
      targetSpec.allowedQuestionTypes.includes(type),
    );
    if (!compatible) {
      return rejected(`Section ${input.targetSectionId} does not accept this question type.`);
    }
  }

  const remainingSourceSlots = sourceSection.slots.filter((candidate) => candidate.slotId !== input.slotId);
  const targetSlots = movingAcrossSections ? targetSection.slots : remainingSourceSlots;
  const targetIndex = Math.max(0, Math.min(input.targetIndex, targetSlots.length));
  if (!movingAcrossSections && sourceIndex === targetIndex) {
    return rejected("The question is already in that position.");
  }

  const targetMarks = Number(targetSpec.marksPerQuestion);
  const movedSlot = {
    ...slot,
    order: sparseOrderAt(targetSlots, targetIndex),
    ...(movingAcrossSections ? { marks: targetMarks } : {}),
  };
  const placedSlots = [
    ...targetSlots.slice(0, targetIndex),
    movedSlot,
    ...targetSlots.slice(targetIndex),
  ];

  const sections = document.sections.map((section) => {
    if (!movingAcrossSections && section.sectionId === input.sourceSectionId) {
      return { ...section, slots: placedSlots };
    }
    if (movingAcrossSections && section.sectionId === input.sourceSectionId) {
      return { ...section, slots: remainingSourceSlots };
    }
    if (movingAcrossSections && section.sectionId === input.targetSectionId) {
      return { ...section, slots: placedSlots };
    }
    return section;
  });

  const blueprint = movingAcrossSections
    ? {
        ...document.blueprint,
        sections: document.blueprint.sections.map((section) => {
          if (section.id === input.sourceSectionId) return { ...section, questionCount: section.questionCount - 1 };
          if (section.id === input.targetSectionId) return { ...section, questionCount: section.questionCount + 1 };
          return section;
        }),
      }
    : document.blueprint;
  const dirtyPaths = movingAcrossSections
    ? [
        `sections.${input.sourceSectionId}.slots`,
        `sections.${input.targetSectionId}.slots`,
        `blueprint.sections.${input.sourceSectionId}.questionCount`,
        `blueprint.sections.${input.targetSectionId}.questionCount`,
      ]
    : [`sections.${input.sourceSectionId}.slots.${input.slotId}.order`];
  const warning = movingAcrossSections && slot.marks !== targetMarks
    ? `Question marks changed to ${targetMarks} to match Section ${input.targetSectionId}.`
    : undefined;

  return {
    document: { ...document, blueprint, sections },
    moved: true,
    dirtyPaths,
    warning,
  };
}

function mix(values: Record<string, number>) {
  return Object.fromEntries(
    Object.entries(values).map(([key, targetPercent]) => [
      key,
      { targetPercent, tolerancePercentagePoints: 5 },
    ]),
  );
}

export function nextSectionId(sections: BlueprintSection[]) {
  const alphabet = "ABCDEFGHIJKLMNOPQRSTUVWXYZ";
  const used = new Set(sections.map((section) => section.id));
  return alphabet.split("").find((id) => !used.has(id)) ?? `S${sections.length + 1}`;
}

export function pathsOverlap(left: string, right: string) {
  return left === right || left.startsWith(`${right}.`) || right.startsWith(`${left}.`);
}

export function applyExternalSlotPatch(document: PaperDraft, external: ExternalSlotPatch): PaperDraft {
  if (external.sequence <= document.generation.lastSequence) return document;

  const targetPath = `sections.${external.sectionId}.slots.${external.slotId}`;
  const conflictsWithLocalEdit = document.meta.dirtyPaths.some((path) => pathsOverlap(path, targetPath));
  const generation = { ...document.generation, lastSequence: external.sequence };

  if (conflictsWithLocalEdit) return { ...document, generation };

  const sections = document.sections.map((section) =>
    section.sectionId === external.sectionId
      ? {
          ...section,
          slots: section.slots.map((slot) =>
            slot.slotId === external.slotId && !slot.locked
              ? { ...slot, ...external.patch, slotId: slot.slotId }
              : slot,
          ),
        }
      : section,
  );
  const filledSlots = sections.reduce(
    (count, section) => count + section.slots.filter((slot) => slot.state === "ready").length,
    0,
  );
  return { ...document, sections, generation: { ...generation, filledSlots } };
}

export function blueprintErrors(blueprint: PaperBlueprint) {
  const errors: string[] = [];
  if (!blueprint.boardId || !blueprint.classId || !blueprint.subjectId || !blueprint.curriculumVersionId) {
    errors.push("Choose a board, curriculum version, class, and subject.");
  }
  if (blueprint.chapterIds.length === 0) errors.push("Choose at least one chapter.");
  if (blueprint.sections.length === 0) errors.push("Add at least one section.");
  const sectionMarks = blueprint.sections.reduce(
    (sum, section) => sum + section.questionCount * Number(section.marksPerQuestion),
    0,
  );
  if (sectionMarks !== Number(blueprint.totalMarks)) {
    errors.push(`Section marks (${sectionMarks}) must equal total marks (${blueprint.totalMarks}).`);
  }
  for (const [label, values] of [
    ["Difficulty", blueprint.difficultyMix],
    ["Source", blueprint.sourceMix],
    ["Bloom", blueprint.bloomMix],
  ] as const) {
    const total = Object.values(values ?? {}).reduce((sum, value) => sum + Number(value.targetPercent), 0);
    if (total !== 100) errors.push(`${label} targets must total 100% (currently ${total}%).`);
  }
  for (const section of blueprint.sections) {
    if (section.allowedQuestionTypes.length === 0) errors.push(`${section.id} needs an allowed question type.`);
    if ((section.chapterIds ?? []).some((id) => !blueprint.chapterIds.includes(id))) {
      errors.push(`${section.id} includes a chapter outside the blueprint scope.`);
    }
  }
  return errors;
}

export function coverageSubmissionGate(
  document: PaperDraft,
  report: PaperCoverageReport | undefined,
  acceptedVersionId: string | null,
) {
  const blockers: string[] = [];
  const slots = document.sections.flatMap((section) => section.slots);
  if (document.status !== "ready" || slots.some((slot) => slot.state !== "ready")) {
    blockers.push("Wait until every question is ready.");
  }
  if (!document.paperId || !document.revisionId) {
    blockers.push("Generate a paper version before submission.");
  } else if (
    !report
    || report.paperId !== document.paperId
    || report.versionId !== document.revisionId
    || report.source !== "persisted-final-exact-selection"
  ) {
    blockers.push("Load the final solver coverage report for this version.");
  } else if (acceptedVersionId !== document.revisionId) {
    blockers.push("Review and accept this version's coverage facts and compromises.");
  }
  return { ready: blockers.length === 0, blockers };
}
