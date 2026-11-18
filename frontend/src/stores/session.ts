import { create } from "zustand";
import { decodeAccessTokenClaims } from "@/lib/jwt";
import type { AuthSessionResponse, AuthUser, OrgMembership, Role, SessionStatus } from "@/types/auth";

type SessionState = {
  status: SessionStatus;
  accessToken: string | null;
  user: AuthUser | null;
  role: Role | null;
  activeOrgId: string | null;
  orgs: OrgMembership[];
  expiresAt: number | null;
  bootstrapError: string | null;
  setLoading: () => void;
  setBootstrapError: (message: string) => void;
  setAuthenticated: (session: AuthSessionResponse) => void;
  setUnauthenticated: () => void;
  updateAccessToken: (accessToken: string) => void;
};

const emptySession = {
  accessToken: null,
  activeOrgId: null,
  expiresAt: null,
  orgs: [],
  role: null,
  user: null,
};

export const useSessionStore = create<SessionState>((set) => ({
  ...emptySession,
  bootstrapError: null,
  status: "loading",
  setLoading: () => set({ status: "loading" }),
  setBootstrapError: (message) => set({ bootstrapError: message }),
  setAuthenticated: (session) =>
    set({
      accessToken: session.accessToken,
      activeOrgId: session.activeOrgId,
      expiresAt: session.expiresAt,
      orgs: session.orgs,
      role: session.role,
      bootstrapError: null,
      status: "authenticated",
      user: session.user,
    }),
  setUnauthenticated: () =>
    set({
      ...emptySession,
      bootstrapError: null,
      status: "unauthenticated",
    }),
  updateAccessToken: (accessToken) => {
    const claims = decodeAccessTokenClaims(accessToken);

    set({
      accessToken,
      activeOrgId: claims?.activeOrgId ?? null,
      expiresAt: claims ? claims.exp * 1000 : null,
      role: claims?.role ?? null,
    });
  },
}));
