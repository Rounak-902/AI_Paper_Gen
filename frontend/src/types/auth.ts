export type Role = "teacher" | "coaching_admin" | "super_admin";

export type SessionStatus = "loading" | "authenticated" | "unauthenticated";

export type AuthUser = {
  id: string;
  name: string;
  email: string;
};

export type OrgMembership = {
  id: string;
  name: string;
  role: Role;
};

export type AuthSessionResponse = {
  accessToken: string;
  user: AuthUser;
  role: Role | null;
  activeOrgId: string | null;
  orgs: OrgMembership[];
  expiresAt: number;
};
