import type { components } from "@/api/api-types";

export type ExportArtifactType = "paper" | "answer_key";
export type ExportFormat = "pdf" | "docx";
export type ExportJob = components["schemas"]["ExportJobOut"];
export type ExportCreateInput = components["schemas"]["ExportCreateIn"];
export type SignedDownload = components["schemas"]["SignedDownloadOut"];

export type PreparedDownload = SignedDownload & {
  expiresAt: number;
};
