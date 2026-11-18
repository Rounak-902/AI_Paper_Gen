"""Timing middleware — doc 07 §6 "Timing" / "Timing middleware records latency" (P1-T6).

Emits one structured line per request with method, path, status and duration. The line
is the only output: no response header is added, because doc 07 does not name one and
inventing ``X-Response-Time`` would put an undocumented field into the contract doc 08
§10.6 parses. If a header is wanted later it is a one-line change here plus an entry in
doc 07 §22.

Pure ASGI for the same reason as :mod:`app.common.middleware.request_id`: the timing
line must be emitted for failed requests too, and ``BaseHTTPMiddleware``'s ``finally``
runs at an awkward point relative to ``ServerErrorMiddleware``. Here the status is
captured off the ``http.response.start`` message, and the ``500`` case is handled
explicitly on the exception path — where no ``http.response.start`` ever reaches this
layer, because ``ServerErrorMiddleware`` sends it from further out.
"""

from __future__ import annotations

import logging
import time

from starlette.types import ASGIApp, Message, Receive, Scope, Send

from app.common.request_context import get_request_id

logger = logging.getLogger("papergen.api.timing")

#: Requests slower than this are logged at WARNING instead of INFO. Doc 07 §19.2's
#: log-category table fixes the number: "Performance logs | Slow queries (>500ms),
#: slow API responses (>2s)". The 500ms figure is the *query* threshold and belongs to
#: the database layer, not to this middleware.
SLOW_REQUEST_MS = 2000.0


class TimingMiddleware:
    """Record wall-clock duration for every request and log it once."""

    def __init__(self, app: ASGIApp) -> None:
        self.app = app

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        if scope["type"] != "http":
            await self.app(scope, receive, send)
            return

        started = time.perf_counter()
        status_code = 0

        async def capturing_send(message: Message) -> None:
            nonlocal status_code
            if message["type"] == "http.response.start":
                status_code = message["status"]
            await send(message)

        try:
            await self.app(scope, receive, capturing_send)
        except BaseException:
            # ServerErrorMiddleware is outside this layer, so it — not this middleware
            # — sends the 500. The status is therefore known by inference only, and
            # recording it as 500 here is accurate for every path that reaches this
            # except clause.
            self._log(scope, 500, started, errored=True)
            raise
        else:
            self._log(scope, status_code, started, errored=False)

    def _log(
        self, scope: Scope, status_code: int, started: float, *, errored: bool
    ) -> None:
        duration_ms = (time.perf_counter() - started) * 1000.0
        query = scope.get("query_string", b"")

        # The path is logged; the query string is logged only as a length. Query
        # parameters are the one place a caller can put a secret into a URL (a
        # password-reset token, an invite code), and doc 07 §19.4's log-hygiene rule is
        # that "no secrets, tokens, or PII reach the logs".
        level = logger.warning if (errored or duration_ms > SLOW_REQUEST_MS) else logger.info
        level(
            "request_completed method=%s path=%s status=%s duration_ms=%.2f "
            "query_len=%s request_id=%s",
            scope.get("method", "-"),
            scope.get("path", "-"),
            status_code,
            duration_ms,
            len(query),
            get_request_id(),
        )


__all__ = ["SLOW_REQUEST_MS", "TimingMiddleware"]
