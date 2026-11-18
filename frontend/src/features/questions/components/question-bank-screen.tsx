"use client";

import { Suspense } from "react";
import { QuestionBankTable } from "@/features/questions/components/question-bank-table";
import { QuestionImportDialog } from "@/features/questions/components/question-import-dialog";
import { useSessionStore } from "@/stores/session";

export function QuestionBankScreen() {
  const role = useSessionStore((state) => state.role);
  const canImport = role === "coaching_admin" || role === "super_admin";

  return (
    <div className="mx-auto max-w-[1500px] space-y-6">
      <header className="flex flex-col justify-between gap-4 sm:flex-row sm:items-start">
        <div>
          <h2 className="text-2xl font-semibold tracking-tight">Question Bank</h2>
          <p className="mt-1 text-sm text-muted-foreground">
            Search the approved inventory, inspect provenance, and review imported drafts.
          </p>
        </div>
        {canImport ? <QuestionImportDialog /> : null}
      </header>
      <Suspense fallback={<div className="h-96 animate-pulse rounded-lg bg-muted" />}>
        <QuestionBankTable />
      </Suspense>
    </div>
  );
}
