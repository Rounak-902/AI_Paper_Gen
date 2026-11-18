import type { Role } from "@/types/auth";

export type AccessTokenClaims = {
  userId: string;
  activeOrgId: string | null;
  role: Role | null;
  iat: number;
  exp: number;
};

export function decodeAccessTokenClaims(token: string): AccessTokenClaims | null {
  const [, payload] = token.split(".");

  if (!payload || typeof globalThis.atob !== "function") {
    return null;
  }

  try {
    const normalized = payload.replace(/-/g, "+").replace(/_/g, "/");
    const padded = normalized.padEnd(
      normalized.length + ((4 - (normalized.length % 4)) % 4),
      "=",
    );

    return JSON.parse(globalThis.atob(padded)) as AccessTokenClaims;
  } catch {
    return null;
  }
}
