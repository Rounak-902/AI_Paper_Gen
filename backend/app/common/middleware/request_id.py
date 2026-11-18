"""Correlation-ID middleware — doc 07 §19.3 (P1-T6).

Pure ASGI rather than ``BaseHTTPMiddleware``, for a reason specific to this
middleware's job.

``BaseHTTPMiddleware`` runs the downstream application in a child task, and a
``ContextVar`` bound in ``dispatch`` reaches that child only because anyio copies the
context at spawn time. That happens to work, but the reset does not: when the
downstream raises, ``dispatch``'s ``finally`` runs while the exception is still
propagating — i.e. *before* ``ServerErrorMiddleware`` (which sits outside) invokes the
catch-all handler. The handler would then read an unbound id and emit a 500 envelope
with ``meta.requestId`` of ``req_unbound``, precisely on the path where a correlation
id matters most.

Pure ASGI keeps everything in one task and lets this middleware control exactly when
the binding is released — see the ``except``/``else`` split in :meth:`__call__`.
"""

from __future__ import annotations

import logging
from typing import Any

from starlette.datastructures import MutableHeaders
from starlette.types import ASGIApp, Message, Receive, Scope, Send

from app.common.request_context import (
    REQUEST_ID_HEADER,
    bind_request_id,
    normalize_request_id,
    reset_request_id,
)

logger = logging.getLogger("papergen.api.request")

#: ASGI scope key under which the resolved id is stashed. Some code has a ``scope``
#: but no contextvar available (an inner ASGI middleware, an exception hook), and a
#: scope key costs nothing.
SCOPE_REQUEST_ID_KEY = "papergen_request_id"


class RequestIdMiddleware:
    """Assign or propagate ``X-Request-Id``, bind it, and echo it on the response.

    Doc 07 §19.3 lists four obligations for the id: attached to all log entries during
    the request, propagated into queued jobs, included in error responses
    (``meta.requestId``), and usable to trace a request across API/queue/worker.

    Two are satisfied here and in :mod:`app.common.request_context` (the log binding,
    and the ``meta.requestId`` that :mod:`app.common.errors` reads from the same
    contextvar); the queue ones have no queue to propagate into until the worker lands,
    and :func:`~app.common.request_context.get_request_id` is the seam they will use.

    The response *header* is not on §19.3's list — see the note in
    :mod:`app.common.request_context` on why it is added anyway. Because it is an
    addition rather than a requirement, it is applied non-destructively: if an inner
    layer already set the header, this one leaves it alone.
    """

    def __init__(self, app: ASGIApp) -> None:
        self.app = app

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        if scope["type"] not in ("http", "websocket"):
            await self.app(scope, receive, send)
            return

        headers = {k.decode("latin-1").lower(): v for k, v in scope.get("headers", [])}
        raw = headers.get(REQUEST_ID_HEADER.lower())
        request_id = normalize_request_id(raw.decode("latin-1") if raw else None)

        scope[SCOPE_REQUEST_ID_KEY] = request_id
        token = bind_request_id(request_id)

        async def send_with_request_id(message: Message) -> None:
            if message["type"] == "http.response.start":
                # ``MutableHeaders`` mutates ``message["headers"]`` in place. ``append``
                # rather than ``__setitem__`` would duplicate the header if an inner
                # layer already set one, so the presence check is deliberate.
                response_headers = MutableHeaders(scope=message)
                if REQUEST_ID_HEADER not in response_headers:
                    response_headers[REQUEST_ID_HEADER] = request_id
            await send(message)

        try:
            await self.app(scope, receive, send_with_request_id)
        except BaseException:
            # Do NOT reset. The exception is about to reach ServerErrorMiddleware,
            # whose handler reads get_request_id() to build the 500 envelope. Leaving
            # the binding in place is safe: uvicorn runs each request cycle in its own
            # task, so the context dies with the request and cannot leak to the next
            # one.
            raise
        else:
            reset_request_id(token)


def request_id_from_scope(scope: Scope, default: Any = None) -> Any:
    """The id stashed in an ASGI scope, for code that has a scope but no contextvar."""
    return scope.get(SCOPE_REQUEST_ID_KEY, default)


__all__ = ["SCOPE_REQUEST_ID_KEY", "RequestIdMiddleware", "request_id_from_scope"]
