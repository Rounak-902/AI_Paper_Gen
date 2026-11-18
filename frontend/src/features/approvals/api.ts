import { apiClient } from "@/services/api-client";
import type {
  ApprovalComment,
  ApprovalHistory,
  ApprovalRequest,
  ApprovalTransitionInput,
  PaperVersionDiff,
} from "@/features/approvals/types";

export const approvalKeys = {
  all: (orgId: string) => ["approvals", orgId] as const,
  queue: (orgId: string) => ["approvals", orgId, "queue"] as const,
  detail: (orgId: string, requestId: string) => ["approvals", orgId, "detail", requestId] as const,
  diff: (orgId: string, paperId: string, versionId: string) => ["approvals", orgId, "diff", paperId, versionId] as const,
};

export function listApprovalQueue(signal?: AbortSignal) {
  return apiClient<ApprovalRequest[]>("/api/v1/approval-requests", { signal });
}

export function listPaperApprovalRounds(paperId: string, signal?: AbortSignal) {
  return apiClient<ApprovalRequest[]>(`/api/v1/papers/${paperId}/approval-requests`, { signal });
}

export function getApprovalHistory(requestId: string, signal?: AbortSignal) {
  return apiClient<ApprovalHistory>(`/api/v1/approval-requests/${requestId}`, { signal });
}

export function getApprovalDiff(paperId: string, versionId: string, signal?: AbortSignal) {
  return apiClient<PaperVersionDiff>(
    `/api/v1/papers/${paperId}/versions/${versionId}/diff`,
    { signal },
  );
}

function transition(requestId: string, action: "review" | "approve" | "reject" | "withdraw-approval", input: ApprovalTransitionInput) {
  return apiClient<ApprovalRequest>(`/api/v1/approval-requests/${requestId}/${action}`, {
    body: input,
    method: "POST",
  });
}

export const startApprovalReview = (request: ApprovalRequest, comment?: string) =>
  transition(request.id, "review", { rowVersion: request.rowVersion, comment });

export const approveApprovalRequest = (request: ApprovalRequest, comment?: string) =>
  transition(request.id, "approve", { rowVersion: request.rowVersion, comment });

export const rejectApprovalRequest = (request: ApprovalRequest, comment: string) =>
  transition(request.id, "reject", { rowVersion: request.rowVersion, comment });

export const withdrawApproval = (request: ApprovalRequest, comment?: string) =>
  transition(request.id, "withdraw-approval", { rowVersion: request.rowVersion, comment });

export function addApprovalComment(requestId: string, input: { slotId?: string | null; body: string }) {
  return apiClient<ApprovalComment>(`/api/v1/approval-requests/${requestId}/comments`, {
    body: input,
    method: "POST",
  });
}
