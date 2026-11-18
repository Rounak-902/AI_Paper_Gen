import { NextResponse, type NextRequest } from "next/server";
import type { components } from "@/api/api-types";
import { successEnvelope } from "@/lib/api-envelope";
import {
  BackendApiError,
  backendErrorResponse,
  backendRequest,
} from "@/lib/backend-api";

type InvitationAccepted = components["schemas"]["InvitationAcceptedOut"];

export async function POST(request: NextRequest) {
  const requestId = request.headers.get("x-request-id") ?? globalThis.crypto.randomUUID();
  const body = await request.json().catch(() => null);
  const fullName = typeof body?.name === "string" ? body.name.trim() : "";
  const password = typeof body?.password === "string" ? body.password : "";
  const token = typeof body?.token === "string" ? body.token.trim() : "";
  if (!fullName || !password || !token) {
    return backendErrorResponse(
      new BackendApiError({
        code: "validation_error",
        message: "Name, password, and invitation token are required.",
        requestId,
        status: 422,
      }),
    );
  }

  try {
    const { data } = await backendRequest<InvitationAccepted>("/api/v1/invitations/accept", {
      body: { fullName, password, token },
      method: "POST",
      requestId,
    });
    return NextResponse.json(successEnvelope({ accepted: true as const, email: data.email }));
  } catch (error) {
    return backendErrorResponse(error);
  }
}
