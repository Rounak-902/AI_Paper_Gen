"use client";

import { keepPreviousData, useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { Archive, ChevronLeft, ChevronRight, Pencil, Plus, RotateCcw } from "lucide-react";
import { useState } from "react";
import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import { Card, CardContent, CardDescription, CardHeader, CardTitle } from "@/components/ui/card";
import {
  Dialog,
  DialogContent,
  DialogDescription,
  DialogFooter,
  DialogHeader,
  DialogTitle,
} from "@/components/ui/dialog";
import { listPrograms, updateProgram } from "@/features/programs/api";
import { ProgramEditorDialog } from "@/features/programs/components/program-editor-dialog";
import type { Program } from "@/features/programs/types";
import { ApiError } from "@/services/api-client";
import { useSessionStore } from "@/stores/session";

const PAGE_SIZE = 20;

export function ProgramsAdminScreen() {
  const activeOrgId = useSessionStore((state) => state.activeOrgId);
  const role = useSessionStore((state) => state.role);
  const queryClient = useQueryClient();
  const [page, setPage] = useState(1);
  const [editorOpen, setEditorOpen] = useState(false);
  const [editingProgram, setEditingProgram] = useState<Program | null>(null);
  const [statusTarget, setStatusTarget] = useState<Program | null>(null);
  const canManage = role === "coaching_admin" || role === "super_admin";

  const programsQuery = useQuery({
    queryKey: [activeOrgId, "programs", { page, pageSize: PAGE_SIZE }],
    queryFn: ({ signal }) => listPrograms(activeOrgId!, page, PAGE_SIZE, signal),
    enabled: Boolean(activeOrgId && canManage),
    placeholderData: keepPreviousData,
  });
  const statusMutation = useMutation({
    mutationFn: (program: Program) =>
      updateProgram(activeOrgId!, program.id, {
        rowVersion: program.rowVersion,
        status: program.status === "archived" ? "active" : "archived",
      }),
    onSuccess: async () => {
      setStatusTarget(null);
      await queryClient.invalidateQueries({ queryKey: [activeOrgId, "programs"] });
    },
  });

  if (!canManage) {
    return (
      <div className="mx-auto max-w-2xl rounded-lg border bg-card p-8 text-center">
        <h2 className="text-xl font-semibold">Programs administration is restricted</h2>
        <p className="mt-2 text-sm text-muted-foreground">
          A coaching administrator or super administrator role is required.
        </p>
      </div>
    );
  }

  const programs = programsQuery.data?.data ?? [];
  const hasNext = Boolean(programsQuery.data?.meta?.pagination?.hasNext);
  return (
    <div className="mx-auto max-w-7xl space-y-6">
      <header className="flex flex-col justify-between gap-4 sm:flex-row sm:items-start">
        <div>
          <h2 className="text-2xl font-semibold tracking-tight">Programs</h2>
          <p className="mt-1 text-sm text-muted-foreground">
            Manage exam cohorts and the teacher and curriculum scope they can use.
          </p>
        </div>
        <Button
          onClick={() => {
            setEditingProgram(null);
            setEditorOpen(true);
          }}
          type="button"
        >
          <Plus /> New program
        </Button>
      </header>

      {programsQuery.error ? (
        <div className="rounded-lg border border-destructive/30 bg-destructive/5 p-5" role="alert">
          <h3 className="font-semibold text-destructive">Programs could not be loaded</h3>
          <p className="mt-1 text-sm">{errorMessage(programsQuery.error)}</p>
          <Button className="mt-3" onClick={() => void programsQuery.refetch()} type="button" variant="outline">Try again</Button>
        </div>
      ) : null}

      {!programsQuery.error && programsQuery.isLoading ? (
        <div className="grid gap-4 md:grid-cols-2 xl:grid-cols-3">
          {Array.from({ length: 6 }).map((_, index) => <div className="h-44 animate-pulse rounded-lg bg-muted" key={index} />)}
        </div>
      ) : null}

      {!programsQuery.error && !programsQuery.isLoading && programs.length === 0 ? (
        <Card className="border-dashed">
          <CardHeader>
            <CardTitle className="text-lg">No programs yet</CardTitle>
            <CardDescription>Create the first program, then assign its teachers and subjects.</CardDescription>
          </CardHeader>
        </Card>
      ) : null}

      {programs.length > 0 ? (
        <div className="grid gap-4 md:grid-cols-2 xl:grid-cols-3">
          {programs.map((program) => (
            <Card key={program.id}>
              <CardHeader>
                <div className="flex items-start justify-between gap-3">
                  <div className="min-w-0">
                    <CardTitle className="truncate text-lg" title={program.name}>{program.name}</CardTitle>
                    <CardDescription className="mt-1">{program.examTarget || "No exam target"}</CardDescription>
                  </div>
                  <Badge variant={program.status === "active" ? "default" : "outline"}>
                    {program.status === "active" ? "Active" : "Archived"}
                  </Badge>
                </div>
              </CardHeader>
              <CardContent className="space-y-4">
                <dl className="grid gap-2 text-sm">
                  <ProgramMeta label="Academic year" value={program.academicYear} />
                  <ProgramMeta label="Branch ID" value={program.branchId} mono />
                  <ProgramMeta label="Updated" value={formatDate(program.updatedAt)} />
                </dl>
                <div className="flex flex-wrap gap-2">
                  <Button
                    onClick={() => {
                      setEditingProgram(program);
                      setEditorOpen(true);
                    }}
                    size="sm"
                    type="button"
                    variant="outline"
                  >
                    <Pencil /> Edit & assign
                  </Button>
                  <Button
                    onClick={() => {
                      statusMutation.reset();
                      setStatusTarget(program);
                    }}
                    size="sm"
                    type="button"
                    variant="ghost"
                  >
                    {program.status === "active" ? <Archive /> : <RotateCcw />}
                    {program.status === "active" ? "Archive" : "Restore"}
                  </Button>
                </div>
              </CardContent>
            </Card>
          ))}
        </div>
      ) : null}

      <nav aria-label="Program pages" className="flex items-center justify-between gap-4">
        <p className="text-sm text-muted-foreground">
          Page {page} · {programs.length} shown
          {programsQuery.isFetching && !programsQuery.isLoading ? " · Updating…" : ""}
        </p>
        <div className="flex gap-2">
          <Button disabled={page === 1 || programsQuery.isFetching} onClick={() => setPage((value) => value - 1)} type="button" variant="outline">
            <ChevronLeft /> Previous
          </Button>
          <Button disabled={!hasNext || programsQuery.isFetching} onClick={() => setPage((value) => value + 1)} type="button" variant="outline">
            Next <ChevronRight />
          </Button>
        </div>
      </nav>

      {activeOrgId && editorOpen ? (
        <ProgramEditorDialog
          activeOrgId={activeOrgId}
          onOpenChange={setEditorOpen}
          open
          program={editingProgram}
        />
      ) : null}

      <Dialog onOpenChange={(open) => !open && setStatusTarget(null)} open={Boolean(statusTarget)}>
        <DialogContent>
          <DialogHeader>
            <DialogTitle>{statusTarget?.status === "active" ? "Archive program?" : "Restore program?"}</DialogTitle>
            <DialogDescription>
              {statusTarget?.status === "active"
                ? "The program will leave active selectors. Its record and assignments are retained."
                : "The program will become available in active selectors again."}
            </DialogDescription>
          </DialogHeader>
          {statusMutation.error ? <p className="text-sm text-destructive" role="alert">{errorMessage(statusMutation.error)}</p> : null}
          <DialogFooter>
            <Button onClick={() => setStatusTarget(null)} type="button" variant="ghost">Cancel</Button>
            <Button
              disabled={statusMutation.isPending}
              onClick={() => statusTarget && statusMutation.mutate(statusTarget)}
              type="button"
              variant={statusTarget?.status === "active" ? "destructive" : "default"}
            >
              {statusTarget?.status === "active" ? "Archive" : "Restore"}
            </Button>
          </DialogFooter>
        </DialogContent>
      </Dialog>
    </div>
  );
}

function ProgramMeta({ label, mono = false, value }: { label: string; mono?: boolean; value: string | null | undefined }) {
  return (
    <div className="grid grid-cols-[105px_minmax(0,1fr)] gap-2">
      <dt className="text-muted-foreground">{label}</dt>
      <dd className={mono ? "truncate font-mono text-xs" : "truncate"} title={value ?? undefined}>{value || "—"}</dd>
    </div>
  );
}

function formatDate(value: string) {
  const date = new Date(value);
  return Number.isNaN(date.getTime()) ? value : date.toLocaleDateString();
}

function errorMessage(error: unknown) {
  if (error instanceof ApiError && error.status === 409) {
    return "This program was changed by someone else. Reload the list and retry.";
  }
  return error instanceof Error ? error.message : "The request failed.";
}
