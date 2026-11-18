import { NextResponse, type NextRequest } from "next/server";
import { ACTIVE_ORG_COOKIE_NAME, REFRESH_COOKIE_NAME } from "./lib/cookies";

const PUBLIC_PREFIXES = ["/login", "/forgot-password", "/accept-invite"];
const PROTECTED_PREFIXES = [
  "/select-org",
  "/dashboard",
  "/notifications",
  "/profile",
  "/settings",
  "/papers",
  "/question-bank",
  "/textbooks",
  "/admin",
  "/platform",
];

export function middleware(request: NextRequest) {
  const { pathname, search } = request.nextUrl;
  const isPublic = startsWithAny(pathname, PUBLIC_PREFIXES);
  const isProtected = startsWithAny(pathname, PROTECTED_PREFIXES);
  const hasSession = Boolean(request.cookies.get(REFRESH_COOKIE_NAME)?.value);
  const hasActiveOrg = Boolean(request.cookies.get(ACTIVE_ORG_COOKIE_NAME)?.value);
  const requestId = request.headers.get("x-request-id") ?? globalThis.crypto.randomUUID();

  if (isPublic && hasSession) {
    return withHeaders(NextResponse.redirect(new URL("/select-org", request.url)), requestId);
  }

  if (isProtected && !hasSession) {
    const loginUrl = new URL("/login", request.url);
    loginUrl.searchParams.set("next", `${pathname}${search}`);

    return withHeaders(NextResponse.redirect(loginUrl), requestId);
  }

  if (isProtected && pathname !== "/select-org" && !hasActiveOrg) {
    const selectOrgUrl = new URL("/select-org", request.url);
    selectOrgUrl.searchParams.set("next", `${pathname}${search}`);

    return withHeaders(NextResponse.redirect(selectOrgUrl), requestId);
  }

  const requestHeaders = new Headers(request.headers);
  requestHeaders.set("x-request-id", requestId);

  return withHeaders(
    NextResponse.next({
      request: {
        headers: requestHeaders,
      },
    }),
    requestId,
  );
}

function startsWithAny(pathname: string, prefixes: string[]) {
  return prefixes.some((prefix) => pathname === prefix || pathname.startsWith(`${prefix}/`));
}

function withHeaders(response: NextResponse, requestId: string) {
  response.headers.set("x-request-id", requestId);
  response.headers.set("Referrer-Policy", "strict-origin-when-cross-origin");
  response.headers.set("X-Content-Type-Options", "nosniff");
  response.headers.set("X-Frame-Options", "DENY");

  return response;
}

export const config = {
  matcher: ["/((?!api|_next/static|_next/image|favicon.ico|.*\\..*).*)"],
};
