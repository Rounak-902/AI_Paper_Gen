"use client";

import { DragDropProvider, type DragEndEvent } from "@dnd-kit/react";
import { isSortable } from "@dnd-kit/react/sortable";
import { useMutation, useQuery } from "@tanstack/react-query";
import type { components } from "@/api/api-types";
import {
  AlertCircle,
  Check,
  CheckCircle2,
  ChevronLeft,
  Clock3,
  FileText,
  FileDown,
  Eye,
  Loader2,
  Plus,
  Redo2,
  RefreshCw,
  Save,
  Sparkles,
  Trash2,
  Undo2,
} from "lucide-react";
import Link from "next/link";
import { useCallback, useEffect, useMemo, useState } from "react";
import { Alert } from "@/components/ui/alert";
import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import { Checkbox } from "@/components/ui/checkbox";
import {
  Dialog,
  DialogContent,
  DialogDescription,
  DialogFooter,
  DialogHeader,
  DialogTitle,
} from "@/components/ui/dialog";
import { Input } from "@/components/ui/input";
import { cn } from "@/lib/utils";
import { useSessionStore } from "@/stores/session";
import {
  checkFeasibility,
  generatePaper,
  getPaperCoverageReport,
  listBoards,
  listChapters,
  listClasses,
  listOutcomes,
  listSubjects,
  listVersions,
  paperBuilderKeys,
  regeneratePaperSlots,
} from "../api";
import { blueprintErrors, coverageSubmissionGate } from "../document";
import { canRestoreUserSnapshot, usePaperBuilderStore } from "../store";
import type { FeasibilityResult, PaperBlueprint, PaperCoverageReport, PaperSlot } from "../types";
import { usePaperEvents } from "../use-paper-events";
import { usePaperDraftAutosave } from "../use-paper-draft-autosave";
import { SortableQuestionCard } from "./sortable-question-card";
import { ExportDialog } from "@/features/exports/components/export-dialog";

const QUESTION_TYPES = [
  "mcq",
  "true_false",
  "one_word",
  "fill_blank",
  "short_answer",
  "long_answer",
  "assertion_reason",
  "case_study",
  "numerical",
  "diagram_based",
  "match",
  "very_short_answer",
  "source_based",
];

type Board = components["schemas"]["BoardOut"];
type CurriculumVersion = components["schemas"]["CurriculumVersionOut"];
type SchoolClass = components["schemas"]["ClassOut"];
type Subject = components["schemas"]["SubjectOut"];
type Chapter = components["schemas"]["ChapterOut"];
type Outcome = components["schemas"]["OutcomeOut"];
type UpdateBlueprint = (patch: Partial<PaperBlueprint>, path?: string) => void;

const MIX_GROUPS = [
  { key: "difficultyMix", label: "Difficulty", values: ["easy", "medium", "hard"] },
  { key: "sourceMix", label: "Source", values: ["textbook", "institute_bank", "intext", "previous_year", "ai_generated", "template_generated"] },
  { key: "bloomMix", label: "Bloom", values: ["remember", "understand", "apply", "analyze", "evaluate", "create"] },
] as const;

export function PaperBuilderScreen() {
  const document = usePaperBuilderStore((state) => state.document);
  const updatePaper = usePaperBuilderStore((state) => state.updatePaper);
  const updateBlueprint = usePaperBuilderStore((state) => state.updateBlueprint);
  const updateSection = usePaperBuilderStore((state) => state.updateSection);
  const renameSection = usePaperBuilderStore((state) => state.renameSection);
  const removeSection = usePaperBuilderStore((state) => state.removeSection);
  const addSection = usePaperBuilderStore((state) => state.addSection);
  const moveSlot = usePaperBuilderStore((state) => state.moveSlot);
  const toggleSlotLock = usePaperBuilderStore((state) => state.toggleSlotLock);
  const startRegeneration = usePaperBuilderStore((state) => state.startRegeneration);
  const startInitialGeneration = usePaperBuilderStore((state) => state.startInitialGeneration);
  const undo = usePaperBuilderStore((state) => state.undo);
  const redo = usePaperBuilderStore((state) => state.redo);
  const canUndo = usePaperBuilderStore((state) => canRestoreUserSnapshot(state.document, state.past.at(-1)));
  const canRedo = usePaperBuilderStore((state) => canRestoreUserSnapshot(state.document, state.future[0]));
  const persistence = usePaperBuilderStore((state) => state.persistence);
  const activeOrgId = useSessionStore((state) => state.activeOrgId);
  const currentUserId = useSessionStore((state) => state.user?.id ?? null);
  const sessionReady = useSessionStore((state) => state.status === "authenticated");
  const blueprint = document.blueprint;
  const [reorderNotice, setReorderNotice] = useState("");
  const [regenerationNotice, setRegenerationNotice] = useState("");
  const [generationNotice, setGenerationNotice] = useState("");
  const [coverageOpen, setCoverageOpen] = useState(false);
  const [coverageAcceptedVersionId, setCoverageAcceptedVersionId] = useState<string | null>(null);
  const [showAnswers, setShowAnswers] = useState(false);
  const [exportOpen, setExportOpen] = useState(false);
  const role = useSessionStore((state) => state.role);
  const autosave = usePaperDraftAutosave(activeOrgId);

  const realtimeMode = usePaperEvents(document.paperId);

  useEffect(() => {
    const handleShortcut = (event: KeyboardEvent) => {
      const modifier = event.ctrlKey || event.metaKey;
      if (!modifier || event.altKey || event.key.toLowerCase() !== "z") return;
      event.preventDefault();
      if (event.shiftKey) redo();
      else undo();
    };
    window.addEventListener("keydown", handleShortcut);
    return () => window.removeEventListener("keydown", handleShortcut);
  }, [redo, undo]);

  const lockedSlotIds = useMemo(
    () => document.sections.flatMap((section) => section.slots.filter((slot) => slot.locked).map((slot) => slot.slotId)),
    [document.sections],
  );
  const regeneration = useMutation({
    mutationFn: ({ targetSlotIds, lockedAtSubmission }: { targetSlotIds: string[]; lockedAtSubmission: string[] }) => {
      if (!document.paperId || !document.revisionId) throw new Error("Save and generate this paper before regenerating questions.");
      const requestId = globalThis.crypto?.randomUUID?.() ?? `regen-${Date.now()}`;
      return regeneratePaperSlots(document.paperId, {
        requestId,
        sourceVersionId: document.revisionId,
        targetSlotIds,
        lockedSlotIds: lockedAtSubmission,
      });
    },
    onSuccess: (accepted) => {
      startRegeneration(accepted);
      setRegenerationNotice(
        `Regeneration started for ${accepted.targetSlotIds.length} question${accepted.targetSlotIds.length === 1 ? "" : "s"}; ${accepted.lockedSlotIds.length} locked question${accepted.lockedSlotIds.length === 1 ? "" : "s"} frozen.`,
      );
    },
  });
  const requestRegeneration = useCallback((targetSlotIds: string[], confirmation?: string) => {
    const targets = targetSlotIds.filter((slotId) => !lockedSlotIds.includes(slotId));
    if (targets.length === 0) {
      setRegenerationNotice("There are no unlocked questions in this scope.");
      return;
    }
    if (confirmation && !window.confirm(confirmation)) return;
    regeneration.mutate({ targetSlotIds: targets, lockedAtSubmission: [...lockedSlotIds] });
  }, [lockedSlotIds, regeneration]);
  const canRegenerate = Boolean(document.paperId && document.revisionId) && !regeneration.isPending;

  const boards = useQuery({ queryKey: paperBuilderKeys.boards(activeOrgId ?? ""), queryFn: ({ signal }) => listBoards(signal), enabled: sessionReady && Boolean(activeOrgId) });
  const versions = useQuery({
    queryKey: paperBuilderKeys.versions(activeOrgId ?? "", blueprint.boardId),
    queryFn: ({ signal }) => listVersions(blueprint.boardId, signal),
    enabled: sessionReady && Boolean(blueprint.boardId),
  });
  const classes = useQuery({
    queryKey: paperBuilderKeys.classes(activeOrgId ?? "", blueprint.boardId),
    queryFn: ({ signal }) => listClasses(blueprint.boardId, signal),
    enabled: sessionReady && Boolean(blueprint.boardId),
  });
  const subjects = useQuery({
    queryKey: paperBuilderKeys.subjects(activeOrgId ?? "", blueprint.classId, blueprint.curriculumVersionId),
    queryFn: ({ signal }) => listSubjects(blueprint.classId, blueprint.curriculumVersionId, signal),
    enabled: sessionReady && Boolean(blueprint.classId && blueprint.curriculumVersionId),
  });
  const chapters = useQuery({
    queryKey: paperBuilderKeys.chapters(activeOrgId ?? "", blueprint.subjectId, blueprint.curriculumVersionId),
    queryFn: ({ signal }) => listChapters(blueprint.subjectId, blueprint.curriculumVersionId, signal),
    enabled: sessionReady && Boolean(blueprint.subjectId && blueprint.curriculumVersionId),
  });
  const outcomes = useQuery({
    queryKey: paperBuilderKeys.outcomes(activeOrgId ?? "", blueprint.chapterIds),
    queryFn: ({ signal }) => listOutcomes(blueprint.chapterIds, signal),
    enabled: sessionReady && blueprint.chapterIds.length > 0,
  });

  useEffect(() => {
    if (autosave.isHydrated && !blueprint.boardId && boards.data?.[0]) updateBlueprint({ boardId: boards.data[0].id }, "blueprint.boardId");
  }, [autosave.isHydrated, blueprint.boardId, boards.data, updateBlueprint]);
  useEffect(() => {
    if (autosave.isHydrated && blueprint.boardId && !blueprint.curriculumVersionId && versions.data?.[0]) {
      const preferred = versions.data.find((item) => item.status === "active") ?? versions.data[0];
      updateBlueprint({ curriculumVersionId: preferred.id }, "blueprint.curriculumVersionId");
    }
  }, [autosave.isHydrated, blueprint.boardId, blueprint.curriculumVersionId, updateBlueprint, versions.data]);
  useEffect(() => {
    if (autosave.isHydrated && blueprint.boardId && !blueprint.classId && classes.data?.[0]) updateBlueprint({ classId: classes.data[0].id }, "blueprint.classId");
  }, [autosave.isHydrated, blueprint.boardId, blueprint.classId, classes.data, updateBlueprint]);
  useEffect(() => {
    if (autosave.isHydrated && blueprint.classId && blueprint.curriculumVersionId && !blueprint.subjectId && subjects.data?.[0]) {
      updateBlueprint({ subjectId: subjects.data[0].id }, "blueprint.subjectId");
    }
  }, [autosave.isHydrated, blueprint.classId, blueprint.curriculumVersionId, blueprint.subjectId, subjects.data, updateBlueprint]);
  useEffect(() => {
    if (autosave.isHydrated && blueprint.subjectId && blueprint.chapterIds.length === 0 && chapters.data?.[0]) {
      updateBlueprint({ chapterIds: [chapters.data[0].id] }, "blueprint.chapterIds");
    }
  }, [autosave.isHydrated, blueprint.chapterIds.length, blueprint.subjectId, chapters.data, updateBlueprint]);

  const errors = useMemo(() => blueprintErrors(blueprint), [blueprint]);
  const debouncedBlueprint = useDebouncedValue(blueprint, 650);
  const feasibilityIsCurrent = debouncedBlueprint === blueprint;
  const debouncedErrors = useMemo(() => blueprintErrors(debouncedBlueprint), [debouncedBlueprint]);
  const feasibilityBlockingErrors = debouncedErrors.filter(
    (item) => !item.startsWith("Section marks ("),
  );
  const feasibility = useQuery({
    queryKey: ["paper-builder", "feasibility", activeOrgId, debouncedBlueprint],
    queryFn: ({ signal }) => checkFeasibility(activeOrgId!, debouncedBlueprint, signal),
    enabled: Boolean(activeOrgId) && feasibilityBlockingErrors.length === 0,
    retry: false,
    staleTime: 15_000,
  });
  const recommendedBlueprint = feasibilityIsCurrent
    ? feasibility.data?.recommendedBlueprint
    : undefined;
  const applyBankRecommendation = useCallback(() => {
    if (!recommendedBlueprint) return;
    updateBlueprint(
      recommendedBlueprint as unknown as PaperBlueprint,
      "blueprint.bankRecommendation",
    );
    setGenerationNotice(
      "Applied a solver-proven section shape and bank-aware difficulty, source, and Bloom targets.",
    );
  }, [recommendedBlueprint, updateBlueprint]);
  const coverageReport = useQuery({
    queryKey: paperBuilderKeys.coverageReport(activeOrgId ?? "", document.paperId ?? "", document.revisionId ?? ""),
    queryFn: ({ signal }) => getPaperCoverageReport(document.paperId!, document.revisionId!, signal),
    enabled: Boolean(document.paperId && document.revisionId && document.status === "ready"),
    retry: false,
  });
  const generation = useMutation({
    mutationFn: () => {
      if (!activeOrgId) throw new Error("Choose an active organization before generating.");
      return generatePaper(activeOrgId, {
        title: document.title,
        durationMinutes: document.durationMinutes,
        blueprint: document.blueprint,
      });
    },
    onSuccess: (accepted) => {
      startInitialGeneration(accepted);
      setGenerationNotice("Generation started. Questions will appear as they are assembled.");
    },
  });
  const canGenerate = Boolean(activeOrgId)
    && errors.length === 0
    && feasibilityIsCurrent
    && Boolean(feasibility.data)
    && !feasibility.error
    && feasibility.data?.outcome !== "infeasible"
    && !feasibility.isFetching
    && !generation.isPending;
  const submitGate = coverageSubmissionGate(document, coverageReport.data, coverageAcceptedVersionId);
  const canRetryInitialGeneration = document.status === "failed" && !document.revisionId;
  const sectionMarks = blueprint.sections.reduce(
    (sum, section) => sum + section.questionCount * Number(section.marksPerQuestion),
    0,
  );
  const handleDragEnd = useCallback((event: DragEndEvent) => {
    if (event.canceled) {
      setReorderNotice("Question reordering cancelled.");
      return;
    }
    const source = event.operation.source;
    if (!isSortable(source)) return;

    const sourceGroup = source.initialGroup ?? source.group;
    const targetGroup = source.group ?? source.initialGroup;
    if (sourceGroup === undefined || targetGroup === undefined) return;

    const sourceSectionId = String(sourceGroup);
    const targetSectionId = String(targetGroup);
    const sourceSection = document.sections.find((section) => section.sectionId === sourceSectionId);
    const result = moveSlot(sourceSectionId, String(source.id), targetSectionId, source.index);
    if (!result.moved) {
      setReorderNotice(result.reason ?? "Question position did not change.");
      return;
    }

    const originalNumber = source.initialIndex + 1;
    const total = sourceSection?.slots.length ?? 0;
    const destination = sourceSectionId === targetSectionId ? "" : ` in Section ${targetSectionId}`;
    const movement = `Question ${originalNumber} of ${total}, Section ${sourceSectionId}, moved to position ${source.index + 1}${destination}.`;
    setReorderNotice(result.warning ? `${movement} ${result.warning}` : movement);
  }, [document.sections, moveSlot]);

  return (
    <DragDropProvider onDragEnd={handleDragEnd}>
      <div className="flex min-h-[calc(100vh-4rem)] flex-col bg-slate-100">
      <div className="flex min-h-14 items-center gap-3 border-b bg-white px-4">
        <Button asChild size="sm" variant="ghost"><Link href="/papers"><ChevronLeft />Papers</Link></Button>
        <div className="min-w-0 flex-1">
          <Input
            aria-label="Paper title"
            className="h-8 max-w-xl border-0 px-1 text-base font-semibold shadow-none focus-visible:ring-1"
            onChange={(event) => updatePaper({ title: event.target.value })}
            value={document.title}
          />
          <p className="flex items-center gap-1 px-1 text-xs text-muted-foreground" data-autosave-state={document.meta.saveState}>
            <Save className="h-3 w-3" />
            {persistence.phase === "loading"
              ? "Restoring draft…"
              : document.meta.saveState === "saving"
                ? "Saving…"
                : document.meta.saveState === "error"
                  ? "Not saved"
                  : document.meta.saveState === "dirty"
                    ? "Unsaved changes"
                    : document.meta.lastSavedAt
                      ? `Saved ${new Date(document.meta.lastSavedAt).toLocaleTimeString()}`
                      : "Draft ready"}
          </p>
        </div>
        <div className="flex items-center rounded-md border bg-slate-50">
          <Button aria-label="Undo last document edit" disabled={!canUndo} onClick={undo} size="icon" title="Undo (Ctrl/Cmd+Z)" variant="ghost"><Undo2 /></Button>
          <Button aria-label="Redo last document edit" disabled={!canRedo} onClick={redo} size="icon" title="Redo (Ctrl/Cmd+Shift+Z)" variant="ghost"><Redo2 /></Button>
        </div>
        <FeasibilityBadge errors={errors} isCurrent={feasibilityIsCurrent} query={feasibility} />
        {recommendedBlueprint ? (
          <Button onClick={applyBankRecommendation} size="sm" variant="outline">
            <Sparkles className="mr-1 h-4 w-4" />
            Auto-fit to bank
          </Button>
        ) : null}
        {document.paperId ? (
          <Badge
            className={realtimeMode === "polling" ? "bg-amber-600" : realtimeMode === "open" ? "bg-emerald-700" : "bg-slate-600"}
            data-realtime-mode={realtimeMode}
          >
            {realtimeMode === "polling" ? "Live via polling" : realtimeMode === "open" ? "Live" : "Reconnecting"}
          </Badge>
        ) : null}
        {document.revisionId ? (
          <Button onClick={() => setCoverageOpen(true)} size="sm" variant="outline">
            <Eye />Coverage report
          </Button>
        ) : null}
        {document.paperId && document.revisionId && activeOrgId ? (
          <Button onClick={() => setExportOpen(true)} size="sm" variant="outline">
            <FileDown />Export
          </Button>
        ) : null}
        {submitGate.ready ? <Badge className="bg-emerald-700"><Check className="mr-1 h-3 w-3" />Ready to submit</Badge> : null}
        {canRetryInitialGeneration ? (
          <Button
            disabled={!canGenerate}
            onClick={() => generation.mutate()}
            size="sm"
            title="Retry initial paper generation"
          >
            <RefreshCw className={generation.isPending ? "animate-spin" : undefined} />Retry generation
          </Button>
        ) : document.paperId ? (
          <Button
            disabled={!canRegenerate}
            onClick={() => {
              const unlocked = document.sections.flatMap((section) => section.slots.filter((slot) => !slot.locked).map((slot) => slot.slotId));
              requestRegeneration(unlocked, `Regenerate ${unlocked.length} unlocked questions across the paper? ${lockedSlotIds.length} locked questions will be preserved.`);
            }}
            size="sm"
            title={document.revisionId ? "Regenerate every unlocked question" : "A generated paper version is required"}
          >
            <RefreshCw className={regeneration.isPending ? "animate-spin" : undefined} />Regenerate paper
          </Button>
        ) : (
          <Button
            size="sm"
            disabled={!canGenerate}
            onClick={() => generation.mutate()}
            title={!feasibilityIsCurrent || feasibility.isFetching
              ? "Wait for the current blueprint's feasibility check"
              : feasibility.data?.outcome === "infeasible"
                ? "Resolve the infeasible blueprint before generating"
                : "Submit this blueprint and start generation"}
          >
            {generation.isPending ? <Loader2 className="animate-spin" /> : <Sparkles />}Generate
          </Button>
        )}
      </div>

      {document.meta.saveError && persistence.phase !== "conflict" ? (
        <Alert className="m-3 flex items-center justify-between gap-3 border-red-200 bg-red-50 text-red-900" data-autosave-error>
          <span>{document.meta.saveError}</span>
          <span className="flex shrink-0 gap-2">
            {document.meta.lastEditKind === "reorder" && canUndo ? <Button onClick={undo} size="sm" variant="outline">Undo reorder</Button> : null}
            <Button onClick={autosave.retry} size="sm" variant="outline">Retry save</Button>
          </span>
        </Alert>
      ) : null}

      <div className="grid min-h-0 flex-1 grid-cols-1 lg:grid-cols-[minmax(260px,0.8fr)_minmax(430px,1.45fr)_minmax(260px,0.85fr)]">
        <aside className="min-h-0 overflow-y-auto border-r bg-white p-4 lg:max-h-[calc(100vh-7.5rem)]">
          <PaneHeading eyebrow="Specify" title="Blueprint" description="Define the constraints the solver must satisfy." />
          <div className="mt-5 space-y-5">
            <CurriculumFields
              blueprint={blueprint}
              boards={boards.data ?? []}
              versions={versions.data ?? []}
              classes={classes.data ?? []}
              subjects={subjects.data ?? []}
              chapters={chapters.data ?? []}
              updateBlueprint={updateBlueprint}
            />

            <FieldGroup title="Paper shape">
              <div className="grid grid-cols-2 gap-3">
                <NumberField label="Total marks" min={1} value={Number(blueprint.totalMarks)} onChange={(value) => updateBlueprint({ totalMarks: value }, "blueprint.totalMarks")} />
                <NumberField label="Duration (min)" min={1} value={document.durationMinutes} onChange={(value) => updatePaper({ durationMinutes: value })} />
              </div>
              <label className="block text-xs font-medium text-muted-foreground">Language
                <select className={selectClass} value={blueprint.language} onChange={(event) => updateBlueprint({ language: event.target.value }, "blueprint.language")}>
                  <option value="en">English</option><option value="hi">Hindi</option>
                </select>
              </label>
            </FieldGroup>

            <FieldGroup title="Question creation">
              <div className="grid grid-cols-1 gap-2 sm:grid-cols-2">
                <Button
                  aria-pressed={blueprint.generationMode === "global_bank"}
                  className="h-auto min-h-16 items-start justify-start whitespace-normal px-3 py-2 text-left"
                  onClick={() => updateBlueprint({ generationMode: "global_bank" }, "blueprint.generationMode")}
                  type="button"
                  variant={blueprint.generationMode === "global_bank" ? "default" : "outline"}
                >
                  <FileText className="mt-0.5 h-4 w-4 shrink-0" />
                  <span><strong className="block">Global bank only</strong><span className="block text-[11px] font-normal opacity-80">Approved shared questions; no AI authoring.</span></span>
                </Button>
                <Button
                  aria-pressed={blueprint.generationMode === "mixed"}
                  className="h-auto min-h-16 items-start justify-start whitespace-normal px-3 py-2 text-left"
                  onClick={() => updateBlueprint({ generationMode: "mixed", textbookFloorPercent: 70 }, "blueprint.generationMode")}
                  type="button"
                  variant={blueprint.generationMode === "mixed" ? "default" : "outline"}
                >
                  <Sparkles className="mt-0.5 h-4 w-4 shrink-0" />
                  <span><strong className="block">Bank + AI if needed</strong><span className="block text-[11px] font-normal opacity-80">Retrieval first; AI fills eligible gaps.</span></span>
                </Button>
              </div>
              <p className="text-xs text-muted-foreground">
                AI-created questions remain traceable drafts. Importing textbook questions is unaffected.
              </p>
            </FieldGroup>

            <details className="rounded-lg border bg-slate-50 p-3">
              <summary className="cursor-pointer text-sm font-semibold">Advanced targets</summary>
              <p className="mt-2 text-xs text-muted-foreground">
                Optional. Auto-fit derives these targets from a solver-valid selection in the approved bank.
              </p>
              <div className="mt-4 space-y-5">
                {MIX_GROUPS.map((group) => (
                  <MixEditor key={group.key} blueprint={blueprint} group={group} updateBlueprint={updateBlueprint} />
                ))}
                <label className="block text-xs font-medium text-muted-foreground">Specific source override
                  <select className={selectClass} value={blueprint.generationMode} onChange={(event) => updateBlueprint({ generationMode: event.target.value as PaperBlueprint["generationMode"] }, "blueprint.generationMode")}>
                    {['mixed','textual','intext','ai_generated','previous_year','institute_bank','global_bank'].map((value) => <option key={value} value={value}>{humanize(value)}</option>)}
                  </select>
                </label>
                <NumberField label="Textbook floor (%)" min={0} max={100} value={Number(blueprint.textbookFloorPercent)} onChange={(value) => updateBlueprint({ textbookFloorPercent: value }, "blueprint.textbookFloorPercent")} />
                <CoverageEditor
                  blueprint={blueprint}
                  chapters={chapters.data ?? []}
                  outcomes={outcomes.data ?? []}
                  updateBlueprint={updateBlueprint}
                />
              </div>
            </details>
          </div>
        </aside>

        <main className="min-h-0 overflow-y-auto p-4 lg:max-h-[calc(100vh-7.5rem)] lg:p-6">
          <div className="mb-5 flex items-end justify-between gap-4">
            <PaneHeading eyebrow="Edit" title="Paper structure" description="Slots exist now; generation will fill them later." />
            <div className="text-right text-sm">
              <p className={cn("font-semibold", sectionMarks === Number(blueprint.totalMarks) ? "text-emerald-700" : "text-amber-700")}>{sectionMarks} / {blueprint.totalMarks} marks</p>
              {sectionMarks !== Number(blueprint.totalMarks) ? <button className="text-xs text-primary underline" onClick={() => updateBlueprint({ totalMarks: sectionMarks }, "blueprint.totalMarks")} type="button">Use section total</button> : null}
            </div>
          </div>
          <div className="space-y-4">
            {blueprint.sections.map((section) => {
              const documentSection = document.sections.find((item) => item.sectionId === section.id)!;
              return (
                <section className="overflow-hidden rounded-xl border bg-white shadow-sm" key={section.id}>
                  <div className="border-b bg-slate-50 p-4">
                    <div className="flex items-center gap-3">
                      <span className="flex h-8 w-8 items-center justify-center rounded-full bg-slate-900 text-xs font-bold text-white">{section.id}</span>
                      <Input className="h-8 flex-1 border-0 bg-transparent px-0 font-semibold shadow-none" aria-label={`Section ${section.id} title`} value={documentSection.title} onChange={(event) => renameSection(section.id, event.target.value)} />
                      <span className="text-xs text-muted-foreground">{section.questionCount * Number(section.marksPerQuestion)} marks</span>
                      <Badge variant="outline">{documentSection.slots.filter((slot) => slot.locked).length} locked</Badge>
                      <Button
                        disabled={!canRegenerate || documentSection.slots.every((slot) => slot.locked)}
                        onClick={() => {
                          const unlocked = documentSection.slots.filter((slot) => !slot.locked).map((slot) => slot.slotId);
                          const lockedCount = documentSection.slots.length - unlocked.length;
                          requestRegeneration(unlocked, `Regenerate ${unlocked.length} unlocked questions in Section ${section.id}? ${lockedCount} locked questions will be preserved.`);
                        }}
                        size="sm"
                        type="button"
                        variant="outline"
                      ><RefreshCw />Regenerate</Button>
                      <Button aria-label={`Delete section ${section.id}`} disabled={blueprint.sections.length === 1} onClick={() => removeSection(section.id)} size="icon" variant="ghost"><Trash2 /></Button>
                    </div>
                    <div className="mt-3 grid gap-3 sm:grid-cols-3">
                      <NumberField label="Questions" min={1} max={200} value={section.questionCount} onChange={(value) => updateSection(section.id, { questionCount: value })} />
                      <NumberField label="Marks each" min={0.5} max={999.9} step={0.5} value={Number(section.marksPerQuestion)} onChange={(value) => updateSection(section.id, { marksPerQuestion: value })} />
                      <label className="block text-xs font-medium text-muted-foreground">Primary type
                        <select className={selectClass} value={section.allowedQuestionTypes[0]} onChange={(event) => updateSection(section.id, { allowedQuestionTypes: [event.target.value, ...section.allowedQuestionTypes.filter((type) => type !== event.target.value)] })}>
                          {QUESTION_TYPES.map((type) => <option key={type} value={type}>{humanize(type)}</option>)}
                        </select>
                      </label>
                    </div>
                    <details className="mt-3 rounded-md border bg-white p-2">
                      <summary className="cursor-pointer text-xs font-medium text-muted-foreground">Allowed question types · {section.allowedQuestionTypes.length} selected</summary>
                      <div className="mt-2 flex flex-wrap gap-2">
                        {QUESTION_TYPES.map((type) => {
                          const checked = section.allowedQuestionTypes.includes(type);
                          return <ToggleChip key={type} checked={checked} label={humanize(type)} onChange={() => updateSection(section.id, { allowedQuestionTypes: checked ? (section.allowedQuestionTypes.length === 1 ? section.allowedQuestionTypes : section.allowedQuestionTypes.filter((item) => item !== type)) : [...section.allowedQuestionTypes, type] })} />;
                        })}
                      </div>
                    </details>
                    <div className="mt-3">
                      <p className="mb-2 text-xs font-medium text-muted-foreground">Chapter scope <span className="font-normal">(none = all selected)</span></p>
                      <div className="flex flex-wrap gap-2">
                        {(chapters.data ?? []).filter((chapter) => blueprint.chapterIds.includes(chapter.id)).map((chapter) => {
                          const checked = (section.chapterIds ?? []).includes(chapter.id);
                          return <ToggleChip key={chapter.id} checked={checked} label={chapter.title} onChange={() => updateSection(section.id, { chapterIds: checked ? (section.chapterIds ?? []).filter((id) => id !== chapter.id) : [...(section.chapterIds ?? []), chapter.id] })} />;
                        })}
                      </div>
                    </div>
                  </div>
                  <div className="p-4">
                    <p className="mb-3 text-xs text-muted-foreground">Use the drag handle to reorder. Press Space or Enter to pick up a question, arrow keys to move it, and Space or Enter to drop it.</p>
                    <ol aria-label={`Questions in Section ${section.id}`} className="space-y-2">
                      {documentSection.slots.map((slot, slotIndex) => (
                        <SortableQuestionCard
                          canRegenerate={canRetryInitialGeneration && slot.state === "failed"
                            ? canGenerate
                            : canRegenerate && Boolean(slot.questionId)}
                          displayNumber={slotIndex + 1}
                          key={slot.slotId}
                          onRegenerate={() => {
                            if (canRetryInitialGeneration && slot.state === "failed") generation.mutate();
                            else requestRegeneration([slot.slotId]);
                          }}
                          onToggleLock={() => toggleSlotLock(section.id, slot.slotId, currentUserId)}
                          retryInitialGeneration={canRetryInitialGeneration && slot.state === "failed"}
                          sectionId={section.id}
                          slot={slot}
                        />
                      ))}
                    </ol>
                  </div>
                </section>
              );
            })}
            <Button className="w-full border-dashed" onClick={addSection} variant="outline"><Plus />Add section</Button>
          </div>
        </main>

        <aside className="min-h-0 overflow-y-auto border-l bg-slate-50 p-4 lg:max-h-[calc(100vh-7.5rem)]">
          <PaneHeading eyebrow="Verify" title="Live preview" description="A read-only view derived from the document." />
          <div className="mx-auto mt-5 min-h-[620px] max-w-md rounded-sm border bg-white p-6 shadow-md">
            <div className="border-b-2 border-slate-900 pb-4 text-center">
              <div className="mx-auto mb-2 flex h-10 w-10 items-center justify-center rounded-full bg-primary/10 text-primary"><FileText /></div>
              <h2 className="font-serif text-xl font-bold">{document.title || "Untitled question paper"}</h2>
              <div className="mt-2 flex justify-center gap-4 text-xs text-slate-600"><span className="flex items-center gap-1"><Clock3 className="h-3 w-3" />{document.durationMinutes} min</span><span>{blueprint.totalMarks} marks</span></div>
            </div>
            <p className="my-4 text-xs leading-5 text-slate-600">{document.instructions}</p>
            <label className="mb-4 flex items-center gap-2 text-xs font-medium text-slate-700">
              <Checkbox checked={showAnswers} onCheckedChange={(checked) => setShowAnswers(checked === true)} />
              Show answer key inline
            </label>
            <div className="space-y-5">
              {document.sections.map((section) => {
                const spec = blueprint.sections.find((item) => item.id === section.sectionId)!;
                return <div key={section.sectionId}><div className="mb-2 flex items-baseline justify-between border-b pb-1"><h3 className="font-serif font-bold">{section.title}</h3><span className="text-[10px] text-slate-500">{humanize(spec.allowedQuestionTypes[0])} · {spec.marksPerQuestion} each</span></div><ol className="space-y-3 pl-5 text-xs text-slate-800">{section.slots.map((slot) => <PreviewQuestion key={slot.slotId} showAnswer={showAnswers} slot={slot} />)}</ol></div>;
              })}
            </div>
            <p className="mt-6 border-t pt-3 text-center text-[10px] text-slate-400">Layout preview · final export controls pagination</p>
          </div>
          <FeasibilityPanel
            errors={errors}
            result={feasibility.data}
            isCurrent={feasibilityIsCurrent}
            isFetching={feasibility.isFetching}
            error={feasibility.error}
            onRetry={() => void feasibility.refetch()}
            onApplyRecommendation={applyBankRecommendation}
          />
        </aside>
      </div>
      <CoverageGateDialog
        accepted={coverageAcceptedVersionId === document.revisionId}
        gate={submitGate}
        loading={coverageReport.isFetching}
        onAccept={() => {
          if (document.revisionId) setCoverageAcceptedVersionId(document.revisionId);
          setCoverageOpen(false);
        }}
        onOpenChange={setCoverageOpen}
        open={coverageOpen}
        report={coverageReport.data}
        reportError={coverageReport.error}
      />
      {document.paperId && document.revisionId && activeOrgId ? (
        <ExportDialog
          onOpenChange={setExportOpen}
          open={exportOpen}
          orgId={activeOrgId}
          paperId={document.paperId}
          paperTitle={document.title}
          role={role}
          versionId={document.revisionId}
        />
      ) : null}
      <Dialog open={Boolean(autosave.conflict)}>
        <DialogContent data-draft-conflict>
          <DialogHeader>
            <DialogTitle>This draft changed in another tab</DialogTitle>
            <DialogDescription>
              Autosave stopped before overwriting either copy. Continue from the server copy, or save this tab as a separate draft.
            </DialogDescription>
          </DialogHeader>
          <Alert className="border-amber-200 bg-amber-50 text-amber-950">
            Your current tab remains intact. Loading the server copy also keeps a browser recovery snapshot.
          </Alert>
          <DialogFooter className="flex-wrap gap-2">
            <Button onClick={autosave.downloadLocalCopy} variant="outline">Download my copy</Button>
            <Button onClick={autosave.useServerCopy} variant="outline">Use server copy</Button>
            <Button onClick={() => void autosave.saveMineAsCopy()}>Save mine as a separate draft</Button>
          </DialogFooter>
        </DialogContent>
      </Dialog>
      <p aria-atomic="true" aria-live="polite" className="sr-only" data-reorder-announcement>{reorderNotice}</p>
      <p aria-atomic="true" aria-live="polite" className="sr-only" data-regeneration-announcement>{regenerationNotice}</p>
      <p aria-atomic="true" aria-live="polite" className="sr-only" data-generation-announcement>{generationNotice}</p>
      {regeneration.error ? <div className="fixed bottom-4 right-4 max-w-sm rounded-md border border-red-200 bg-red-50 p-3 text-sm text-red-800 shadow-lg">{regeneration.error.message}</div> : null}
      {generation.error ? <div className="fixed bottom-4 right-4 max-w-sm rounded-md border border-red-200 bg-red-50 p-3 text-sm text-red-800 shadow-lg">{generation.error.message}</div> : null}
      </div>
    </DragDropProvider>
  );
}

function CurriculumFields({ blueprint, boards, versions, classes, subjects, chapters, updateBlueprint }: {
  blueprint: PaperBlueprint;
  boards: Board[];
  versions: CurriculumVersion[];
  classes: SchoolClass[];
  subjects: Subject[];
  chapters: Chapter[];
  updateBlueprint: UpdateBlueprint;
}) {
  return <FieldGroup title="Curriculum">
    <SelectField label="Board" value={blueprint.boardId} options={boards.map((item) => ({ value: item.id, label: item.name }))} onChange={(boardId) => updateBlueprint({ boardId, curriculumVersionId: "", classId: "", subjectId: "", chapterIds: [], chapterCoverage: [], learningOutcomeCoverage: [] }, "blueprint.curriculum")} />
    <SelectField label="Curriculum version" value={blueprint.curriculumVersionId} options={versions.map((item) => ({ value: item.id, label: `${item.label ?? item.academicYear}${item.status === 'active' ? ' · Active' : ''}` }))} onChange={(curriculumVersionId) => updateBlueprint({ curriculumVersionId, subjectId: "", chapterIds: [], chapterCoverage: [], learningOutcomeCoverage: [] }, "blueprint.curriculumVersionId")} />
    <SelectField label="Class" value={blueprint.classId} options={classes.map((item) => ({ value: item.id, label: item.name }))} onChange={(classId) => updateBlueprint({ classId, subjectId: "", chapterIds: [], chapterCoverage: [], learningOutcomeCoverage: [] }, "blueprint.classId")} />
    <SelectField label="Subject" value={blueprint.subjectId} options={subjects.map((item) => ({ value: item.id, label: item.name }))} onChange={(subjectId) => updateBlueprint({ subjectId, chapterIds: [], chapterCoverage: [], learningOutcomeCoverage: [] }, "blueprint.subjectId")} />
    <div><p className="mb-2 text-xs font-medium text-muted-foreground">Chapters</p><div className="flex flex-wrap gap-2">{chapters.map((chapter) => { const checked = blueprint.chapterIds.includes(chapter.id); return <ToggleChip key={chapter.id} checked={checked} label={`${chapter.seq}. ${chapter.title}`} onChange={() => { const chapterIds = checked ? blueprint.chapterIds.filter((id) => id !== chapter.id) : [...blueprint.chapterIds, chapter.id]; updateBlueprint({ chapterIds, chapterCoverage: (blueprint.chapterCoverage ?? []).filter((entry) => chapterIds.includes(entry.chapterId)), learningOutcomeCoverage: checked ? [] : blueprint.learningOutcomeCoverage }, "blueprint.chapterIds"); }} />; })}</div></div>
  </FieldGroup>;
}

function MixEditor({ blueprint, group, updateBlueprint }: { blueprint: PaperBlueprint; group: (typeof MIX_GROUPS)[number]; updateBlueprint: (patch: Partial<PaperBlueprint>, path?: string) => void }) {
  const values = blueprint[group.key] ?? {};
  const total = Object.values(values).reduce((sum, value) => sum + Number(value.targetPercent), 0);
  return <div><div className="mb-2 flex justify-between text-xs font-medium"><span>{group.label} mix</span><span className={total === 100 ? "text-emerald-700" : "text-amber-700"}>{total}%</span></div><div className="grid grid-cols-3 gap-2">{group.values.map((name) => <label className="text-[11px] text-muted-foreground" key={name}>{humanize(name)}<Input className="mt-1 h-8 px-2" min={0} max={100} type="number" value={Number(values[name]?.targetPercent ?? 0)} onChange={(event) => updateBlueprint({ [group.key]: { ...values, [name]: { targetPercent: Number(event.target.value), tolerancePercentagePoints: Number(values[name]?.tolerancePercentagePoints ?? 5) } } }, `blueprint.${group.key}.${name}`)} /></label>)}</div></div>;
}

function CoverageEditor({ blueprint, chapters, outcomes, updateBlueprint }: {
  blueprint: PaperBlueprint;
  chapters: Chapter[];
  outcomes: Outcome[];
  updateBlueprint: UpdateBlueprint;
}) {
  const chapterCoverage = blueprint.chapterCoverage ?? [];
  const learningOutcomeCoverage = blueprint.learningOutcomeCoverage ?? [];
  return <div className="space-y-4"><div><p className="mb-2 text-xs font-semibold">Chapter minimum coverage</p>{chapters.filter((item) => blueprint.chapterIds.includes(item.id)).map((chapter) => { const entry = chapterCoverage.find((item) => item.chapterId === chapter.id); return <div className="mb-2 flex items-center gap-2" key={chapter.id}><Checkbox checked={Boolean(entry)} onCheckedChange={(checked) => updateBlueprint({ chapterCoverage: checked ? [...chapterCoverage, { chapterId: chapter.id, minimumPercent: 10, tolerancePercentagePoints: 5 }] : chapterCoverage.filter((item) => item.chapterId !== chapter.id) }, "blueprint.chapterCoverage")} /><span className="min-w-0 flex-1 truncate text-xs">{chapter.title}</span>{entry ? <Input aria-label={`${chapter.title} minimum percent`} className="h-8 w-16 px-2" type="number" min={1} max={100} value={Number(entry.minimumPercent)} onChange={(event) => updateBlueprint({ chapterCoverage: chapterCoverage.map((item) => item.chapterId === chapter.id ? { ...item, minimumPercent: Number(event.target.value) } : item) }, "blueprint.chapterCoverage")} /> : null}</div>; })}</div><div><p className="mb-2 text-xs font-semibold">Learning-outcome minimum coverage</p>{outcomes.length === 0 ? <p className="text-xs text-muted-foreground">No outcomes available for selected chapters.</p> : outcomes.map((outcome) => { const entry = learningOutcomeCoverage.find((item) => item.learningOutcomeId === outcome.id); return <div className="mb-2 flex items-center gap-2" key={outcome.id}><Checkbox checked={Boolean(entry)} onCheckedChange={(checked) => updateBlueprint({ learningOutcomeCoverage: checked ? [...learningOutcomeCoverage, { learningOutcomeId: outcome.id, minimumPercent: 10, tolerancePercentagePoints: 5 }] : learningOutcomeCoverage.filter((item) => item.learningOutcomeId !== outcome.id) }, "blueprint.learningOutcomeCoverage")} /><span className="min-w-0 flex-1 truncate text-xs" title={outcome.description}>{outcome.code ?? outcome.description}</span>{entry ? <Input aria-label={`${outcome.code ?? 'Outcome'} minimum percent`} className="h-8 w-16 px-2" type="number" min={1} max={100} value={Number(entry.minimumPercent)} onChange={(event) => updateBlueprint({ learningOutcomeCoverage: learningOutcomeCoverage.map((item) => item.learningOutcomeId === outcome.id ? { ...item, minimumPercent: Number(event.target.value) } : item) }, "blueprint.learningOutcomeCoverage")} /> : null}</div>; })}</div></div>;
}

function FeasibilityBadge({ errors, isCurrent, query }: { errors: string[]; isCurrent: boolean; query: { isFetching: boolean; data?: FeasibilityResult } }) {
  if (errors.length) return <Badge variant="outline"><AlertCircle className="mr-1 h-3 w-3 text-amber-600" />Needs attention</Badge>;
  if (!isCurrent || query.isFetching) return <Badge variant="secondary"><Loader2 className="mr-1 h-3 w-3 animate-spin" />Checking</Badge>;
  if (query.data?.outcome === "infeasible") return <Badge variant="destructive">Infeasible</Badge>;
  if (query.data) return <Badge className="bg-emerald-700"><Check className="mr-1 h-3 w-3" />{query.data.outcome === "feasible" ? "Feasible" : "Feasible with compromise"}</Badge>;
  return <Badge variant="outline">Not checked</Badge>;
}

function FeasibilityPanel({ errors, result, isCurrent, isFetching, error, onRetry, onApplyRecommendation }: { errors: string[]; result?: FeasibilityResult; isCurrent: boolean; isFetching: boolean; error: Error | null; onRetry: () => void; onApplyRecommendation: () => void }) {
  const visibleResult = isCurrent ? result : undefined;
  return <div className="mt-4 rounded-lg border bg-white p-4"><div className="flex items-center justify-between"><h3 className="text-sm font-semibold">Live feasibility</h3><Button aria-label="Run feasibility check" disabled={errors.length > 0 || isFetching || !isCurrent} onClick={onRetry} size="icon" variant="ghost"><RefreshCw className={cn((isFetching || !isCurrent) && "animate-spin")} /></Button></div>{!isCurrent && errors.length === 0 ? <p className="mt-3 flex gap-2 text-xs text-blue-800"><Loader2 className="h-3 w-3 animate-spin" />Checking the edited blueprint…</p> : null}{errors.length ? <div className="mt-3 space-y-1">{errors.map((item) => <p className="flex gap-2 text-xs text-amber-800" key={item}><AlertCircle className="mt-0.5 h-3 w-3 shrink-0" />{item}</p>)}</div> : null}{error && isCurrent ? <Alert className="mt-3 border-red-200 bg-red-50 text-red-800">{error.message}</Alert> : null}{visibleResult ? <div className="mt-3 space-y-3"><div className="flex items-center gap-2 text-sm font-medium"><CheckCircle2 className={cn("h-4 w-4", visibleResult.outcome === "infeasible" ? "text-red-600" : "text-emerald-600")} />{humanize(visibleResult.outcome)}</div>{visibleResult.recommendedBlueprint ? <Alert className="border-blue-200 bg-blue-50 text-blue-950"><div className="space-y-2"><p><strong>Bank-aware fix available.</strong> Sections and advanced targets can be fitted automatically to the approved questions.</p><Button onClick={onApplyRecommendation} size="sm"><Sparkles className="mr-1 h-4 w-4" />Auto-fit sections and targets</Button></div></Alert> : null}<div className="grid grid-cols-2 gap-2 text-xs"><Metric label="Eligible bank" value={visibleResult.bankSnapshot.eligibleQuestionCount} /><Metric label="Selected" value={visibleResult.bankSnapshot.selectedQuestionCount} /><Metric label="Solver" value={visibleResult.metrics.engine} /><Metric label="Time" value={`${Math.round(visibleResult.metrics.totalTimeMs)} ms`} /></div><CoverageFacts facts={visibleResult.coverage} />{visibleResult.reasons.map((reason) => <Alert className="border-red-200 bg-red-50 text-red-800" key={`${reason.code}-${reason.constraint}`}><strong>{reason.constraint}</strong><br />{reason.detail}</Alert>)}{visibleResult.compromises.map((item) => <Alert className="border-amber-200 bg-amber-50 text-amber-900" key={`${item.tier}-${item.constraint}`}><strong>{humanize(item.tier)}:</strong> {item.reason}</Alert>)}<p className="text-[11px] text-muted-foreground">Projected by the real synchronous solver · {visibleResult.requestFlow} · no enqueue</p></div> : null}</div>;
}

function PreviewQuestion({ showAnswer, slot }: { showAnswer: boolean; slot: PaperSlot }) {
  const content = previewQuestionContent(slot.question);
  return <li className="list-decimal pl-1">
    <div className="flex gap-2">
      <div className="min-w-0 flex-1">
        {content.stem ? <p className="leading-5">{content.stem}</p> : <p className="italic text-slate-400">{slot.state === "generating" ? "Generating question…" : slot.state === "failed" ? "Generation failed — retry this slot" : "Empty question slot"}</p>}
        {content.options.length ? <ol className="mt-1 space-y-0.5 text-[11px] text-slate-600">{content.options.map((option, index) => <li key={`${slot.slotId}-preview-option-${index}`}>{String.fromCharCode(97 + index)}. {option}</li>)}</ol> : null}
        {showAnswer && content.answer ? <p className="mt-1 rounded bg-emerald-50 px-2 py-1 text-[11px] text-emerald-900"><strong>Answer:</strong> {content.answer}</p> : null}
      </div>
      <span className="shrink-0 text-[10px] text-slate-500">[{slot.marks}]</span>
    </div>
  </li>;
}

function CoverageFacts({ facts }: { facts: PaperCoverageReport["coverage"] }) {
  if (facts.length === 0) return <p className="text-xs text-muted-foreground">The solver returned no explicit coverage constraints for this shape.</p>;
  return <div className="space-y-1.5" aria-label="Solver coverage facts">{facts.map((fact, index) => <div className="grid grid-cols-[1fr_auto] gap-2 rounded border bg-slate-50 px-2 py-1.5 text-xs" key={`${fact.dimension}-${fact.target}-${index}`}><span className="min-w-0"><strong>{humanize(fact.dimension)}</strong> · <span className="break-all">{humanize(fact.target)}</span><span className="block text-[10px] text-muted-foreground">Target {fact.targetValue}</span></span><span className={cn("self-center font-semibold", fact.satisfied ? "text-emerald-700" : "text-amber-700")}>{fact.actual} {fact.satisfied ? "✓" : "⚠"}</span></div>)}</div>;
}

function CoverageGateDialog({ accepted, gate, loading, onAccept, onOpenChange, open, report, reportError }: {
  accepted: boolean;
  gate: { ready: boolean; blockers: string[] };
  loading: boolean;
  onAccept: () => void;
  onOpenChange: (open: boolean) => void;
  open: boolean;
  report?: PaperCoverageReport;
  reportError: Error | null;
}) {
  return <Dialog onOpenChange={onOpenChange} open={open}>
    <DialogContent className="max-h-[90vh] max-w-3xl overflow-y-auto">
      <DialogHeader>
        <DialogTitle>Final solver coverage report</DialogTitle>
        <DialogDescription>This gates the later submit-for-approval or direct-export action. Generate has already produced this immutable version.</DialogDescription>
      </DialogHeader>
      {loading ? <p className="flex items-center gap-2 text-sm text-muted-foreground"><Loader2 className="h-4 w-4 animate-spin" />Loading persisted solver output…</p> : null}
      {reportError ? <Alert className="border-red-200 bg-red-50 text-red-800">{reportError.message}</Alert> : null}
      {report ? <div className="space-y-4">
        <Alert className="border-blue-200 bg-blue-50 text-blue-900">Source: final exact-selection solver output persisted with version <code>{report.versionId}</code>. This UI does not recalculate coverage.</Alert>
        <div><h3 className="mb-2 text-sm font-semibold">Coverage facts</h3><CoverageFacts facts={report.coverage} /></div>
        <div><h3 className="mb-2 text-sm font-semibold">Compromises</h3>{report.compromises.length ? <div className="space-y-2">{report.compromises.map((item) => <Alert className="border-amber-200 bg-amber-50 text-amber-900" key={`${item.tier}-${item.constraint}`}><strong>{humanize(item.tier)} · {item.constraint}</strong><br />Target {item.target}; actual {item.actual}; {item.deviation}.<br />{item.reason}</Alert>)}</div> : <p className="text-sm text-emerald-800">No solver compromises were required.</p>}</div>
        {accepted ? <p className="flex items-center gap-2 text-sm font-medium text-emerald-800"><CheckCircle2 className="h-4 w-4" />You accepted this version&apos;s coverage report.</p> : null}
      </div> : null}
      {!report && !loading ? <div className="space-y-1">{gate.blockers.map((blocker) => <p className="text-sm text-amber-800" key={blocker}>{blocker}</p>)}</div> : null}
      <DialogFooter>
        <Button disabled={!report || loading || accepted} onClick={onAccept}>{accepted ? "Coverage accepted" : "Accept tradeoffs and mark ready to submit"}</Button>
      </DialogFooter>
    </DialogContent>
  </Dialog>;
}

function previewQuestionContent(question: Record<string, unknown> | null) {
  return {
    stem: readablePreviewValue(question?.stem),
    options: Array.isArray(question?.options)
      ? question.options.map(readablePreviewValue).filter((value): value is string => Boolean(value))
      : question?.options && typeof question.options === "object"
        ? Object.values(question.options).map(readablePreviewValue).filter((value): value is string => Boolean(value))
        : [],
    answer: readablePreviewValue(question?.answer),
  };
}

function readablePreviewValue(value: unknown): string | null {
  if (typeof value === "string" || typeof value === "number") return String(value);
  if (value && typeof value === "object") {
    const record = value as Record<string, unknown>;
    return readablePreviewValue(record.text ?? record.label ?? record.value);
  }
  return null;
}

function Metric({ label, value }: { label: string; value: string | number }) { return <div className="rounded bg-slate-50 p-2"><p className="text-muted-foreground">{label}</p><p className="mt-1 font-semibold">{value}</p></div>; }
function PaneHeading({ eyebrow, title, description }: { eyebrow: string; title: string; description: string }) { return <div><p className="text-[11px] font-bold uppercase tracking-[0.16em] text-primary">{eyebrow}</p><h2 className="mt-1 text-lg font-bold">{title}</h2><p className="mt-1 text-xs leading-5 text-muted-foreground">{description}</p></div>; }
function FieldGroup({ title, children }: { title: string; children: React.ReactNode }) { return <div className="space-y-3"><h3 className="border-b pb-2 text-sm font-semibold">{title}</h3>{children}</div>; }
function NumberField({ label, value, onChange, ...props }: { label: string; value: number; onChange: (value: number) => void } & Omit<React.InputHTMLAttributes<HTMLInputElement>, "value" | "onChange">) { return <label className="block text-xs font-medium text-muted-foreground">{label}<Input className="mt-1 h-9" type="number" value={value} onChange={(event) => onChange(Number(event.target.value))} {...props} /></label>; }
function SelectField({ label, value, options, onChange }: { label: string; value: string; options: Array<{ value: string; label: string }>; onChange: (value: string) => void }) { return <label className="block text-xs font-medium text-muted-foreground">{label}<select className={selectClass} value={value} onChange={(event) => onChange(event.target.value)}><option value="">Select {label.toLowerCase()}</option>{options.map((option) => <option key={option.value} value={option.value}>{option.label}</option>)}</select></label>; }
function ToggleChip({ checked, label, onChange }: { checked: boolean; label: string; onChange: () => void }) { return <button aria-pressed={checked} className={cn("rounded-full border px-2.5 py-1 text-left text-xs transition", checked ? "border-primary bg-primary/10 text-primary" : "bg-white text-muted-foreground hover:border-slate-400")} onClick={onChange} type="button">{checked ? "✓ " : ""}{label}</button>; }
function humanize(value: string) { return value.replaceAll("_", " ").replaceAll("-", " ").replace(/\b\w/g, (letter) => letter.toUpperCase()); }
function useDebouncedValue<T>(value: T, delay: number) { const [debounced, setDebounced] = useState(value); useEffect(() => { const timeout = window.setTimeout(() => setDebounced(value), delay); return () => window.clearTimeout(timeout); }, [delay, value]); return debounced; }
const selectClass = "mt-1 flex h-9 w-full rounded-md border border-input bg-white px-3 py-1 text-sm text-foreground shadow-sm focus:outline-none focus:ring-2 focus:ring-ring disabled:opacity-50";
