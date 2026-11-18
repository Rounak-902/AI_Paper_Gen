"use client";

import { zodResolver } from "@hookform/resolvers/zod";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import {
  AlertCircle,
  CheckCircle2,
  Clock3,
  Download,
  FileDown,
  FileKey2,
  FileText,
  Loader2,
  RefreshCw,
} from "lucide-react";
import { useEffect, useMemo, useState } from "react";
import { useForm } from "react-hook-form";
import { Alert } from "@/components/ui/alert";
import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import {
  Dialog,
  DialogContent,
  DialogDescription,
  DialogFooter,
  DialogHeader,
  DialogTitle,
} from "@/components/ui/dialog";
import { cn } from "@/lib/utils";
import { listTemplates } from "@/features/templates/api";
import type { Role } from "@/types/auth";
import { canAccessAnswerKeyExports } from "@/features/exports/policy";
import {
  createExport,
  listExports,
  prepareExportDownload,
  savePreparedDownload,
  SignedDownloadExpiredError,
} from "@/features/exports/api";
import { exportFormSchema, type ExportFormValues } from "@/features/exports/schema";
import type { ExportArtifactType, ExportJob, PreparedDownload } from "@/features/exports/types";

type ExportDialogProps = {
  open: boolean;
  onOpenChange: (open: boolean) => void;
  orgId: string;
  paperId: string;
  versionId: string;
  paperTitle: string;
  role: Role | null;
};

type DownloadState = {
  prepared?: PreparedDownload;
  phase: "idle" | "preparing" | "ready" | "downloading" | "expired" | "error";
  message?: string;
};

const exportKeys = {
  history: (orgId: string, paperId: string, versionId: string, artifactType: ExportArtifactType) =>
    ["exports", orgId, paperId, versionId, artifactType] as const,
};

export function ExportDialog({
  open,
  onOpenChange,
  orgId,
  paperId,
  versionId,
  paperTitle,
  role,
}: ExportDialogProps) {
  const queryClient = useQueryClient();
  const canAccessAnswerKey = canAccessAnswerKeyExports(role);
  const [historyPurpose, setHistoryPurpose] = useState<ExportArtifactType>("paper");
  const [downloadStates, setDownloadStates] = useState<Record<string, DownloadState>>({});
  const [now, setNow] = useState(() => Date.now());
  const form = useForm<ExportFormValues>({
    resolver: zodResolver(exportFormSchema),
    defaultValues: { templateId: "", format: "pdf", contents: "paper" },
  });

  const templates = useQuery({
    queryKey: ["templates", orgId, "export-picker"],
    queryFn: ({ signal }) => listTemplates(orgId, 1, 100, "active", signal),
    enabled: open,
    staleTime: 60_000,
  });
  const activeTemplates = useMemo(() => templates.data?.data ?? [], [templates.data?.data]);

  useEffect(() => {
    if (!open || form.getValues("templateId") || activeTemplates.length === 0) return;
    form.setValue("templateId", (activeTemplates.find((item) => item.isDefault) ?? activeTemplates[0]).id);
  }, [activeTemplates, form, open]);

  useEffect(() => {
    if (!canAccessAnswerKey) {
      form.setValue("contents", "paper");
      setHistoryPurpose("paper");
    }
  }, [canAccessAnswerKey, form]);

  useEffect(() => {
    if (!open) return;
    const timer = window.setInterval(() => setNow(Date.now()), 1_000);
    return () => window.clearInterval(timer);
  }, [open]);

  useEffect(() => {
    setDownloadStates((current) => {
      let changed = false;
      const next = { ...current };
      for (const [id, state] of Object.entries(current)) {
        if (state.phase === "ready" && state.prepared && now >= state.prepared.expiresAt) {
          next[id] = {
            ...state,
            phase: "expired",
            message: "This signed link expired. Refresh it to download; the completed export is still available.",
          };
          changed = true;
        }
      }
      return changed ? next : current;
    });
  }, [now]);

  const history = useQuery({
    queryKey: exportKeys.history(orgId, paperId, versionId, historyPurpose),
    queryFn: ({ signal }) => listExports(paperId, versionId, historyPurpose, signal),
    enabled: open,
    refetchInterval: (query) => {
      const jobs = query.state.data as ExportJob[] | undefined;
      return jobs?.some((job) => job.status === "queued" || job.status === "processing") ? 1_500 : false;
    },
  });
  const hasActiveJob = history.data?.some((job) => job.status === "queued" || job.status === "processing") ?? false;

  const createMutation = useMutation({
    mutationFn: async (values: ExportFormValues) => {
      const jobs = [
        await createExport(paperId, versionId, {
          templateId: values.templateId,
          format: values.format,
          artifactType: "paper",
        }),
      ];
      if (values.contents === "paper_and_answer_key") {
        jobs.push(await createExport(paperId, versionId, {
          templateId: values.templateId,
          format: "docx",
          artifactType: "answer_key",
        }));
      }
      return jobs;
    },
    onSuccess: (jobs) => {
      setHistoryPurpose(jobs.length > 1 ? "answer_key" : "paper");
    },
    onSettled: async () => {
      await Promise.all([
        queryClient.invalidateQueries({ queryKey: exportKeys.history(orgId, paperId, versionId, "paper") }),
        queryClient.invalidateQueries({ queryKey: exportKeys.history(orgId, paperId, versionId, "answer_key") }),
      ]);
    },
  });

  const setDownloadState = (id: string, state: DownloadState) => {
    setDownloadStates((current) => ({ ...current, [id]: state }));
  };

  const prepareDownload = async (job: ExportJob) => {
    setDownloadState(job.id, { phase: "preparing" });
    try {
      const prepared = await prepareExportDownload(job.id);
      setDownloadState(job.id, { phase: "ready", prepared });
    } catch (error) {
      setDownloadState(job.id, { phase: "error", message: errorMessage(error) });
    }
  };

  const download = async (job: ExportJob, prepared: PreparedDownload) => {
    setDownloadState(job.id, { phase: "downloading", prepared });
    try {
      const result = await savePreparedDownload(
        prepared,
        exportFilename(paperTitle, job),
        () => prepareExportDownload(job.id),
      );
      setDownloadState(job.id, {
        phase: "ready",
        prepared: result.prepared,
        message: result.refreshed ? "The expired link was refreshed automatically before download." : "Download started.",
      });
    } catch (error) {
      setDownloadState(job.id, {
        phase: error instanceof SignedDownloadExpiredError ? "expired" : "error",
        message: errorMessage(error),
      });
    }
  };

  const selectedContents = form.watch("contents");
  const selectedFormat = form.watch("format");
  const sortedHistory = useMemo(
    () => [...(history.data ?? [])].sort((left, right) => right.createdAt.localeCompare(left.createdAt)),
    [history.data],
  );

  return (
    <Dialog open={open} onOpenChange={onOpenChange}>
      <DialogContent className="max-h-[90vh] max-w-3xl overflow-y-auto" data-export-dialog>
        <DialogHeader>
          <DialogTitle>Export this paper</DialogTitle>
          <DialogDescription>
            Exports run in the background against the saved paper version and selected active layout template.
          </DialogDescription>
        </DialogHeader>

        <form className="space-y-4" onSubmit={form.handleSubmit((values) => createMutation.mutate(values))}>
          <div className="grid gap-4 sm:grid-cols-2">
            <label className="text-sm font-medium">Format
              <select className={selectClass} {...form.register("format")}>
                <option value="pdf">PDF</option>
                <option value="docx">DOCX</option>
              </select>
            </label>
            <label className="text-sm font-medium">Layout template
              <select className={selectClass} disabled={templates.isLoading} {...form.register("templateId")}>
                <option value="">Choose an active template</option>
                {activeTemplates.map((template) => (
                  <option key={template.id} value={template.id}>{template.name}{template.isDefault ? " · Org default" : ""}</option>
                ))}
              </select>
              {form.formState.errors.templateId ? <span className="mt-1 block text-xs text-red-700">{form.formState.errors.templateId.message}</span> : null}
            </label>
          </div>

          <fieldset>
            <legend className="mb-2 text-sm font-medium">Contents</legend>
            <div className={cn("grid gap-3", canAccessAnswerKey && "sm:grid-cols-2")}>
              <label className={choiceClass(selectedContents === "paper")}>
                <input className="mt-1" type="radio" value="paper" {...form.register("contents")} />
                <span><strong className="block">Paper only</strong><span className="text-xs text-muted-foreground">One {selectedFormat.toUpperCase()} paper artifact.</span></span>
              </label>
              {canAccessAnswerKey ? (
                <label className={choiceClass(selectedContents === "paper_and_answer_key")} data-answer-key-option>
                  <input className="mt-1" type="radio" value="paper_and_answer_key" {...form.register("contents")} />
                  <span><strong className="block">Paper + answer key</strong><span className="text-xs text-muted-foreground">Separate {selectedFormat.toUpperCase()} paper and DOCX answer-key jobs.</span></span>
                </label>
              ) : null}
            </div>
          </fieldset>

          {templates.error ? <Alert className="border-red-200 bg-red-50 text-red-900">{errorMessage(templates.error)}</Alert> : null}
          {!templates.isLoading && !templates.error && activeTemplates.length === 0 ? (
            <Alert className="border-amber-200 bg-amber-50 text-amber-950">An active layout template is required before export.</Alert>
          ) : null}
          {createMutation.error ? <Alert className="border-red-200 bg-red-50 text-red-900">{errorMessage(createMutation.error)}</Alert> : null}
          {createMutation.isSuccess ? (
            <Alert className="border-emerald-200 bg-emerald-50 text-emerald-900">
              Export queued. Status below refreshes automatically until each artifact is ready.
            </Alert>
          ) : null}

          <DialogFooter>
            <Button disabled={createMutation.isPending || activeTemplates.length === 0} type="submit">
              {createMutation.isPending ? <Loader2 className="animate-spin" /> : <FileDown />}
              {selectedContents === "paper_and_answer_key" ? "Queue 2 exports" : "Queue export"}
            </Button>
          </DialogFooter>
        </form>

        <section className="border-t pt-5" aria-labelledby="export-history-heading">
          <div className="flex flex-wrap items-center justify-between gap-3">
            <div>
              <h3 className="font-semibold" id="export-history-heading">Export history</h3>
              <p className="text-xs text-muted-foreground">Signed links are minted only when requested and are never stored.</p>
            </div>
            <div className="flex rounded-md border bg-slate-50 p-1" role="tablist" aria-label="Artifact purpose">
              <Button aria-selected={historyPurpose === "paper"} onClick={() => setHistoryPurpose("paper")} role="tab" size="sm" type="button" variant={historyPurpose === "paper" ? "secondary" : "ghost"}><FileText />Papers</Button>
              {canAccessAnswerKey ? (
                <Button aria-selected={historyPurpose === "answer_key"} data-answer-key-history-tab onClick={() => setHistoryPurpose("answer_key")} role="tab" size="sm" type="button" variant={historyPurpose === "answer_key" ? "secondary" : "ghost"}><FileKey2 />Answer keys</Button>
              ) : null}
            </div>
          </div>

          {hasActiveJob ? <p className="mt-3 flex items-center gap-2 text-xs text-blue-800"><Loader2 className="h-3.5 w-3.5 animate-spin" />Checking job status every 1.5 seconds.</p> : null}
          {history.isLoading ? <p className="mt-4 flex items-center gap-2 text-sm text-muted-foreground"><Loader2 className="animate-spin" />Loading history…</p> : null}
          {history.error ? <Alert className="mt-4 border-red-200 bg-red-50 text-red-900">{errorMessage(history.error)}</Alert> : null}
          {!history.isLoading && !history.error && sortedHistory.length === 0 ? <p className="mt-4 rounded-md border border-dashed p-5 text-center text-sm text-muted-foreground">No {historyPurpose === "paper" ? "paper" : "answer-key"} exports yet.</p> : null}

          <div className="mt-4 space-y-3">
            {sortedHistory.map((job) => (
              <ExportHistoryRow
                downloadState={downloadStates[job.id] ?? { phase: "idle" }}
                job={job}
                key={job.id}
                now={now}
                onDownload={(prepared) => void download(job, prepared)}
                onPrepare={() => void prepareDownload(job)}
              />
            ))}
          </div>
        </section>
      </DialogContent>
    </Dialog>
  );
}

function ExportHistoryRow({
  downloadState,
  job,
  now,
  onDownload,
  onPrepare,
}: {
  downloadState: DownloadState;
  job: ExportJob;
  now: number;
  onDownload: (prepared: PreparedDownload) => void;
  onPrepare: () => void;
}) {
  const remainingSeconds = downloadState.prepared
    ? Math.max(0, Math.ceil((downloadState.prepared.expiresAt - now) / 1_000))
    : 0;
  return (
    <article className="rounded-lg border bg-white p-3" data-export-id={job.id} data-export-status={job.status}>
      <div className="flex flex-wrap items-start justify-between gap-3">
        <div className="min-w-0">
          <div className="flex flex-wrap items-center gap-2">
            <strong className="text-sm">{job.format.toUpperCase()} {job.artifactType === "answer_key" ? "answer key" : "paper"}</strong>
            <StatusBadge status={job.status} />
          </div>
          <p className="mt-1 text-xs text-muted-foreground">Requested {new Date(job.createdAt).toLocaleString()}</p>
          {job.status === "done" && job.byteSize ? <p className="text-xs text-muted-foreground">{formatBytes(job.byteSize)} · checksum {job.sha256?.slice(0, 10)}…</p> : null}
          {job.status === "failed" ? <p className="mt-1 text-xs text-red-700">{job.error ?? "The renderer did not complete this export."}</p> : null}
        </div>
        {job.status === "done" ? (
          <div className="flex flex-wrap justify-end gap-2">
            {downloadState.phase === "idle" || downloadState.phase === "error" || downloadState.phase === "expired" ? (
              <Button onClick={onPrepare} size="sm" type="button" variant="outline">
                {downloadState.phase === "expired" ? <RefreshCw /> : <Download />}
                {downloadState.phase === "expired" ? "Refresh download link" : "Get download link"}
              </Button>
            ) : null}
            {downloadState.phase === "preparing" ? <Button disabled size="sm" type="button" variant="outline"><Loader2 className="animate-spin" />Preparing link</Button> : null}
            {(downloadState.phase === "ready" || downloadState.phase === "downloading") && downloadState.prepared ? (
              <Button disabled={downloadState.phase === "downloading"} onClick={() => onDownload(downloadState.prepared!)} size="sm" type="button">
                {downloadState.phase === "downloading" ? <Loader2 className="animate-spin" /> : <Download />}
                Download {job.format.toUpperCase()}
              </Button>
            ) : null}
          </div>
        ) : null}
      </div>
      {downloadState.phase === "ready" && downloadState.prepared ? (
        <p className="mt-2 flex items-center gap-1 text-xs text-emerald-800" data-signed-link-state="ready"><Clock3 className="h-3.5 w-3.5" />Signed link ready for {formatDuration(remainingSeconds)}.{downloadState.message ? ` ${downloadState.message}` : ""}</p>
      ) : null}
      {downloadState.phase === "expired" ? <p className="mt-2 flex items-center gap-1 text-xs text-amber-800" data-signed-link-state="expired"><AlertCircle className="h-3.5 w-3.5" />{downloadState.message}</p> : null}
      {downloadState.phase === "error" ? <p className="mt-2 flex items-center gap-1 text-xs text-red-700"><AlertCircle className="h-3.5 w-3.5" />{downloadState.message}</p> : null}
    </article>
  );
}

function StatusBadge({ status }: { status: ExportJob["status"] }) {
  if (status === "done") return <Badge className="bg-emerald-700"><CheckCircle2 />Ready</Badge>;
  if (status === "failed") return <Badge className="bg-red-700"><AlertCircle />Failed</Badge>;
  return <Badge className="bg-blue-700"><Loader2 className="animate-spin" />{status === "queued" ? "Queued" : "Rendering"}</Badge>;
}

function exportFilename(title: string, job: ExportJob) {
  const safeTitle = title.trim().replace(/[^a-z0-9]+/gi, "-").replace(/^-|-$/g, "").toLowerCase() || "paper";
  return `${safeTitle}-${job.artifactType === "answer_key" ? "answer-key" : "paper"}.${job.format}`;
}

function formatBytes(bytes: number) {
  if (bytes < 1_024) return `${bytes} B`;
  if (bytes < 1_048_576) return `${(bytes / 1_024).toFixed(1)} KB`;
  return `${(bytes / 1_048_576).toFixed(1)} MB`;
}

function formatDuration(seconds: number) {
  const minutes = Math.floor(seconds / 60);
  const rest = seconds % 60;
  return minutes ? `${minutes}m ${rest}s` : `${rest}s`;
}

function errorMessage(error: unknown) {
  return error instanceof Error ? error.message : "Something went wrong. Please try again.";
}

const selectClass = "mt-1 block h-9 w-full rounded-md border border-input bg-background px-3 text-sm shadow-sm focus:outline-none focus:ring-1 focus:ring-ring";
const choiceClass = (selected: boolean) => cn(
  "flex cursor-pointer gap-3 rounded-lg border p-3 text-sm transition-colors",
  selected ? "border-primary bg-primary/5 ring-1 ring-primary" : "hover:bg-slate-50",
);
