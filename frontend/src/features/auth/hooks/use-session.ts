"use client";

import { useSessionStore } from "@/stores/session";

export function useSession() {
  return useSessionStore((state) => ({
    activeOrgId: state.activeOrgId,
    orgs: state.orgs,
    role: state.role,
    status: state.status,
    user: state.user,
  }));
}
