import { NextResponse, type NextRequest } from "next/server";
import type { components } from "@/api/api-types";
import { successEnvelope } from "@/lib/api-envelope";
import {
  BackendApiError,
  backendErrorResponse,
  backendRefreshCookie,
  backendRequest,
  buildBrowserSession,
} from "@/lib/backend-api";
import { ACTIVE_ORG_COOKIE_NAME, REFRESH_COOKIE_NAME, authCookieOptions } from "@/lib/cookies";

type LoginOut = components["schemas"]["LoginOut"];

export async function POST(request: NextRequest) {
  const requestId = request.headers.get("x-request-id") ?? globalThis.crypto.randomUUID();
  const body = await request.json().catch(() => null);
  const email = typeof body?.email === "string" ? body.email.trim().toLowerCase() : "";
  const password = typeof body?.password === "string" ? body.password : "";
  const orgId = typeof body?.orgId === "string" && body.orgId ? body.orgId : undefined;
  if (!email || !password) {
    return backendErrorResponse(
      new BackendApiError({
        code: "validation_error",
        message: "Email and password are required.",
        requestId,
        status: 422,
      }),
    );
  }

  try {
    const backend = await backendRequest<LoginOut>("/api/v1/auth/login", {
      body: { email, password, ...(orgId ? { orgId } : {}) },
      method: "POST",
      requestId,
    });
    const refreshCookie = backendRefreshCookie(backend.headers);
    if (!refreshCookie) {
      throw new BackendApiError({
        code: "missing_refresh_cookie",
        message: "The authentication service did not establish a refresh session.",
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
      refreshCookie.value,
      authCookieOptions(refreshCookie.maxAge),
    );
    if (session.activeOrgId) {
      response.cookies.set(ACTIVE_ORG_COOKIE_NAME, session.activeOrgId, authCookieOptions());
    } else {
      response.cookies.set(ACTIVE_ORG_COOKIE_NAME, "", { ...authCookieOptions(), maxAge: 0 });
    }
    return response;
  } catch (error) {
    return backendErrorResponse(error);
  }
}
