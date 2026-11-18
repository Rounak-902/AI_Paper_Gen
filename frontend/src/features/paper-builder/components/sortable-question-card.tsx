"use client";

import { useSortable } from "@dnd-kit/react/sortable";
import { GripVertical, Lock, RefreshCw, Unlock } from "lucide-react";
import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import { cn } from "@/lib/utils";
import type { PaperSlot } from "../types";

type SortableQuestionCardProps = {
  slot: PaperSlot;
  sectionId: string;
  displayNumber: number;
  canRegenerate: boolean;
  retryInitialGeneration?: boolean;
  onRegenerate: () => void;
  onToggleLock: () => void;
};

const REORDER_HELP = "Press Space or Enter to pick up this question, Arrow keys to move it, Space or Enter to drop it, or Escape to cancel.";

export function SortableQuestionCard({
  slot,
  sectionId,
  displayNumber,
  canRegenerate,
  retryInitialGeneration = false,
  onRegenerate,
  onToggleLock,
}: SortableQuestionCardProps) {
  const { ref, handleRef, isDragging, isDropTarget } = useSortable({
    id: slot.slotId,
    index: displayNumber - 1,
    group: sectionId,
    type: "paper-slot",
    accept: "paper-slot",
  });
  const content = questionContent(slot.question);
  const state = cardState(slot);

  return (
    <li
      className={cn(
        "list-none",
        isDragging && "opacity-50",
        isDropTarget && "rounded-lg ring-2 ring-primary ring-offset-2",
      )}
      data-question-slot={slot.slotId}
      ref={ref}
    >
      <article className={cn(
        "min-h-24 rounded-lg border bg-white px-3 py-3 shadow-sm transition-colors",
        content.stem ? "border-slate-200" : "border-dashed bg-slate-50",
        slot.state === "failed" && "border-red-200 bg-red-50",
      )}>
        <div className="flex items-start gap-2">
          <button
            aria-label={`Reorder question ${displayNumber} in Section ${sectionId}`}
            className="mt-0.5 inline-flex h-8 w-8 shrink-0 cursor-grab items-center justify-center rounded text-slate-500 outline-none hover:bg-slate-100 focus-visible:ring-2 focus-visible:ring-ring active:cursor-grabbing"
            ref={handleRef}
            type="button"
          >
            <GripVertical aria-hidden="true" className="h-4 w-4" />
          </button>
          <span className="mt-1 text-sm font-semibold text-slate-600">{displayNumber}.</span>
          <div className="min-w-0 flex-1">
            <div className="flex flex-wrap items-center gap-2">
              <Badge className={state.className} variant={state.variant}>{state.label}</Badge>
              {slot.origin ? <Badge variant="outline">{humanize(slot.origin)}</Badge> : null}
              {slot.locked ? <span className="inline-flex items-center gap-1 text-xs font-medium text-slate-600"><Lock aria-hidden="true" className="h-3.5 w-3.5" />Locked</span> : null}
              <span className="ml-auto flex items-center gap-1">
                <Button
                  aria-label={`${slot.locked ? "Unlock" : "Lock"} question ${displayNumber} in Section ${sectionId}`}
                  disabled={!slot.questionId}
                  onClick={onToggleLock}
                  size="icon"
                  title={slot.locked ? "Unlock question" : "Lock question against regeneration"}
                  type="button"
                  variant="ghost"
                >
                  {slot.locked ? <Unlock aria-hidden="true" /> : <Lock aria-hidden="true" />}
                </Button>
                <Button
                  aria-label={retryInitialGeneration
                    ? `Retry generation after question ${displayNumber} failed in Section ${sectionId}`
                    : `Regenerate question ${displayNumber} in Section ${sectionId}`}
                  disabled={!canRegenerate || slot.locked || slot.state === "generating"}
                  onClick={onRegenerate}
                  size="icon"
                  title={slot.locked
                    ? "Unlock this question before regenerating"
                    : retryInitialGeneration
                      ? "Retry initial paper generation"
                      : "Regenerate this question"}
                  type="button"
                  variant="ghost"
                >
                  <RefreshCw aria-hidden="true" className={slot.state === "generating" ? "animate-spin" : undefined} />
                </Button>
              </span>
            </div>
            {content.stem ? (
              <p className="mt-2 text-sm leading-6 text-slate-800">{content.stem}</p>
            ) : (
              <p className="mt-2 text-sm text-slate-500">{state.placeholder}</p>
            )}
            {content.options.length ? (
              <ol className="mt-2 space-y-1 text-xs text-slate-600">
                {content.options.slice(0, 4).map((option, index) => <li key={`${slot.slotId}-option-${index}`}>{String.fromCharCode(97 + index)}. {option}</li>)}
              </ol>
            ) : null}
            {content.answer ? <p className="mt-2 text-xs font-medium text-emerald-800">Answer: {content.answer}</p> : null}
            {slot.error ? <p className="mt-2 text-xs text-red-700">{slot.error}</p> : null}
            <div className="mt-3 flex flex-wrap gap-x-3 gap-y-1 text-xs text-slate-500">
              <span>{slot.marks} marks</span>
              {slot.difficulty ? <span>{humanize(slot.difficulty)}</span> : null}
              {slot.bloomLevel ? <span>{humanize(slot.bloomLevel)}</span> : null}
              {slot.chapterId ? <span>Chapter {slot.chapterId}</span> : null}
            </div>
          </div>
        </div>
        <span className="sr-only">{REORDER_HELP}</span>
      </article>
    </li>
  );
}

function cardState(slot: PaperSlot) {
  if (slot.state === "ready") {
    return { label: "Ready", placeholder: "Question content is ready.", className: "bg-emerald-700", variant: "default" as const };
  }
  if (slot.state === "generating") {
    return { label: "Generating", placeholder: "Generating question content…", className: "bg-blue-700", variant: "default" as const };
  }
  if (slot.state === "failed") {
    return { label: "Failed — retry", placeholder: "Question generation failed. Retry generation to continue.", className: "", variant: "destructive" as const };
  }
  return { label: "Empty slot", placeholder: "Empty slot — generation will fill this question later.", className: "", variant: "outline" as const };
}

function questionContent(question: Record<string, unknown> | null) {
  const stem = stringValue(question?.stem);
  return {
    stem,
    options: optionValues(question?.options),
    answer: readableValue(question?.answer),
  };
}

function optionValues(value: unknown) {
  if (Array.isArray(value)) return value.map(readableValue).filter((item): item is string => Boolean(item));
  if (value && typeof value === "object") return Object.values(value).map(readableValue).filter((item): item is string => Boolean(item));
  return [];
}

function readableValue(value: unknown): string | null {
  if (typeof value === "string" || typeof value === "number") return String(value);
  if (value && typeof value === "object") {
    const record = value as Record<string, unknown>;
    return stringValue(record.text) ?? stringValue(record.label) ?? stringValue(record.value) ?? null;
  }
  return null;
}

function stringValue(value: unknown): string | null {
  return typeof value === "string" && value.trim() ? value : null;
}

function humanize(value: string) {
  return value.replaceAll("_", " ").replaceAll("-", " ").replace(/\b\w/g, (letter) => letter.toUpperCase());
}
