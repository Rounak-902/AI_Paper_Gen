import { NextResponse, type NextRequest } from "next/server";
import type { components } from "@/api/api-types";
import { successEnvelope } from "@/lib/api-envelope";
import {
  BackendApiError,
  backendErrorResponse,
  backendRefreshCookie,
  backendRequest,
  buildBrowserSession,
  forwardRefreshCookie,
} from "@/lib/backend-api";
import { ACTIVE_ORG_COOKIE_NAME, REFRESH_COOKIE_NAME, authCookieOptions } from "@/lib/cookies";

type RefreshOut = components["schemas"]["RefreshOut"];

export async function POST(request: NextRequest) {
  const requestId = request.headers.get("x-request-id") ?? globalThis.crypto.randomUUID();
  const refreshToken = request.cookies.get(REFRESH_COOKIE_NAME)?.value;
  if (!refreshToken) {
    return backendErrorResponse(
      new BackendApiError({
        code: "session_expired",
        message: "Your session has expired. Please sign in again.",
        requestId,
        status: 401,
      }),
    );
  }

  let rotatedRefreshCookie: ReturnType<typeof backendRefreshCookie> = null;
  try {
    const backend = await backendRequest<RefreshOut>("/api/v1/auth/refresh", {
      cookie: forwardRefreshCookie(refreshToken),
      method: "POST",
      requestId,
    });
    rotatedRefreshCookie = backendRefreshCookie(backend.headers);
    if (!rotatedRefreshCookie) {
      throw new BackendApiError({
        code: "missing_refresh_cookie",
        message: "The authentication service did not rotate the refresh session.",
        requestId,
        status: 502,
      });
    }
    const session = await buildBrowserSession(
      backend.data.accessToken,
      backend.data.expiresIn,
      requestId,
    );
    const response = NextResponse.json(successEnvelope(session));
    response.cookies.set(
      REFRESH_COOKIE_NAME,
      rotatedRefreshCookie.value,
      authCookieOptions(rotatedRefreshCookie.maxAge),
    );
    if (session.activeOrgId) {
      response.cookies.set(ACTIVE_ORG_COOKIE_NAME, session.activeOrgId, authCookieOptions());
    } else {
      response.cookies.set(ACTIVE_ORG_COOKIE_NAME, "", { ...authCookieOptions(), maxAge: 0 });
    }
    return response;
  } catch (error) {
    const response = backendErrorResponse(error);
    if (error instanceof BackendApiError && error.status === 401) {
      // A rotated token is not useful when the follow-up principal lookup rejects
      // the account (for example, after curriculum/question data was reset along
      // with the development user). Keeping it would make middleware see a session
      // cookie and redirect /login back to /select-org forever.
      response.cookies.set(REFRESH_COOKIE_NAME, "", { ...authCookieOptions(), maxAge: 0 });
      response.cookies.set(ACTIVE_ORG_COOKIE_NAME, "", { ...authCookieOptions(), maxAge: 0 });
    } else if (rotatedRefreshCookie) {
      // Rotation already consumed the browser's old token. Preserve the new
      // token even if the follow-up profile request failed transiently so the
      // next refresh can recover the session.
      response.cookies.set(
        REFRESH_COOKIE_NAME,
        rotatedRefreshCookie.value,
        authCookieOptions(rotatedRefreshCookie.maxAge),
      );
    }
    return response;
  }
}
