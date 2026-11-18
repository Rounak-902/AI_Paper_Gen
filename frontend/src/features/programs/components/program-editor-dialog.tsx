"use client";

import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { AlertTriangle, Copy, Loader2 } from "lucide-react";
import { useEffect, useMemo, useState, type FormEvent } from "react";
import { Button } from "@/components/ui/button";
import {
  Dialog,
  DialogContent,
  DialogDescription,
  DialogFooter,
  DialogHeader,
  DialogTitle,
} from "@/components/ui/dialog";
import { Input } from "@/components/ui/input";
import { Label } from "@/components/ui/label";
import {
  createProgram,
  getProgramAssignments,
  listMemberships,
  replaceProgramSubjects,
  replaceProgramTeachers,
  updateProgram,
} from "@/features/programs/api";
import type { Program, ProgramFormValues } from "@/features/programs/types";
import { ApiError } from "@/services/api-client";

const EMPTY_FORM: ProgramFormValues = {
  academicYear: "",
  branchId: "",
  examTarget: "",
  name: "",
};

export function ProgramEditorDialog({
  activeOrgId,
  onOpenChange,
  open,
  program,
}: {
  activeOrgId: string;
  onOpenChange: (open: boolean) => void;
  open: boolean;
  program: Program | null;
}) {
  const queryClient = useQueryClient();
  const [form, setForm] = useState<ProgramFormValues>(EMPTY_FORM);
  const [teachers, setTeachers] = useState("");
  const [subjects, setSubjects] = useState("");
  const [confirmTeachers, setConfirmTeachers] = useState(false);
  const [confirmSubjects, setConfirmSubjects] = useState(false);
  const [assignmentMessage, setAssignmentMessage] = useState<string | null>(null);
  const editing = Boolean(program);

  useEffect(() => {
    if (!open) return;
    setForm(
      program
        ? {
            academicYear: program.academicYear ?? "",
            branchId: program.branchId ?? "",
            examTarget: program.examTarget ?? "",
            name: program.name,
          }
        : EMPTY_FORM,
    );
    setTeachers("");
    setSubjects("");
    setConfirmTeachers(false);
    setConfirmSubjects(false);
    setAssignmentMessage(null);
  }, [open, program]);

  const membershipsQuery = useQuery({
    queryKey: [activeOrgId, "memberships", "program-teachers"],
    queryFn: ({ signal }) => listMemberships(activeOrgId, signal),
    enabled: open && editing,
    staleTime: 60_000,
  });
  const assignmentsQuery = useQuery({
    queryKey: [activeOrgId, "programs", program?.id, "assignments"],
    queryFn: ({ signal }) => getProgramAssignments(activeOrgId, program!.id, signal),
    enabled: open && editing,
  });
  const teacherCandidates = useMemo(
    () =>
      (membershipsQuery.data ?? []).filter(
        (membership) => membership.role === "teacher" && membership.status === "active",
      ),
    [membershipsQuery.data],
  );
  const assignmentsReady =
    assignmentsQuery.isSuccess &&
    !assignmentsQuery.isFetching &&
    !assignmentsQuery.error;

  useEffect(() => {
    if (!open || !program || !assignmentsQuery.data) return;
    setTeachers(assignmentsQuery.data.teacherIds.join("\n"));
    setSubjects(assignmentsQuery.data.subjectIds.join("\n"));
  }, [assignmentsQuery.data, open, program]);

  const saveMutation = useMutation({
    mutationFn: () => {
      if (program) {
        return updateProgram(activeOrgId, program.id, buildProgramUpdate(program, form));
      }
      const branchId = form.branchId.trim();
      if (branchId && !isUuid(branchId)) {
        throw new Error("Branch ID must be a valid UUID.");
      }
      return createProgram(activeOrgId, {
        academicYear: optional(form.academicYear),
        branchId: optional(branchId),
        examTarget: optional(form.examTarget),
        name: form.name.trim(),
      });
    },
    onSuccess: async () => {
      await queryClient.invalidateQueries({ queryKey: [activeOrgId, "programs"] });
      onOpenChange(false);
    },
  });

  const teacherMutation = useMutation({
    mutationFn: (ids: string[]) => replaceProgramTeachers(activeOrgId, program!.id, ids),
    onSuccess: async () => {
      await queryClient.invalidateQueries({
        queryKey: [activeOrgId, "programs", program!.id, "assignments"],
      });
      setAssignmentMessage("Teacher assignment set replaced.");
      setConfirmTeachers(false);
    },
  });
  const subjectMutation = useMutation({
    mutationFn: (ids: string[]) => replaceProgramSubjects(activeOrgId, program!.id, ids),
    onSuccess: async () => {
      await queryClient.invalidateQueries({
        queryKey: [activeOrgId, "programs", program!.id, "assignments"],
      });
      setAssignmentMessage("Subject assignment set replaced.");
      setConfirmSubjects(false);
    },
  });

  function submit(event: FormEvent) {
    event.preventDefault();
    if (form.name.trim()) saveMutation.mutate();
  }

  function submitTeachers() {
    setAssignmentMessage(null);
    subjectMutation.reset();
    const parsed = parseUuidList(teachers);
    if ("error" in parsed) {
      setAssignmentMessage(parsed.error);
      return;
    }
    teacherMutation.mutate(parsed.ids);
  }

  function submitSubjects() {
    setAssignmentMessage(null);
    teacherMutation.reset();
    const parsed = parseUuidList(subjects);
    if ("error" in parsed) {
      setAssignmentMessage(parsed.error);
      return;
    }
    subjectMutation.mutate(parsed.ids);
  }

  const saveError = mutationMessage(saveMutation.error);
  const assignmentError = mutationMessage(teacherMutation.error ?? subjectMutation.error);

  return (
    <Dialog onOpenChange={onOpenChange} open={open}>
      <DialogContent className="max-h-[92vh] max-w-3xl overflow-y-auto">
        <DialogHeader>
          <DialogTitle>{editing ? "Edit program" : "Create program"}</DialogTitle>
          <DialogDescription>
            Programs group an exam target, academic year, teachers, and subject scope.
          </DialogDescription>
        </DialogHeader>

        <form className="space-y-4" onSubmit={submit}>
          <div className="grid gap-4 sm:grid-cols-2">
            <Field
              label="Program name"
              onChange={(name) => setForm({ ...form, name })}
              required
              value={form.name}
            />
            <Field
              label="Exam target"
              onChange={(examTarget) => setForm({ ...form, examTarget })}
              placeholder="e.g. CBSE Board 2027"
              value={form.examTarget}
            />
            <Field
              label="Academic year"
              onChange={(academicYear) => setForm({ ...form, academicYear })}
              placeholder="e.g. 2026–27"
              value={form.academicYear}
            />
            <Field
              disabled={editing}
              hint={editing ? "Branch placement cannot be changed after creation." : "Optional branch UUID"}
              label="Branch ID"
              onChange={(branchId) => setForm({ ...form, branchId })}
              value={form.branchId}
            />
          </div>
          {saveError ? <InlineError message={saveError} /> : null}
          <DialogFooter>
            <Button onClick={() => onOpenChange(false)} type="button" variant="ghost">Cancel</Button>
            <Button disabled={!form.name.trim() || saveMutation.isPending} type="submit">
              {saveMutation.isPending ? <Loader2 className="animate-spin" /> : null}
              {editing ? "Save changes" : "Create program"}
            </Button>
          </DialogFooter>
        </form>

        {program ? (
          <div className="space-y-4 border-t pt-5">
            <div className="flex gap-3 rounded-md border border-amber-500/40 bg-amber-500/10 p-3 text-sm">
              <AlertTriangle className="mt-0.5 h-4 w-4 shrink-0 text-amber-700" />
              <p>
                The current assignment sets are loaded below. Each submission replaces the
                complete set; an empty confirmed field clears that assignment set.
              </p>
            </div>

            {assignmentsQuery.isFetching ? (
              <p className="flex items-center gap-2 text-sm text-muted-foreground" role="status">
                <Loader2 className="animate-spin" /> Loading current assignments…
              </p>
            ) : null}
            {assignmentsQuery.error ? (
              <InlineError message={assignmentReadMessage(assignmentsQuery.error)} />
            ) : null}

            <AssignmentEditor
              checked={confirmTeachers}
              disabled={!assignmentsReady}
              help="Separate teacher user UUIDs with commas, spaces, or new lines."
              label="Teacher user IDs"
              onCheckedChange={setConfirmTeachers}
              onSubmit={submitTeachers}
              pending={teacherMutation.isPending}
              setValue={setTeachers}
              value={teachers}
            />
            {teacherCandidates.length > 0 ? (
              <div className="rounded-md bg-muted/60 p-3">
                <p className="text-xs font-semibold uppercase text-muted-foreground">Active teacher IDs</p>
                <div className="mt-2 flex flex-wrap gap-2">
                  {teacherCandidates.map((membership) => (
                    <Button
                      key={membership.id}
                      disabled={!assignmentsReady}
                      onClick={() => setTeachers(appendId(teachers, membership.userId))}
                      size="sm"
                      title={"Add " + membership.userId}
                      type="button"
                      variant="outline"
                    >
                      <Copy /> {shortId(membership.userId)}
                    </Button>
                  ))}
                </div>
              </div>
            ) : null}

            <AssignmentEditor
              checked={confirmSubjects}
              disabled={!assignmentsReady}
              help="Separate curriculum subject UUIDs with commas, spaces, or new lines."
              label="Subject IDs"
              onCheckedChange={setConfirmSubjects}
              onSubmit={submitSubjects}
              pending={subjectMutation.isPending}
              setValue={setSubjects}
              value={subjects}
            />
            {assignmentMessage ? <p className="text-sm" role="status">{assignmentMessage}</p> : null}
            {assignmentError ? <InlineError message={assignmentError} /> : null}
          </div>
        ) : null}
      </DialogContent>
    </Dialog>
  );
}

function AssignmentEditor({
  checked,
  disabled,
  help,
  label,
  onCheckedChange,
  onSubmit,
  pending,
  setValue,
  value,
}: {
  checked: boolean;
  disabled: boolean;
  help: string;
  label: string;
  onCheckedChange: (checked: boolean) => void;
  onSubmit: () => void;
  pending: boolean;
  setValue: (value: string) => void;
  value: string;
}) {
  const id = label.toLowerCase().replaceAll(" ", "-");
  return (
    <section className="space-y-2 rounded-lg border p-4">
      <Label htmlFor={id}>{label}</Label>
      <textarea
        aria-describedby={id + "-help"}
        className="min-h-24 w-full rounded-md border bg-background px-3 py-2 font-mono text-xs outline-none focus:ring-2 focus:ring-ring"
        id={id}
        disabled={disabled}
        onChange={(event) => setValue(event.target.value)}
        placeholder="00000000-0000-0000-0000-000000000000"
        value={value}
      />
      <p className="text-xs text-muted-foreground" id={id + "-help"}>{help}</p>
      <label className="flex items-start gap-2 text-sm">
        <input
          checked={checked}
          className="mt-1"
          disabled={disabled}
          onChange={(event) => onCheckedChange(event.target.checked)}
          type="checkbox"
        />
        <span>I understand this replaces the complete assignment set.</span>
      </label>
      <Button disabled={disabled || !checked || pending} onClick={onSubmit} size="sm" type="button" variant="outline">
        {pending ? <Loader2 className="animate-spin" /> : null} Replace assignments
      </Button>
    </section>
  );
}

function Field({
  disabled,
  hint,
  label,
  onChange,
  placeholder,
  required,
  value,
}: {
  disabled?: boolean;
  hint?: string;
  label: string;
  onChange: (value: string) => void;
  placeholder?: string;
  required?: boolean;
  value: string;
}) {
  const id = label.toLowerCase().replaceAll(" ", "-");
  return (
    <div className="grid gap-1.5">
      <Label htmlFor={id}>{label}{required ? " (required)" : ""}</Label>
      <Input
        disabled={disabled}
        id={id}
        maxLength={200}
        onChange={(event) => onChange(event.target.value)}
        placeholder={placeholder}
        required={required}
        value={value}
      />
      {hint ? <p className="text-xs text-muted-foreground">{hint}</p> : null}
    </div>
  );
}

function InlineError({ message }: { message: string }) {
  return <p className="rounded-md border border-destructive/30 bg-destructive/5 p-3 text-sm text-destructive" role="alert">{message}</p>;
}

function optional(value: string) {
  const normalized = value.trim();
  return normalized || null;
}

function buildProgramUpdate(program: Program, form: ProgramFormValues) {
  const name = form.name.trim();
  const examTarget = form.examTarget.trim();
  const academicYear = form.academicYear.trim();
  const payload: Parameters<typeof updateProgram>[2] = { rowVersion: program.rowVersion };
  if (name !== program.name) payload.name = name;
  if (examTarget !== (program.examTarget ?? "")) {
    if (!examTarget) {
      throw new Error("The current API cannot clear an exam target. Keep its value or enter a replacement.");
    }
    payload.examTarget = examTarget;
  }
  if (academicYear !== (program.academicYear ?? "")) {
    if (!academicYear) {
      throw new Error("The current API cannot clear an academic year. Keep its value or enter a replacement.");
    }
    payload.academicYear = academicYear;
  }
  if (Object.keys(payload).length === 1) {
    throw new Error("Change at least one field before saving.");
  }
  return payload;
}

function mutationMessage(error: unknown) {
  if (!error) return null;
  if (error instanceof ApiError && error.status === 409) {
    return "This program changed after you opened it. Close the editor, reload the list, and try again.";
  }
  return error instanceof Error ? error.message : "The program could not be updated.";
}

function assignmentReadMessage(error: unknown) {
  const detail = error instanceof Error ? error.message : "The request failed.";
  return "Current assignments could not be loaded. Replacement is disabled to protect the persisted set. " + detail;
}

function parseUuidList(value: string): { ids: string[] } | { error: string } {
  const ids = [...new Set(value.split(/[\s,]+/).map((item) => item.trim()).filter(Boolean))];
  const invalid = ids.find(
    (id) => !/^[0-9a-f]{8}-[0-9a-f]{4}-[1-8][0-9a-f]{3}-[89ab][0-9a-f]{3}-[0-9a-f]{12}$/i.test(id),
  );
  return invalid ? { error: "Invalid UUID: " + invalid } : { ids };
}

function appendId(value: string, id: string) {
  const parsed = value.split(/[\s,]+/).filter(Boolean);
  return parsed.includes(id) ? value : [...parsed, id].join("\n");
}

function shortId(value: string) {
  return value.slice(0, 8) + "…";
}

function isUuid(value: string) {
  return /^[0-9a-f]{8}-[0-9a-f]{4}-[1-8][0-9a-f]{3}-[89ab][0-9a-f]{3}-[0-9a-f]{12}$/i.test(value);
}
