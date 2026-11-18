import { NextResponse, type NextRequest } from "next/server";
import type { components } from "@/api/api-types";
import { successEnvelope } from "@/lib/api-envelope";
import {
  backendErrorResponse,
  backendRequest,
  forwardRefreshCookie,
} from "@/lib/backend-api";
import { ACTIVE_ORG_COOKIE_NAME, REFRESH_COOKIE_NAME, authCookieOptions } from "@/lib/cookies";

type LogoutOut = components["schemas"]["LogoutOut"];

export async function POST(request: NextRequest) {
  const requestId = request.headers.get("x-request-id") ?? globalThis.crypto.randomUUID();
  const refreshToken = request.cookies.get(REFRESH_COOKIE_NAME)?.value;
  let response: NextResponse;
  try {
    if (refreshToken) {
      await backendRequest<LogoutOut>("/api/v1/auth/logout", {
        cookie: forwardRefreshCookie(refreshToken),
        method: "POST",
        requestId,
      });
    }
    response = NextResponse.json(successEnvelope({ ok: true as const }));
  } catch (error) {
    response = backendErrorResponse(error);
  }
  response.cookies.set(REFRESH_COOKIE_NAME, "", { ...authCookieOptions(), maxAge: 0 });
  response.cookies.set(ACTIVE_ORG_COOKIE_NAME, "", { ...authCookieOptions(), maxAge: 0 });
  return response;
}
