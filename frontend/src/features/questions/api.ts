import { ApiError, apiClient, apiClientWithMeta, downloadApiFile } from "@/services/api-client";
import type {
  Board,
  Chapter,
  ColumnMapping,
  ImportPreview,
  ImportJob,
  ImportResult,
  Question,
  QuestionFilters,
  SchoolClass,
  Subject,
} from "@/features/questions/types";
import { questionSearchParams } from "@/features/questions/query-params";

export function listQuestions(orgId: string, filters: QuestionFilters, signal?: AbortSignal) {
  return apiClientWithMeta<Question[]>(
    `/api/v1/orgs/${orgId}/questions?${questionSearchParams(filters).toString()}`,
    { signal, timeoutMs: 60_000 },
  );
}

export function getQuestion(orgId: string, questionId: string, signal?: AbortSignal) {
  return apiClient<Question>(`/api/v1/orgs/${orgId}/questions/${questionId}`, { signal, timeoutMs: 60_000 });
}

export function getQuestionLineage(orgId: string, questionId: string, signal?: AbortSignal) {
  return apiClient<Question[]>(`/api/v1/orgs/${orgId}/questions/${questionId}/lineage`, { signal, timeoutMs: 60_000 });
}

export function previewQuestionImport(orgId: string, file: File, signal?: AbortSignal) {
  const body = new FormData();
  body.set("file", file);
  return apiClient<ImportPreview>(`/api/v1/orgs/${orgId}/questions/import/preview`, {
    body,
    method: "POST",
    signal,
    timeoutMs: 300_000,
  });
}

export function runQuestionImport(
  orgId: string,
  file: File,
  mapping: ColumnMapping[],
  submissionId: string,
  signal?: AbortSignal,
  onProgress?: (job: ImportJob) => void,
) {
  const body = new FormData();
  body.set("file", file);
  body.set("mapping", JSON.stringify(mapping));
  body.set("submission_id", submissionId);
  return submitQuestionImport(orgId, body, submissionId, signal).then((job) =>
    waitForQuestionImport(orgId, job, signal, onProgress),
  );
}

async function submitQuestionImport(
  orgId: string,
  body: FormData,
  submissionId: string,
  signal?: AbortSignal,
) {
  try {
    return await apiClient<ImportJob>(`/api/v1/orgs/${orgId}/questions/import`, {
      body,
      method: "POST",
      signal,
      timeoutMs: 60_000,
    });
  } catch (error) {
    if (!(error instanceof ApiError) || error.code !== "request_timeout") throw error;
    return waitForSubmittedImport(orgId, submissionId, signal, error);
  }
}

async function waitForSubmittedImport(
  orgId: string,
  submissionId: string,
  signal: AbortSignal | undefined,
  timeoutError: ApiError,
): Promise<ImportJob> {
  const deadline = Date.now() + 15 * 60_000;
  while (Date.now() < deadline) {
    await abortableDelay(2_000, signal);
    try {
      return await apiClient<ImportJob>(
        `/api/v1/orgs/${orgId}/questions/import-jobs/${submissionId}`,
        { signal, timeoutMs: 60_000 },
      );
    } catch (error) {
      if (
        error instanceof ApiError &&
        (error.status === 404 || error.code === "request_timeout" || error.code === "network_error")
      ) {
        continue;
      }
      throw error;
    }
  }
  throw timeoutError;
}

async function waitForQuestionImport(
  orgId: string,
  initialJob: ImportJob,
  signal?: AbortSignal,
  onProgress?: (job: ImportJob) => void,
): Promise<ImportResult> {
  let job = initialJob;
  const deadline = Date.now() + 15 * 60_000;
  onProgress?.(job);
  while (
    (job.status === "queued" || job.status === "processing") &&
    Date.now() < deadline
  ) {
    await abortableDelay(1_000, signal);
    try {
      job = await apiClient<ImportJob>(
        `/api/v1/orgs/${orgId}/questions/import-jobs/${job.id}`,
        { signal, timeoutMs: 30_000 },
      );
    } catch (error) {
      if (
        error instanceof ApiError &&
        (error.code === "request_timeout" || error.code === "network_error")
      ) {
        continue;
      }
      throw error;
    }
    onProgress?.(job);
  }
  if (job.status === "queued" || job.status === "processing") {
    throw new ApiError({
      code: "question_import_status_timeout",
      message:
        "The import is still running in the background. Close this dialog and reopen the question bank before retrying.",
      retryable: true,
      status: 0,
    });
  }
  if (job.status === "failed") {
    throw new Error(job.error || "The question import failed in the background worker.");
  }
  return {
    total_rows: job.total_rows,
    created_count: job.created_count,
    created_with_warnings_count: job.created_with_warnings_count,
    error_count: job.error_count,
    duplicate_count: job.duplicate_count,
    rows: job.rows ?? [],
    error_report_token: job.error_report_token,
  };
}

function abortableDelay(milliseconds: number, signal?: AbortSignal) {
  return new Promise<void>((resolve, reject) => {
    if (signal?.aborted) {
      reject(new DOMException("The import wait was cancelled.", "AbortError"));
      return;
    }
    const onAbort = () => {
      window.clearTimeout(timeout);
      reject(new DOMException("The import wait was cancelled.", "AbortError"));
    };
    const timeout = window.setTimeout(() => {
      signal?.removeEventListener("abort", onAbort);
      resolve();
    }, milliseconds);
    signal?.addEventListener("abort", onAbort, { once: true });
  });
}

export function downloadQuestionImportTemplate(orgId: string) {
  return downloadApiFile(
    `/api/v1/orgs/${orgId}/questions/import/template.csv`,
    "question_import_template.csv",
    { timeoutMs: 30_000 },
  );
}

export function downloadQuestionImportReport(orgId: string, token: string) {
  return downloadApiFile(
    `/api/v1/orgs/${orgId}/questions/import/report/${encodeURIComponent(token)}`,
    "import_error_report.csv",
    { timeoutMs: 30_000 },
  );
}

export function listBoards(signal?: AbortSignal) {
  return listAllCatalogPages<Board>("/api/v1/boards", signal);
}

export function listClasses(boardId: string, signal?: AbortSignal) {
  return listAllCatalogPages<SchoolClass>(
    `/api/v1/classes?boardId=${encodeURIComponent(boardId)}`,
    signal,
  );
}

export function listSubjects(classId: string, signal?: AbortSignal) {
  return listAllCatalogPages<Subject>(
    `/api/v1/subjects?classId=${encodeURIComponent(classId)}`,
    signal,
  );
}

export function listChapters(subjectId: string, signal?: AbortSignal) {
  return listAllCatalogPages<Chapter>(
    `/api/v1/chapters?subjectId=${encodeURIComponent(subjectId)}`,
    signal,
  );
}

async function listAllCatalogPages<T>(path: string, signal?: AbortSignal) {
  const items: T[] = [];
  const separator = path.includes("?") ? "&" : "?";

  for (let page = 1; ; page += 1) {
    const response = await apiClientWithMeta<T[]>(
      `${path}${separator}page=${page}&pageSize=100`,
      { signal, timeoutMs: 60_000 },
    );
    items.push(...response.data);

    if (!response.meta?.pagination?.hasNext || response.data.length === 0) {
      return items;
    }
  }
}
