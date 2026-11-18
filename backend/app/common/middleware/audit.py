"""Audit middleware — doc 07 §6 "Audit middleware logs the mutation" (P1-T6).

Scope, stated plainly because the gap matters
---------------------------------------------
This emits a structured **log line** for every state-changing request. It does not
write rows to the ``audit_logs`` table (``app/models/audit.py``). The reason is
structural rather than a shortcut:

Middleware sits outside the dependency chain, so it has no ``get_db`` session and no
resolved ``OrgContext``. To write an audit row it would have to open its own
transaction *after* the mutation's transaction already committed, with no way to make
the two atomic — a crash between them loses the audit record for a change that did
happen. ADR-004 makes ``audit_logs`` append-only precisely so the trail can be trusted,
and a trail with silent holes is worse than an obviously absent one.

The durable audit row therefore belongs inside the service transaction that performs
the mutation (doc 07 §11's service layer, "Emit domain events after commit"), which is
P1-T8 and later. This middleware provides the operational trace in the meantime and is
reported as an intentional partial, not a finished item.

What is logged, and what is not
-------------------------------
Method, path, status, actor and org — never the request body. Doc 07 §19.4 requires
that "no secrets, tokens, or PII reach the logs", and a mutation body is exactly where
those live (a password on a user-create, a student name on an enrolment). The actor is
read from the guard chain's placeholder headers rather than from the resolved
``OrgContext``, because a middleware cannot see dependency results; that is noted as a
limitation of the middleware placement doc 07 §6 specifies, and it is another reason
the durable row belongs in the service layer where the real context is in scope.
"""

from __future__ import annotations

import logging

from starlette.types import ASGIApp, Message, Receive, Scope, Send

from app.common.deps import DEBUG_ORG_ID_HEADER, DEBUG_USER_ID_HEADER
from app.common.request_context import get_request_id

logger = logging.getLogger("papergen.api.audit")

#: Doc 07 §6 says "logs the mutation", so read traffic is out of scope — the timing
#: middleware already emits a line for every request, and duplicating it here would
#: double the log volume for no added signal.
MUTATING_METHODS: frozenset[str] = frozenset({"POST", "PUT", "PATCH", "DELETE"})


class AuditLogMiddleware:
    """Log one line per state-changing request, after the status is known."""

    def __init__(self, app: ASGIApp) -> None:
        self.app = app

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        if scope["type"] != "http" or scope.get("method") not in MUTATING_METHODS:
            await self.app(scope, receive, send)
            return

        headers = {k.decode("latin-1").lower(): v for k, v in scope.get("headers", [])}

        def header(name: str) -> str:
            raw = headers.get(name.lower())
            if raw is None:
                return "-"
            value = raw.decode("latin-1")
            # Bound the length: these are client-controlled and land in a log line.
            return value[:64] if value else "-"

        actor = header(DEBUG_USER_ID_HEADER)
        org = header(DEBUG_ORG_ID_HEADER)
        status_code = 0

        async def capturing_send(message: Message) -> None:
            nonlocal status_code
            if message["type"] == "http.response.start":
                status_code = message["status"]
            await send(message)

        def emit(code: int) -> None:
            # Outcome is derived from the status rather than assumed: a 4xx mutation
            # that was rejected is exactly the event doc 01's EH-14 wants audited
            # ("the attempt is audited"), so failures are logged, not just successes.
            outcome = "success" if 200 <= code < 300 else "rejected"
            logger.info(
                "mutation method=%s path=%s status=%s outcome=%s actor=%s org=%s "
                "request_id=%s",
                scope.get("method", "-"),
                scope.get("path", "-"),
                code,
                outcome,
                actor,
                org,
                get_request_id(),
            )

        try:
            await self.app(scope, receive, capturing_send)
        except BaseException:
            emit(500)
            raise
        else:
            emit(status_code)


__all__ = ["MUTATING_METHODS", "AuditLogMiddleware"]
