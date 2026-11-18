import type { ResponseCookie } from "next/dist/compiled/@edge-runtime/cookies";

export const REFRESH_COOKIE_NAME = "papergen_refresh";
export const ACTIVE_ORG_COOKIE_NAME = "papergen_active_org";

export function authCookieOptions(maxAge = 7 * 24 * 60 * 60): Partial<ResponseCookie> {
  const secureSetting = process.env.AUTH_COOKIE_SECURE?.trim().toLowerCase();
  return {
    httpOnly: true,
    maxAge,
    path: "/",
    sameSite: "strict",
    secure: secureSetting
      ? secureSetting !== "false" && secureSetting !== "0"
      : process.env.NODE_ENV === "production",
  };
}
