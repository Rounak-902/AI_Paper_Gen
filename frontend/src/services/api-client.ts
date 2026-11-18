import { z } from "zod";

type TokenAccessor = () => string | null;
type RefreshAccessToken = () => Promise<string | null>;
type UnauthenticatedHandler = () => void;

export type ApiClientOptions = Omit<RequestInit, "body"> & {
  body?: BodyInit | Record<string, unknown> | null;
  idempotencyKey?: string;
  skipAuth?: boolean;
  skipAuthRefresh?: boolean;
  timeoutMessage?: string;
  timeoutMs?: number;
};

export type ApiPagination = {
  page?: number;
  pageSize?: number;
  total?: number;
  hasNext?: boolean;
  cursor?: string | null;
  nextCursor?: string | null;
};

export type ApiResponse<T> = {
  data: T;
  message?: string;
  meta?: {
    requestId?: string;
    pagination?: ApiPagination | null;
    [key: string]: unknown;
  };
};

const successEnvelopeSchema = z
  .object({
    success: z.literal(true),
    data: z.unknown(),
    message: z.string().nullish(),
    meta: z
      .object({
        requestId: z.string().optional(),
        pagination: z.unknown().optional(),
      })
      .passthrough()
      .optional(),
  })
  .passthrough();

const errorEnvelopeSchema = z
  .object({
    success: z.literal(false),
    error: z
      .object({
        code: z.string(),
        message: z.string(),
        details: z.unknown().optional(),
        ehRef: z.string().nullish(),
      })
      .passthrough(),
    meta: z
      .object({
        requestId: z.string().optional(),
      })
      .passthrough()
      .optional(),
  })
  .passthrough();

const envelopeSchema = z.union([successEnvelopeSchema, errorEnvelopeSchema]);

let getAccessToken: TokenAccessor = () => null;
let refreshAccessToken: RefreshAccessToken | null = null;
let onUnauthenticated: UnauthenticatedHandler | null = null;
let refreshPromise: Promise<string | null> | null = null;

export class ApiError extends Error {
  status: number;
  code: string;
  details?: unknown;
  ehRef?: string;
  requestId?: string;
  retryable: boolean;

  constructor({
    code,
    details,
    ehRef,
    message,
    requestId,
    retryable,
    status,
  }: {
    status: number;
    code: string;
    message: string;
    details?: unknown;
    ehRef?: string;
    requestId?: string;
    retryable?: boolean;
  }) {
    super(message);
    this.name = "ApiError";
    this.status = status;
    this.code = code;
    this.details = details;
    this.ehRef = ehRef;
    this.requestId = requestId;
    this.retryable = retryable ?? false;
  }
}

export function configureApiAuth({
  getToken,
  handleUnauthenticated,
  refresh,
}: {
  getToken: TokenAccessor;
  refresh: RefreshAccessToken;
  handleUnauthenticated: UnauthenticatedHandler;
}) {
  getAccessToken = getToken;
  refreshAccessToken = refresh;
  onUnauthenticated = handleUnauthenticated;
}

export async function apiClient<T>(path: string, options: ApiClientOptions = {}): Promise<T> {
  return (await request<T>(path, options, false)).data;
}

export async function apiClientWithMeta<T>(
  path: string,
  options: ApiClientOptions = {},
): Promise<ApiResponse<T>> {
  return request<T>(path, options, false);
}

export async function downloadApiFile(
  path: string,
  fallbackFilename: string,
  options: ApiClientOptions = {},
) {
  const response = await requestFile(path, options, false);
  const filename = filenameFromDisposition(response.headers.get("content-disposition")) ?? fallbackFilename;
  const blob = await response.blob();
  const href = URL.createObjectURL(blob);
  const anchor = document.createElement("a");
  anchor.href = href;
  anchor.download = filename;
  document.body.append(anchor);
  anchor.click();
  anchor.remove();
  window.setTimeout(() => URL.revokeObjectURL(href), 0);
}

async function request<T>(
  path: string,
  options: ApiClientOptions,
  didRetryAfterRefresh: boolean,
): Promise<ApiResponse<T>> {
  const response = await performFetch(path, options);

  if (response.status === 401 && !options.skipAuthRefresh) {
    const token = didRetryAfterRefresh ? null : await refreshAfterUnauthorized();

    if (token) {
      return request<T>(path, options, true);
    }

    onUnauthenticated?.();
  }

  return unwrapEnvelope<T>(response);
}

async function requestFile(
  path: string,
  options: ApiClientOptions,
  didRetryAfterRefresh: boolean,
): Promise<Response> {
  const response = await performFetch(path, options);

  if (response.status === 401 && !options.skipAuthRefresh) {
    const token = didRetryAfterRefresh ? null : await refreshAfterUnauthorized();

    if (token) {
      return requestFile(path, options, true);
    }

    onUnauthenticated?.();
  }

  if (!response.ok) {
    await unwrapEnvelope<never>(response);
  }

  return response;
}

async function performFetch(path: string, options: ApiClientOptions) {
  const controller = new AbortController();
  const timeoutMs = options.timeoutMs ?? 15_000;
  let timedOut = false;
  const timeout = window.setTimeout(() => {
    timedOut = true;
    controller.abort();
  }, timeoutMs);
  const abortFromCaller = () => controller.abort();
  options.signal?.addEventListener("abort", abortFromCaller, { once: true });

  try {
    return await fetch(resolveApiUrl(path), {
      ...options,
      body: serializeBody(options.body),
      credentials: options.credentials ?? "same-origin",
      headers: buildHeaders(options),
      signal: controller.signal,
    });
  } catch (error) {
    if (error instanceof ApiError) {
      throw error;
    }

    if (options.signal?.aborted && !timedOut) {
      throw error;
    }

    throw new ApiError({
      code: timedOut ? "request_timeout" : "network_error",
      message: timedOut
        ? options.timeoutMessage ?? "This request took longer than expected."
        : "PaperGen could not reach the server.",
      retryable: true,
      status: 0,
    });
  } finally {
    window.clearTimeout(timeout);
    options.signal?.removeEventListener("abort", abortFromCaller);
  }
}

function buildHeaders(options: ApiClientOptions) {
  const headers = new Headers(options.headers);
  const token = options.skipAuth ? null : getAccessToken();

  headers.set("Accept", "application/json");
  headers.set("X-Request-Id", newRequestId());

  if (options.idempotencyKey) {
    headers.set("Idempotency-Key", options.idempotencyKey);
  }

  if (options.body && !isBodyInit(options.body)) {
    headers.set("Content-Type", "application/json");
  }

  if (token) {
    headers.set("Authorization", `Bearer ${token}`);
  }

  return headers;
}

function serializeBody(body: ApiClientOptions["body"]) {
  if (!body) {
    return undefined;
  }

  if (isBodyInit(body)) {
    return body;
  }

  return JSON.stringify(body);
}

function isBodyInit(body: ApiClientOptions["body"]): body is BodyInit {
  return (
    typeof body === "string" ||
    body instanceof Blob ||
    body instanceof FormData ||
    body instanceof URLSearchParams ||
    body instanceof ReadableStream
  );
}

async function unwrapEnvelope<T>(response: Response): Promise<ApiResponse<T>> {
  const parsedBody = await parseJson(response);
  const envelope = envelopeSchema.safeParse(parsedBody);

  if (!envelope.success) {
    throw new ApiError({
      code: response.ok ? "invalid_response_envelope" : "http_error",
      message: response.ok
        ? "PaperGen received an invalid response."
        : `Request failed with status ${response.status}.`,
      retryable: response.status >= 500,
      status: response.status,
    });
  }

  if (!envelope.data.success) {
    throw new ApiError({
      code: envelope.data.error.code,
      details: envelope.data.error.details,
      ehRef: envelope.data.error.ehRef ?? undefined,
      message: envelope.data.error.message,
      requestId: envelope.data.meta?.requestId,
      retryable: response.status >= 500 || response.status === 429,
      status: response.status,
    });
  }

  if (!response.ok) {
    throw new ApiError({
      code: "http_error",
      message: `Request failed with status ${response.status}.`,
      requestId: envelope.data.meta?.requestId,
      retryable: response.status >= 500,
      status: response.status,
    });
  }

  return {
    data: envelope.data.data as T,
    message: envelope.data.message ?? undefined,
    meta: envelope.data.meta as ApiResponse<T>["meta"],
  };
}

async function parseJson(response: Response) {
  const text = await response.text();

  if (!text) {
    return null;
  }

  try {
    return JSON.parse(text);
  } catch {
    return null;
  }
}

async function runRefresh() {
  if (!refreshAccessToken) {
    return null;
  }

  refreshPromise ??= refreshAccessToken().finally(() => {
    refreshPromise = null;
  });

  return refreshPromise;
}

async function refreshAfterUnauthorized() {
  try {
    return await runRefresh();
  } catch (error) {
    // A rejected refresh session is terminal. A transient network failure is
    // not: preserve the local session so the user can retry when connectivity
    // returns.
    if (error instanceof ApiError && error.status === 401) {
      onUnauthenticated?.();
    }
    throw error;
  }
}

function newRequestId() {
  return globalThis.crypto?.randomUUID?.() ?? `req_${Date.now()}`;
}

export function resolveApiUrl(path: string) {
  if (/^https?:\/\//i.test(path) || !path.startsWith("/api/v1")) {
    return path;
  }

  const configuredBase = (
    process.env.NEXT_PUBLIC_API_BASE_URL?.trim() ||
    (process.env.NODE_ENV === "development" ? "http://127.0.0.1:8000" : "")
  ).replace(/\/+$/, "");
  return configuredBase ? `${configuredBase}${path}` : path;
}

function filenameFromDisposition(value: string | null) {
  if (!value) {
    return null;
  }

  const utf8Match = value.match(/filename\*=UTF-8''([^;]+)/i);
  if (utf8Match?.[1]) {
    try {
      return decodeURIComponent(utf8Match[1]);
    } catch {
      return utf8Match[1];
    }
  }

  return value.match(/filename="?([^";]+)"?/i)?.[1] ?? null;
}
