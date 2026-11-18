"""Body-size cap — doc 07 §6 "Body size cap", §20.1 "default 10MB for API" (P1-T6).

Two enforcement points, because one is not enough:

1. ``Content-Length``, checked before the body is read at all. Rejects the honest
   oversized request without buffering a byte of it.
2. A wrapped ``receive`` that counts what actually arrives. A chunked request sends no
   ``Content-Length``, and a dishonest one can understate it, so the header check alone
   is a suggestion rather than a limit.

Why the 413 is sent from inside this middleware
-----------------------------------------------
The obvious implementation raises an exception and lets the error handlers turn it into
a response. That does not work here. ``add_middleware`` installs this layer *outside*
the router's ``ExceptionMiddleware``, so a raised exception skips every
``@app.exception_handler`` registration except the ``Exception`` catch-all — turning a
413 into a 500. So the over-limit case is answered here directly, using
``error_payload`` so the body is the same envelope every other error uses. The private
sentinel exception exists only to unwind out of ``receive``; it never escapes.
"""

from __future__ import annotations

import json
import logging

from starlette.types import ASGIApp, Message, Receive, Scope, Send

from app.common.envelope import error_payload
from app.common.request_context import REQUEST_ID_HEADER, get_request_id

logger = logging.getLogger("papergen.api.request")

#: Doc 07 §20.1: "Request body size capped (default 10MB for API, 500MB for uploads
#: via signed URL)". Only the API figure applies to this middleware — the 500MB path is
#: an upload that bypasses the API entirely via a signed URL, so it never traverses
#: this stack.
#:
#: JUDGMENT: this is a module constant rather than a ``Settings`` field. Adding one to
#: ``app/core/config.py`` would be the tidier home, but P1-T6 has no mandate to change
#: the settings surface (and ``test_config.py`` asserts against it), so the value is
#: overridable per-instance instead and the settings field is left to whoever owns the
#: deployment configuration.
DEFAULT_MAX_BODY_BYTES = 10 * 1024 * 1024

#: Doc 07 §22.2's status table does not list 413. It is the only correct status for
#: this condition (RFC 9110 §15.5.14, "Content Too Large"), so it is used and reported
#: as an addition to the documented set rather than forced into a documented status.
STATUS_CONTENT_TOO_LARGE = 413
ERROR_CODE_BODY_TOO_LARGE = "REQUEST_BODY_TOO_LARGE"


class _BodyTooLarge(Exception):
    """Internal unwind signal. Never reaches an exception handler."""

    def __init__(self, received: int) -> None:
        super().__init__(received)
        self.received = received


class BodySizeLimitMiddleware:
    """Reject request bodies over ``max_body_bytes`` with an enveloped 413."""

    def __init__(self, app: ASGIApp, *, max_body_bytes: int = DEFAULT_MAX_BODY_BYTES) -> None:
        self.app = app
        self.max_body_bytes = max_body_bytes

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        if scope["type"] != "http":
            await self.app(scope, receive, send)
            return

        headers = {k.decode("latin-1").lower(): v for k, v in scope.get("headers", [])}
        raw_length = headers.get("content-length")
        if raw_length is not None:
            try:
                declared = int(raw_length.decode("latin-1"))
            except ValueError:
                declared = -1
            if declared > self.max_body_bytes:
                await self._reject(send, declared, source="content-length")
                return

        received = 0
        response_started = False

        async def counting_receive() -> Message:
            nonlocal received
            message = await receive()
            if message["type"] == "http.request":
                received += len(message.get("body", b""))
                if received > self.max_body_bytes:
                    raise _BodyTooLarge(received)
            return message

        async def tracking_send(message: Message) -> None:
            nonlocal response_started
            if message["type"] == "http.response.start":
                response_started = True
            await send(message)

        try:
            await self.app(scope, counting_receive, tracking_send)
        except _BodyTooLarge as exc:
            if response_started:
                # Headers are already on the wire; there is no valid way to change the
                # status now. Log it and let it surface as a broken response rather
                # than pretending to send a second one.
                logger.error(
                    "body_limit_exceeded_after_response_started received=%s limit=%s",
                    exc.received,
                    self.max_body_bytes,
                )
                raise
            await self._reject(send, exc.received, source="stream")

    async def _reject(self, send: Send, size: int, *, source: str) -> None:
        request_id = get_request_id()
        logger.warning(
            "body_limit_exceeded source=%s size=%s limit=%s request_id=%s",
            source,
            size,
            self.max_body_bytes,
            request_id,
        )

        body = json.dumps(
            error_payload(
                code=ERROR_CODE_BODY_TOO_LARGE,
                # The limit is stated. It is not a secret, and a client that cannot
                # learn it can only guess-and-retry, which is worse for both sides.
                message=(
                    f"Request body exceeds the {self.max_body_bytes} byte limit."
                ),
                request_id=request_id,
            )
        ).encode("utf-8")

        await send(
            {
                "type": "http.response.start",
                "status": STATUS_CONTENT_TOO_LARGE,
                "headers": [
                    (b"content-type", b"application/json"),
                    (b"content-length", str(len(body)).encode("latin-1")),
                    (REQUEST_ID_HEADER.encode("latin-1"), request_id.encode("latin-1")),
                    # RFC 9110: the connection state is undefined after refusing a
                    # body mid-stream, so do not invite the client to reuse it.
                    (b"connection", b"close"),
                ],
            }
        )
        await send({"type": "http.response.body", "body": body})


__all__ = [
    "DEFAULT_MAX_BODY_BYTES",
    "ERROR_CODE_BODY_TOO_LARGE",
    "STATUS_CONTENT_TOO_LARGE",
    "BodySizeLimitMiddleware",
]
