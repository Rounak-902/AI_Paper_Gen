"use client";

import { useMutation, useQueryClient } from "@tanstack/react-query";
import { AlertCircle, CheckCircle2, Download, FileSpreadsheet, Upload } from "lucide-react";
import { useRouter } from "next/navigation";
import { useMemo, useRef, useState } from "react";
import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import {
  Dialog,
  DialogContent,
  DialogDescription,
  DialogFooter,
  DialogHeader,
  DialogTitle,
  DialogTrigger,
} from "@/components/ui/dialog";
import {
  downloadQuestionImportReport,
  downloadQuestionImportTemplate,
  previewQuestionImport,
  runQuestionImport,
} from "@/features/questions/api";
import {
  IMPORT_TARGET_FIELDS,
  REQUIRED_IMPORT_FIELDS,
  humanize,
  type ColumnMapping,
  type ImportJob,
  type ImportPreview,
  type ImportResult,
} from "@/features/questions/types";
import { useSessionStore } from "@/stores/session";

const MAX_FILE_BYTES = 9 * 1024 * 1024;
const ACCEPTED_EXTENSIONS = [".csv", ".xlsx", ".json"];

export function QuestionImportDialog() {
  const activeOrgId = useSessionStore((state) => state.activeOrgId);
  const queryClient = useQueryClient();
  const router = useRouter();
  const inputRef = useRef<HTMLInputElement>(null);
  const selectedFileRef = useRef<File | null>(null);
  const previewControllerRef = useRef<AbortController | null>(null);
  const importControllerRef = useRef<AbortController | null>(null);
  const submissionIdRef = useRef<string | null>(null);
  const openRef = useRef(false);
  const [open, setOpen] = useState(false);
  const [file, setFile] = useState<File | null>(null);
  const [preview, setPreview] = useState<ImportPreview | null>(null);
  const [mapping, setMapping] = useState<Record<string, string>>({});
  const [result, setResult] = useState<ImportResult | null>(null);
  const [importProgress, setImportProgress] = useState<ImportJob | null>(null);
  const [localError, setLocalError] = useState<string | null>(null);
  const [downloadPending, setDownloadPending] = useState(false);

  const mappingEntries = useMemo<ColumnMapping[]>(
    () =>
      Object.entries(mapping)
        .filter((entry): entry is [string, string] => Boolean(entry[1]))
        .map(([source_column, target_field]) => ({ source_column, target_field })),
    [mapping],
  );
  const mappedTargets = mappingEntries.map((entry) => entry.target_field);
  const missingRequired = REQUIRED_IMPORT_FIELDS.filter((field) => !mappedTargets.includes(field));
  const duplicateTargets = mappedTargets.filter((field, index) => mappedTargets.indexOf(field) !== index);

  const previewMutation = useMutation({
    mutationFn: ({ selectedFile, signal }: { selectedFile: File; signal: AbortSignal }) =>
      previewQuestionImport(activeOrgId!, selectedFile, signal),
    onSuccess: (data, { selectedFile }) => {
      if (selectedFileRef.current !== selectedFile) return;
      const recommended = new Map(
        data.recommended_mapping.map((entry) => [entry.source_column, entry.target_field]),
      );
      setMapping(
        Object.fromEntries(
          data.detected_columns.map((column) => [column, recommended.get(column) ?? ""]),
        ),
      );
      setPreview(data);
      setResult(null);
      setImportProgress(null);
      setLocalError(null);
    },
  });

  const importMutation = useMutation({
    mutationFn: ({
      mapping,
      selectedFile,
      signal,
      submissionId,
    }: {
      mapping: ColumnMapping[];
      selectedFile: File;
      signal: AbortSignal;
      submissionId: string;
    }) => runQuestionImport(activeOrgId!, selectedFile, mapping, submissionId, signal, (job) => {
      if (openRef.current && selectedFileRef.current === selectedFile) setImportProgress(job);
    }),
    onSuccess: (data, { selectedFile }) => {
      if (!openRef.current || selectedFileRef.current !== selectedFile) return;
      setResult(data);
      setImportProgress(null);
      setLocalError(null);
    },
    onError: () => setImportProgress(null),
    onSettled: () => {
      submissionIdRef.current = null;
      void queryClient.invalidateQueries({ queryKey: [activeOrgId, "questions"] });
    },
  });

  function chooseFile(selectedFile: File | null) {
    previewControllerRef.current?.abort();
    previewControllerRef.current = null;
    selectedFileRef.current = selectedFile;
    submissionIdRef.current = null;
    setLocalError(null);
    setPreview(null);
    setResult(null);
    setImportProgress(null);
    previewMutation.reset();
    importMutation.reset();

    if (!selectedFile) {
      setFile(null);
      return;
    }
    const extension = ACCEPTED_EXTENSIONS.find((item) => selectedFile.name.toLowerCase().endsWith(item));
    if (!extension) {
      setFile(null);
      setLocalError("Choose a CSV, XLSX, or JSON file.");
      return;
    }
    if (selectedFile.size > MAX_FILE_BYTES) {
      setFile(null);
      setLocalError("The file must be smaller than 9 MiB so it fits the API request limit.");
      return;
    }
    setFile(selectedFile);
  }

  function previewFile() {
    if (!file) return;
    previewControllerRef.current?.abort();
    const controller = new AbortController();
    previewControllerRef.current = controller;
    previewMutation.mutate({ selectedFile: file, signal: controller.signal });
  }

  function importFile() {
    if (!file) return;
    importControllerRef.current?.abort();
    const controller = new AbortController();
    importControllerRef.current = controller;
    const submissionId = submissionIdRef.current ?? globalThis.crypto.randomUUID();
    submissionIdRef.current = submissionId;
    importMutation.mutate({
      mapping: [...mappingEntries],
      selectedFile: file,
      signal: controller.signal,
      submissionId,
    });
  }

  async function downloadTemplate() {
    if (!activeOrgId) return;
    setDownloadPending(true);
    setLocalError(null);
    try {
      await downloadQuestionImportTemplate(activeOrgId);
    } catch (error) {
      setLocalError(error instanceof Error ? error.message : "The template could not be downloaded.");
    } finally {
      setDownloadPending(false);
    }
  }

  async function downloadReport() {
    if (!activeOrgId || !result?.error_report_token) return;
    setDownloadPending(true);
    setLocalError(null);
    try {
      await downloadQuestionImportReport(activeOrgId, result.error_report_token);
    } catch (error) {
      setLocalError(error instanceof Error ? error.message : "The report could not be downloaded.");
    } finally {
      setDownloadPending(false);
    }
  }

  function resetWizard() {
    previewControllerRef.current?.abort();
    previewControllerRef.current = null;
    importControllerRef.current?.abort();
    importControllerRef.current = null;
    selectedFileRef.current = null;
    submissionIdRef.current = null;
    setFile(null);
    setPreview(null);
    setMapping({});
    setResult(null);
    setImportProgress(null);
    setLocalError(null);
    previewMutation.reset();
    importMutation.reset();
    if (inputRef.current) inputRef.current.value = "";
  }

  function closeWizard() {
    if (importMutation.isPending) return;
    openRef.current = false;
    resetWizard();
    setOpen(false);
  }

  const mutationError = previewMutation.error ?? importMutation.error;
  const errorMessage =
    localError ??
    (isAbortError(mutationError)
      ? null
      : mutationError instanceof Error
        ? mutationError.message
        : mutationError
          ? "The import failed."
          : null);

  return (
    <Dialog
      onOpenChange={(nextOpen) => {
        if (!nextOpen && importMutation.isPending) return;
        openRef.current = nextOpen;
        setOpen(nextOpen);
        if (!nextOpen) resetWizard();
      }}
      open={open}
    >
      <DialogTrigger asChild>
        <Button type="button"><Upload /> Import questions</Button>
      </DialogTrigger>
      <DialogContent className="max-h-[92vh] max-w-5xl overflow-y-auto">
        <DialogHeader>
          <DialogTitle>Import questions</DialogTitle>
          <DialogDescription>
            Preview the file, verify its column mapping, then import valid rows as drafts.
          </DialogDescription>
        </DialogHeader>

        {result ? (
          <ImportResultView
            downloadPending={downloadPending}
            onDownloadReport={() => void downloadReport()}
            onShowDrafts={() => {
              closeWizard();
              router.push("/question-bank?status=draft");
            }}
            result={result}
          />
        ) : (
          <div className="space-y-5">
            <div className="rounded-lg border border-dashed p-5">
              <div className="flex flex-col gap-4 sm:flex-row sm:items-center sm:justify-between">
                <div>
                  <p className="font-medium">CSV, XLSX, or JSON</p>
                  <p className="text-sm text-muted-foreground">Maximum 9 MiB. Use the template for canonical columns.</p>
                </div>
                <Button disabled={downloadPending} onClick={() => void downloadTemplate()} type="button" variant="outline">
                  <Download /> {downloadPending ? "Downloading…" : "Download template"}
                </Button>
              </div>
              <label className="mt-4 grid gap-2 text-sm font-medium">
                <span>Question file</span>
                <input
                  accept=".csv,.xlsx,.json,text/csv,application/json,application/vnd.openxmlformats-officedocument.spreadsheetml.sheet"
                  className="block w-full rounded-md border bg-background p-2 text-sm file:mr-3 file:rounded file:border-0 file:bg-secondary file:px-3 file:py-1.5 file:font-medium"
                  disabled={importMutation.isPending}
                  onChange={(event) => chooseFile(event.target.files?.[0] ?? null)}
                  ref={inputRef}
                  type="file"
                />
              </label>
              {file ? (
                <p className="mt-3 flex items-center gap-2 text-sm">
                  <FileSpreadsheet className="h-4 w-4 text-primary" />
                  {file.name} · {formatBytes(file.size)}
                </p>
              ) : null}
            </div>

            {preview ? (
              <ImportPreviewView mapping={mapping} preview={preview} setMapping={setMapping} />
            ) : null}

            {preview && (missingRequired.length > 0 || duplicateTargets.length > 0) ? (
              <div className="rounded-md border border-amber-500/40 bg-amber-500/10 p-3 text-sm" role="alert">
                {missingRequired.length > 0
                  ? "Map required fields: " + missingRequired.map(humanize).join(", ") + ". "
                  : ""}
                {duplicateTargets.length > 0
                  ? "Each target field can be mapped only once: " + [...new Set(duplicateTargets)].map(humanize).join(", ") + "."
                  : ""}
              </div>
            ) : null}

            {importProgress ? (
              <div className="rounded-md border bg-muted/40 p-3 text-sm" role="status">
                <div className="flex items-center justify-between gap-4">
                  <span className="font-medium">
                    {importProgress.status === "queued" ? "Import queued" : "Importing questions"}
                  </span>
                  <span className="tabular-nums text-muted-foreground">
                    {importProgress.processed_rows.toLocaleString()} committed /{" "}
                    {importProgress.total_rows.toLocaleString()}
                  </span>
                </div>
                <div className="mt-2 h-2 overflow-hidden rounded-full bg-muted">
                  <div
                    className="h-full rounded-full bg-primary transition-[width]"
                    style={{
                      width: `${importProgress.total_rows > 0
                        ? Math.round((importProgress.processed_rows / importProgress.total_rows) * 100)
                        : 100}%`,
                    }}
                  />
                </div>
                <p className="mt-2 text-xs text-muted-foreground">
                  The import continues as a background job without a browser request timeout.
                </p>
              </div>
            ) : null}
          </div>
        )}

        {errorMessage ? (
          <div className="flex gap-2 rounded-md border border-destructive/30 bg-destructive/5 p-3 text-sm text-destructive" role="alert">
            <AlertCircle className="mt-0.5 h-4 w-4 shrink-0" />
            <span>{errorMessage}</span>
          </div>
        ) : null}

        {!result ? (
          <DialogFooter>
            <Button disabled={importMutation.isPending} onClick={closeWizard} type="button" variant="ghost">Cancel</Button>
            {!preview ? (
              <Button
                disabled={!file || previewMutation.isPending || !activeOrgId}
                onClick={previewFile}
                type="button"
              >
                {previewMutation.isPending ? "Reading file…" : "Preview columns"}
              </Button>
            ) : (
              <Button
                disabled={
                  !file ||
                  importMutation.isPending ||
                  missingRequired.length > 0 ||
                  duplicateTargets.length > 0
                }
                onClick={importFile}
                type="button"
              >
                {importMutation.isPending
                  ? importProgress
                    ? `Importing ${importProgress.processed_rows}/${importProgress.total_rows}…`
                    : "Starting import…"
                  : "Import as drafts"}
              </Button>
            )}
          </DialogFooter>
        ) : (
          <DialogFooter>
            <Button onClick={resetWizard} type="button" variant="outline">Import another file</Button>
            <Button onClick={closeWizard} type="button">Done</Button>
          </DialogFooter>
        )}
      </DialogContent>
    </Dialog>
  );
}

function ImportPreviewView({
  mapping,
  preview,
  setMapping,
}: {
  mapping: Record<string, string>;
  preview: ImportPreview;
  setMapping: (mapping: Record<string, string>) => void;
}) {
  return (
    <div className="space-y-4">
      <div>
        <h3 className="font-semibold">Map columns</h3>
        <p className="text-sm text-muted-foreground">
          {preview.row_count_estimate === null || preview.row_count_estimate === undefined
            ? "Row count unavailable"
            : preview.row_count_estimate.toLocaleString() + " rows detected"}
        </p>
      </div>
      <div className="grid gap-3 rounded-lg border p-4 sm:grid-cols-2 lg:grid-cols-3">
        {preview.detected_columns.map((column) => (
          <label className="grid gap-1 text-xs font-medium text-muted-foreground" key={column}>
            <span className="truncate" title={column}>{column}</span>
            <select
              className="h-10 rounded-md border bg-background px-3 text-sm text-foreground"
              onChange={(event) => setMapping({ ...mapping, [column]: event.target.value })}
              value={mapping[column] ?? ""}
            >
              <option value="">Ignore column</option>
              {IMPORT_TARGET_FIELDS.map((field) => (
                <option key={field} value={field}>{humanize(field)}</option>
              ))}
            </select>
          </label>
        ))}
      </div>
      <div className="overflow-x-auto rounded-lg border">
        <table className="w-full min-w-max text-sm">
          <caption className="sr-only">First rows from the selected import file</caption>
          <thead className="bg-muted">
            <tr>
              {preview.detected_columns.map((column) => (
                <th className="max-w-[260px] px-3 py-2 text-left font-medium" key={column}>{column}</th>
              ))}
            </tr>
          </thead>
          <tbody>
            {preview.preview_rows.map((row, index) => (
              <tr className="border-t" key={index}>
                {preview.detected_columns.map((column) => (
                  <td className="max-w-[260px] truncate px-3 py-2" key={column} title={displayValue(row[column])}>
                    {displayValue(row[column])}
                  </td>
                ))}
              </tr>
            ))}
          </tbody>
        </table>
      </div>
    </div>
  );
}

function ImportResultView({
  downloadPending,
  onDownloadReport,
  onShowDrafts,
  result,
}: {
  downloadPending: boolean;
  onDownloadReport: () => void;
  onShowDrafts: () => void;
  result: ImportResult;
}) {
  const notableRows = result.rows.filter((row) => row.status !== "created").slice(0, 50);
  return (
    <div className="space-y-5">
      <div className="flex gap-3 rounded-lg border border-primary/30 bg-primary/5 p-4">
        <CheckCircle2 className="mt-0.5 h-5 w-5 text-primary" />
        <div>
          <h3 className="font-semibold">Import complete</h3>
          <p className="text-sm text-muted-foreground">Valid questions were saved as drafts for review.</p>
        </div>
      </div>
      <dl className="grid gap-3 sm:grid-cols-2 lg:grid-cols-5">
        <ResultCount label="Total" value={result.total_rows} />
        <ResultCount label="Created" value={result.created_count} />
        <ResultCount label="Warnings" value={result.created_with_warnings_count} />
        <ResultCount label="Duplicates" value={result.duplicate_count} />
        <ResultCount label="Errors" value={result.error_count} />
      </dl>
      <div className="flex flex-wrap gap-2">
        <Button onClick={onShowDrafts} type="button" variant="outline">Review imported drafts</Button>
        {result.error_report_token ? (
          <Button disabled={downloadPending} onClick={onDownloadReport} type="button" variant="outline">
            <Download /> {downloadPending ? "Downloading…" : "Download row report"}
          </Button>
        ) : null}
      </div>
      {notableRows.length > 0 ? (
        <div className="overflow-x-auto rounded-lg border">
          <table className="w-full text-sm">
            <thead className="bg-muted">
              <tr>
                <th className="px-3 py-2 text-left">Row</th>
                <th className="px-3 py-2 text-left">Outcome</th>
                <th className="px-3 py-2 text-left">Details</th>
              </tr>
            </thead>
            <tbody>
              {notableRows.map((row) => (
                <tr className="border-t" key={row.row_number}>
                  <td className="px-3 py-2">{row.row_number}</td>
                  <td className="px-3 py-2"><Badge variant={row.status === "error" ? "destructive" : "outline"}>{humanize(row.status)}</Badge></td>
                  <td className="px-3 py-2 text-muted-foreground">
                    {row.errors?.join(" · ") ||
                      row.warnings?.map((warning) => warning.stem_preview).join(" · ") ||
                      "No additional details"}
                  </td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      ) : null}
    </div>
  );
}

function ResultCount({ label, value }: { label: string; value: number }) {
  return (
    <div className="rounded-lg border bg-card p-3">
      <dt className="text-xs font-medium uppercase text-muted-foreground">{label}</dt>
      <dd className="mt-1 text-2xl font-semibold">{value.toLocaleString()}</dd>
    </div>
  );
}

function displayValue(value: unknown) {
  if (value === null || value === undefined) return "";
  return typeof value === "string" ? value : JSON.stringify(value);
}

function formatBytes(value: number) {
  if (value < 1024) return value + " B";
  if (value < 1024 * 1024) return (value / 1024).toFixed(1) + " KiB";
  return (value / (1024 * 1024)).toFixed(1) + " MiB";
}

function isAbortError(error: unknown) {
  return error instanceof DOMException && error.name === "AbortError";
}
