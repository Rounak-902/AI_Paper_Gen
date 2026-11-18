import type { components } from "@/api/api-types";

export type ApprovalComment = components["schemas"]["ApprovalCommentOut"];
export type ApprovalHistory = components["schemas"]["ApprovalHistoryOut"];
export type ApprovalRequest = components["schemas"]["ApprovalRequestOut"];
export type ApprovalStatus = ApprovalRequest["status"];
export type ApprovalTransitionInput = components["schemas"]["ApprovalTransitionIn"];
export type PaperVersionDiff = components["schemas"]["PaperVersionDiffOut"];

