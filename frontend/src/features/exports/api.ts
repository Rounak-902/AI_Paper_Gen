import { apiClient } from "@/services/api-client";
import type {
  ExportArtifactType,
  ExportCreateInput,
  ExportJob,
  PreparedDownload,
  SignedDownload,
} from "@/features/exports/types";

const versionExportsPath = (paperId: string, versionId: string) =>
  `/api/v1/papers/${paperId}/versions/${versionId}/exports`;

export function createExport(paperId: string, versionId: string, input: ExportCreateInput) {
  return apiClient<ExportJob>(versionExportsPath(paperId, versionId), {
    method: "POST",
    body: input,
  });
}

export function listExports(
  paperId: string,
  versionId: string,
  artifactType: ExportArtifactType,
  signal?: AbortSignal,
) {
  const query = new URLSearchParams({ artifactType });
  return apiClient<ExportJob[]>(`${versionExportsPath(paperId, versionId)}?${query}`, { signal });
}

export function getExport(exportId: string, signal?: AbortSignal) {
  return apiClient<ExportJob>(`/api/v1/papers/exports/${exportId}`, { signal });
}

export async function prepareExportDownload(exportId: string): Promise<PreparedDownload> {
  const signed = await apiClient<SignedDownload>(`/api/v1/papers/exports/${exportId}/download`, {
    method: "POST",
  });
  return {
    ...signed,
    expiresAt: Date.now() + signed.expiresInSeconds * 1000,
  };
}

export class SignedDownloadExpiredError extends Error {
  constructor() {
    super("The signed download link expired.");
    this.name = "SignedDownloadExpiredError";
  }
}

export async function savePreparedDownload(
  prepared: PreparedDownload,
  filename: string,
  refresh: () => Promise<PreparedDownload>,
): Promise<{ prepared: PreparedDownload; refreshed: boolean }> {
  let current = prepared;
  let refreshed = false;

  if (Date.now() >= current.expiresAt) {
    current = await refresh();
    refreshed = true;
  }

  let response = await fetch(current.url, { credentials: "omit" });
  if (response.status === 403 && !refreshed) {
    current = await refresh();
    refreshed = true;
    response = await fetch(current.url, { credentials: "omit" });
  }
  if (response.status === 403) throw new SignedDownloadExpiredError();
  if (!response.ok) throw new Error(`The artifact download failed with status ${response.status}.`);

  const blobUrl = URL.createObjectURL(await response.blob());
  const anchor = document.createElement("a");
  anchor.href = blobUrl;
  anchor.download = filename;
  document.body.append(anchor);
  anchor.click();
  anchor.remove();
  window.setTimeout(() => URL.revokeObjectURL(blobUrl), 0);
  return { prepared: current, refreshed };
}
