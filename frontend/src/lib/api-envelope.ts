import type { ApiEnvelope } from "@/types/api";

function requestId() {
  return globalThis.crypto?.randomUUID?.() ?? "req_frontend_stub";
}

export function successEnvelope<T>(data: T, message?: string): ApiEnvelope<T> {
  return {
    success: true,
    data,
    message,
    meta: {
      requestId: requestId(),
    },
  };
}

export function errorEnvelope(
  status: number,
  code: string,
  message: string,
  details?: unknown,
) {
  return Response.json(
    {
      success: false,
      error: {
        code,
        message,
        details,
        ehRef: "EH-15",
      },
      meta: {
        requestId: requestId(),
      },
    },
    { status },
  );
}
