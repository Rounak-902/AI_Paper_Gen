import type { components } from "@/api/api-types";

// The generated OpenAPI contract is the one and only blueprint payload type.
export type PaperBlueprint = components["schemas"]["FeasibilityRequest-Input"];
export type BlueprintSection = components["schemas"]["SectionIn-Input"];
export type FeasibilityResult = components["schemas"]["FeasibilityResponse"];
export type GeneratePaperRequest = components["schemas"]["GeneratePaperRequest"];
export type GenerationAccepted = components["schemas"]["GenerationAccepted"];
export type PaperCoverageReport = components["schemas"]["PaperCoverageReport"];
export type PaperRealtimeState = components["schemas"]["PaperRealtimeState"];
export type PaperDraftResource = components["schemas"]["PaperDraftResource"];
export type SavePaperDraftRequest = components["schemas"]["SavePaperDraftRequest"];

export type SlotState = "empty" | "generating" | "ready" | "failed";

export type PaperSlot = {
  slotId: string;
  order: number;
  state: SlotState;
  origin: "bank" | "ai_generated" | null;
  locked: boolean;
  lockOwnerId: string | null;
  questionId: string | null;
  question: Record<string, unknown> | null;
  marks: number;
  difficulty: string | null;
  bloomLevel: string | null;
  chapterId: string | null;
  topicId: string | null;
  edited: boolean;
  error: string | null;
};

export type PaperDocumentSection = {
  sectionId: string;
  order: number;
  title: string;
  slots: PaperSlot[];
};

export type PaperDraft = {
  paperId: string | null;
  version: number;
  revisionId: string | null;
  status: "draft" | "generating" | "ready" | "failed";
  title: string;
  durationMinutes: number;
  instructions: string;
  blueprint: PaperBlueprint;
  sections: PaperDocumentSection[];
  generation: {
    jobId: string | null;
    runId: string | null;
    lastSequence: number;
    filledSlots: number;
    totalSlots: number;
  };
  meta: {
    dirtyPaths: string[];
    lastSavedAt: string | null;
    saveState: "clean" | "dirty" | "saving" | "error";
    saveError: string | null;
    lastEditKind: "edit" | "reorder" | "undo" | "redo" | "system" | null;
  };
};

export type ExternalSlotPatch = {
  sequence: number;
  sectionId: string;
  slotId: string;
  patch: Partial<Omit<PaperSlot, "slotId">>;
};

export type RegenerationRequest = components["schemas"]["RegenerationRequest"];
export type RegenerationAccepted = components["schemas"]["RegenerationAccepted"];

export type GenerationProgressPatch = {
  sequence: number;
  runId: string;
  filledSlots: number;
  totalSlots: number;
  status: string;
};
