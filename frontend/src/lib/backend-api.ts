import { NextResponse } from "next/server";
import type { components } from "@/api/api-types";
import { uniqueOrgMemberships } from "@/features/auth/session";
import type { AuthSessionResponse, Role } from "@/types/auth";

type Principal = components["schemas"]["PrincipalOut"];

type BackendEnvelope<T> = {
  success: boolean;
  data?: T;
  error?: {
    code?: string;
    message?: string;
    details?: unknown;
    ehRef?: string | null;
  };
  meta?: {
    requestId?: string;
  };
};

type BackendRequestOptions = {
  accessToken?: string;
  body?: unknown;
  cookie?: string;
  method?: "GET" | "POST" | "PATCH" | "DELETE";
  requestId?: string;
  timeoutMs?: number;
};

export class BackendApiError extends Error {
  status: number;
  code: string;
  details?: unknown;
  ehRef?: string;
  requestId?: string;

  constructor({
    code,
    details,
    ehRef,
    message,
    requestId,
    status,
  }: {
    status: number;
    code: string;
    message: string;
    details?: unknown;
    ehRef?: string;
    requestId?: string;
  }) {
    super(message);
    this.name = "BackendApiError";
    this.status = status;
    this.code = code;
    this.details = details;
    this.ehRef = ehRef;
    this.requestId = requestId;
  }
}

export async function backendRequest<T>(
  path: string,
  options: BackendRequestOptions = {},
): Promise<{ data: T; headers: Headers }> {
  const controller = new AbortController();
  const timeout = setTimeout(() => controller.abort(), options.timeoutMs ?? 20_000);
  const requestId = options.requestId ?? globalThis.crypto.randomUUID();
  try {
    const response = await fetch(backendUrl(path), {
      body: options.body === undefined ? undefined : JSON.stringify(options.body),
      cache: "no-store",
      headers: {
        Accept: "application/json",
        ...(options.body === undefined ? {} : { "Content-Type": "application/json" }),
        ...(options.accessToken ? { Authorization: "Bearer " + options.accessToken } : {}),
        ...(options.cookie ? { Cookie: options.cookie } : {}),
        "X-Request-Id": requestId,
      },
      method: options.method ?? "GET",
      signal: controller.signal,
    });
    const envelope = (await response.json().catch(() => null)) as BackendEnvelope<T> | null;
    if (!response.ok || !envelope?.success || envelope.data === undefined) {
      throw new BackendApiError({
        code: envelope?.error?.code ?? (response.ok ? "invalid_backend_response" : "backend_http_error"),
        details: envelope?.error?.details,
        ehRef: envelope?.error?.ehRef ?? undefined,
        message:
          envelope?.error?.message ??
          (response.ok
            ? "PaperGen received an invalid authentication response."
            : "Authentication failed with status " + response.status + "."),
        requestId: envelope?.meta?.requestId ?? requestId,
        status: response.ok ? 502 : response.status,
      });
    }
    return { data: envelope.data, headers: response.headers };
  } catch (error) {
    if (error instanceof BackendApiError) throw error;
    throw new BackendApiError({
      code: controller.signal.aborted ? "backend_timeout" : "backend_unavailable",
      message: controller.signal.aborted
        ? "The authentication service took longer than expected."
        : "The authentication service is unavailable.",
      requestId,
      status: 502,
    });
  } finally {
    clearTimeout(timeout);
  }
}

export async function buildBrowserSession(
  accessToken: string,
  expiresIn: number,
  requestId?: string,
): Promise<AuthSessionResponse> {
  const { data: principal } = await backendRequest<Principal>("/api/v1/auth/me", {
    accessToken,
    requestId,
  });
  const orgs = uniqueOrgMemberships(
    principal.memberships
      .filter((membership) => isRole(membership.role))
      .map((membership) => ({
        id: membership.orgId,
        name: membership.orgName,
        role: membership.role as Role,
      })),
  );
  const activeMembership = orgs.find((membership) => membership.id === principal.activeOrgId);
  const role = isRole(principal.activeRole) ? principal.activeRole : activeMembership?.role ?? null;
  return {
    accessToken,
    activeOrgId: principal.activeOrgId ?? null,
    expiresAt: Date.now() + expiresIn * 1000,
    orgs,
    role,
    user: {
      email: principal.email,
      id: principal.id,
      name: principal.fullName,
    },
  };
}

export function backendErrorResponse(error: unknown) {
  const normalized =
    error instanceof BackendApiError
      ? error
      : new BackendApiError({
          code: "backend_unavailable",
          message: "The authentication service is unavailable.",
          status: 502,
        });
  return NextResponse.json(
    {
      success: false,
      error: {
        code: normalized.code,
        details: normalized.details,
        ehRef: normalized.ehRef ?? null,
        message: normalized.message,
      },
      meta: {
        pagination: null,
        requestId: normalized.requestId ?? globalThis.crypto.randomUUID(),
      },
    },
    { status: normalized.status },
  );
}

export function backendRefreshCookie(headers: Headers) {
  const setCookie = headers.get("set-cookie");
  if (!setCookie) return null;
  const match = setCookie.match(/(?:^|,\s*)refresh=([^;]*)/i);
  if (!match?.[1]) return null;
  const maxAgeText = setCookie.match(/(?:^|;\s*)Max-Age=(\d+)/i)?.[1];
  const maxAge = maxAgeText ? Number(maxAgeText) : undefined;
  try {
    return { maxAge, value: decodeURIComponent(match[1]) };
  } catch {
    return { maxAge, value: match[1] };
  }
}

export function forwardRefreshCookie(value: string) {
  return "refresh=" + encodeURIComponent(value);
}

function backendUrl(path: string) {
  const base =
    process.env.API_BASE_URL?.trim() ||
    process.env.NEXT_PUBLIC_API_BASE_URL?.trim() ||
    "http://127.0.0.1:8000";
  return base.replace(/\/+$/, "") + path;
}

function isRole(value: string | null | undefined): value is Role {
  return value === "teacher" || value === "coaching_admin" || value === "super_admin";
}
