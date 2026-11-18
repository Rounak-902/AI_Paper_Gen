import { NextResponse, type NextRequest } from "next/server";
import type { components } from "@/api/api-types";
import { successEnvelope } from "@/lib/api-envelope";
import {
  BackendApiError,
  backendErrorResponse,
  backendRequest,
  buildBrowserSession,
} from "@/lib/backend-api";
import { ACTIVE_ORG_COOKIE_NAME, REFRESH_COOKIE_NAME, authCookieOptions } from "@/lib/cookies";

type SwitchOrgOut = components["schemas"]["SwitchOrgOut"];

export async function POST(request: NextRequest) {
  const requestId = request.headers.get("x-request-id") ?? globalThis.crypto.randomUUID();
  const body = await request.json().catch(() => null);
  const orgId = typeof body?.orgId === "string" ? body.orgId : "";
  const authorization = request.headers.get("authorization");
  const accessToken = authorization?.match(/^Bearer\s+(.+)$/i)?.[1];
  const hasRefreshSession = Boolean(request.cookies.get(REFRESH_COOKIE_NAME)?.value);
  if (!orgId || !accessToken || !hasRefreshSession) {
    return backendErrorResponse(
      new BackendApiError({
        code: accessToken && hasRefreshSession ? "validation_error" : "session_expired",
        message:
          accessToken && hasRefreshSession
            ? "Choose an organization."
            : "Your session has expired. Please sign in again.",
        requestId,
        status: accessToken && hasRefreshSession ? 422 : 401,
      }),
    );
  }

  try {
    const backend = await backendRequest<SwitchOrgOut>("/api/v1/auth/switch-org", {
      accessToken,
      body: { orgId },
      method: "POST",
      requestId,
    });
    const session = await buildBrowserSession(
      backend.data.accessToken,
      backend.data.expiresIn,
      requestId,
    );
    const response = NextResponse.json(successEnvelope(session));
    response.cookies.set(ACTIVE_ORG_COOKIE_NAME, session.activeOrgId ?? orgId, authCookieOptions());
    return response;
  } catch (error) {
    return backendErrorResponse(error);
  }
}
