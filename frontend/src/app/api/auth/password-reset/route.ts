import type { NextRequest } from "next/server";
import { errorEnvelope, successEnvelope } from "@/lib/api-envelope";

export async function POST(request: NextRequest) {
  // Deliberately return a neutral acknowledgement until the backend ships
  // reset-token delivery; avoid exposing whether an account exists.
  const body = await request.json().catch(() => null);
  const email = typeof body?.email === "string" ? body.email.trim() : "";

  if (!email) {
    return errorEnvelope(422, "validation_error", "Email is required.");
  }

  return Response.json(successEnvelope({ submitted: true }));
}
