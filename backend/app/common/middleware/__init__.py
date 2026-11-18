"""Middleware stack (P1-T6) — and the record of what was deliberately left out.

Doc 07 §6 lists the stack outermost-first:

    - Security headers (CSP, nosniff, frame options, HSTS)
    - CORS (strict allow-list, credentials explicit)
    - Body size cap
    - Correlation ID (X-Request-Id generation/propagation, bound to structlog contextvars)
    - Rate limiter (Redis-backed, per-user + per-org)
    - Timing

and on the way back out:

    - Envelope middleware wraps in the success shape
    - Audit middleware logs the mutation
    - Timing middleware records latency

Built in this task
------------------
``IMPLEMENTATION_PLAN.md`` P1-T6 scopes the middleware work to "request-ID / audit /
timing middleware", so those three are built, plus the body-size cap and CORS:

* :class:`~app.common.middleware.request_id.RequestIdMiddleware` — plan item
  "request-ID"; doc 07 §19.3.
* :class:`~app.common.middleware.timing.TimingMiddleware` — plan item "timing";
  doc 07 §6.
* :class:`~app.common.middleware.audit.AuditLogMiddleware` — plan item "audit";
  doc 07 §6 "Audit middleware logs the mutation".
* :class:`~app.common.middleware.body_limit.BodySizeLimitMiddleware` — not named by
  the plan line, but it is a *guard* in doc 07 §6's stack and doc 07 §20.1 gives it a
  concrete value ("Request body size capped (default 10MB for API...)"). It needs no
  infrastructure and it protects the validation layer this task is building, so
  omitting it would leave a hole in the very chain under construction.
* ``CORSMiddleware`` (Starlette's) — wired in ``main.py`` rather than here, because
  ``Settings.CORS_ORIGINS`` already exists and without it the doc 08 frontend cannot
  call this API at all. Three lines of configuration, no new abstraction.

Deliberately deferred, with reasons
-----------------------------------
* **Rate limiter → P1-T10 (infra) / later.** Doc 07 §20.3 specifies "Redis-backed
  sliding window", and the emphasis is on *sliding*. The cheap version — ``INCR``
  plus ``EXPIRE`` on a fixed bucket — permits a 2x burst straddling the bucket
  boundary, so it would satisfy the config knob while missing the requirement.
  ``LOGIN_RATE_LIMIT = 50`` (``core/config.py``) has no companion window-duration
  setting, which is itself a sign the shape is not yet settled. Key design is the
  second open question: per-IP alone punishes NAT'd offices and is defeated by a
  botnet, per-email alone hands an attacker a way to lock a known account out, and a
  per-pair limit needs a deliberate decision about which limit trips first and what
  each response discloses. Redis itself is **not** the obstacle — it is provisioned
  and load-bearing as of P1-T7, since refresh sessions live in it.
  :class:`app.common.errors.RateLimitError` is defined now so the limiter has a
  mapped error to raise and never formats its own HTTP response, which doc 07 §10.1
  forbids.
* **Security headers → deployment/infra.** Doc 07 §20.2's list is CSP, HSTS, nosniff,
  frame options. HSTS is meaningless without the TLS termination doc 07 §6 places
  "upstream at the LB / ingress", and a CSP has to be written against the real
  frontend asset origins, which do not exist yet. Guessing a CSP now produces either
  a permissive one nobody revisits or a strict one that breaks the first UI commit.
* **``audit_logs`` table writes → P1-T8 / P2.** The middleware emits the structured
  log line doc 07 §6 asks for. It does not write rows to the ``audit_logs`` table
  (``app/models/audit.py``), because middleware runs *outside* the dependency chain
  and so has no org-scoped session — obtaining one would mean opening a second
  transaction per mutation, after the first has already committed, with no way to make
  the pair atomic. ADR-004 makes ``audit_logs`` append-only, so a durable audit row
  belongs in the service transaction that performed the mutation, not in a middleware
  bolted on after it. Flagged in the report as a real gap rather than a completed item.

Stack order deviates from doc 07 §6 in one place
------------------------------------------------
The correlation ID is installed **outside** the body-size cap, not inside it as §6
lists. Reason: doc 07 §19.3 requires the request id to be "Included in error
responses (``meta.requestId``)" and "Attached to all log entries during the request"
— for every request, with no exemption for ones the server rejects early. With §6's
ordering, a request rejected by the body cap is answered before the id exists, so its
413 would carry ``meta.requestId`` of ``req_unbound`` and its log line would be
uncorrelatable. §19.3's guarantee is the more specific and more testable of the two
statements, so it wins. Reported as a judgment call.
"""

from app.common.middleware.audit import AuditLogMiddleware
from app.common.middleware.body_limit import (
    DEFAULT_MAX_BODY_BYTES,
    BodySizeLimitMiddleware,
)
from app.common.middleware.request_id import RequestIdMiddleware
from app.common.middleware.timing import TimingMiddleware

__all__ = [
    "DEFAULT_MAX_BODY_BYTES",
    "AuditLogMiddleware",
    "BodySizeLimitMiddleware",
    "RequestIdMiddleware",
    "TimingMiddleware",
]
