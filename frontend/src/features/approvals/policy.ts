import type { ApprovalRequest } from "@/features/approvals/types";
import type { Role } from "@/types/auth";

export const canManageApprovals = (role: Role | null) =>
  role === "coaching_admin" || role === "super_admin";

export const isSeparationBlocked = (request: ApprovalRequest, userId: string | null | undefined) =>
  request.separationOfDutiesRequired && Boolean(userId) && request.submittedBy === userId;

