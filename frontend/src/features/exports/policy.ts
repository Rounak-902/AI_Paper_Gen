import type { Role } from "@/types/auth";

/** Mirrors P6-T4's org-role predicate; the backend remains the security boundary. */
export const canAccessAnswerKeyExports = (role: Role | null) =>
  role === "coaching_admin" || role === "super_admin";
