"""Domain exceptions and the global exception handlers (P1-T6).

Doc 07 §3 names this file: ``src/common/errors.py`` — "exception handlers -> one
error envelope with ehRef". Doc 07 §10.1 states the rule it enforces:

    "A single set of ``@app.exception_handler(...)`` registrations catches every
     error class and transforms it into the standard error envelope. **No module
     formats its own HTTP error.** Handlers are registered for the domain exception
     base class, ``RequestValidationError``, ``HTTPException``, and ``Exception``
     (the catch-all, which logs with the request id and returns an opaque 500 with
     an ``ehRef`` — never a stack trace)."

Those four registrations are exactly what :func:`register_exception_handlers`
installs, plus one extra for P1-T5's ``app/db/exceptions.py`` classes, which predate
this task and do not inherit from the base defined here (see below).

Status codes come from doc 07 §22.2 (the status table) and §10.2 (the error-category
table). Where a mapping was not stated outright it is flagged in a ``JUDGMENT``
comment on the class itself, and every one of those is listed in the task report.

The P1-T5 exceptions are handled, not changed
---------------------------------------------
``TenantContextMissing``, ``OptimisticLockError`` and ``EntityNotFoundError`` live in
``app/db/exceptions.py`` and are **not** modified by this task. They do not subclass
``AppError``, so they get their own handler that translates them into the same
envelope. The alternative — making them inherit from a P1-T6 base — would have meant
editing P1-T5's files, which this task must not do, and would also have coupled the
repository layer to HTTP concerns that doc 07 §11.1 explicitly keeps out of it
("HTTP concerns ... that's the router").
"""

from __future__ import annotations

import logging
from typing import Any, Optional

from fastapi import FastAPI, HTTPException, Request, status
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse
from starlette.exceptions import HTTPException as StarletteHTTPException

from app.common.envelope import ErrorDetail, error_payload
from app.common.request_context import REQUEST_ID_HEADER, get_request_id
from app.db.exceptions import (
    EntityNotFoundError,
    OptimisticLockError,
    TenantContextMissing,
)

logger = logging.getLogger("papergen.api.errors")


# ---------------------------------------------------------------------------
# Domain exception base
# ---------------------------------------------------------------------------
class AppError(Exception):
    """Base class for every deliberate, mapped application error.

    Doc 07 §10.1 registers a handler for "the domain exception base class" — this is
    that class. A subclass carries its own HTTP status, its stable machine-readable
    ``code`` (doc 08 §25.3: "stable, machine-readable ... It is stable across
    releases; message text is not"), an optional ``ehRef`` into the EH-1…EH-18
    catalogue of doc 01, and optional per-field ``details``.

    ``message`` is client-visible by construction. Anything that must not reach a
    client belongs in ``log_detail``, which is logged server-side and never
    serialised — the same split doc 07 §10.1 demands of the 500 path, applied
    uniformly so no subclass can leak by accident.
    """

    #: HTTP status for this error class. Subclasses override.
    status_code: int = status.HTTP_400_BAD_REQUEST
    #: Stable machine-readable code. Subclasses override or pass per-instance.
    code: str = "APP_ERROR"
    #: Default EH-n reference; may be overridden per instance.
    eh_ref: Optional[str] = None

    def __init__(
        self,
        message: str,
        *,
        code: Optional[str] = None,
        eh_ref: Optional[str] = None,
        details: Optional[list[ErrorDetail]] = None,
        status_code: Optional[int] = None,
        log_detail: Optional[str] = None,
    ) -> None:
        super().__init__(message)
        self.message = message
        if code is not None:
            self.code = code
        if eh_ref is not None:
            self.eh_ref = eh_ref
        if status_code is not None:
            self.status_code = status_code
        self.details: list[ErrorDetail] = details or []
        #: Server-only diagnostic. Never serialised into a response.
        self.log_detail = log_detail


class AuthenticationError(AppError):
    """401 — doc 07 §10.2 "Authentication errors | 401 | Expired token, invalid
    credentials, revoked session", and §7.5's ``get_current_user`` "(401)".

    EH-15 ("Session expired") is the catalogue's closest entry, so it is the default
    rather than a hardcode: a *missing* token is not an expired session. Callers that
    mean expiry pass ``eh_ref="EH-15"`` explicitly; the default here is None.
    """

    status_code = status.HTTP_401_UNAUTHORIZED
    code = "UNAUTHENTICATED"


class AuthorizationError(AppError):
    """403 — doc 07 §10.2 "Authorization errors | 403 | Insufficient role, wrong org,
    ownership violation", and §7.5's ``get_org_context`` / ``require_roles`` "(403)".

    ``ehRef`` defaults to EH-14, which doc 01's catalogue defines as "Unauthorized
    action → Request denied → Generic authorization message; the attempt is audited
    (NFR-2)". Note the catalogue's own wording mandates a *generic* message, which is
    why the guard chain passes deliberately non-specific text and puts the real
    reason in ``log_detail``.
    """

    status_code = status.HTTP_403_FORBIDDEN
    code = "FORBIDDEN"
    eh_ref = "EH-14"


class NotFoundError(AppError):
    """404 — doc 07 §10.2 "Not found | 404 | Question not found, paper not found".

    Doc 08's status table adds the reason this is never softened into a 403:
    "404 | Resource absent, **or scoped away by tenant isolation** | ... never 'you
    lack access', since 404 may be deliberate scoping."
    """

    status_code = status.HTTP_404_NOT_FOUND
    code = "NOT_FOUND"


class ConflictError(AppError):
    """409 — doc 07 §10.2 "Conflict | 409 | Optimistic lock failure, duplicate
    content_hash".

    Doc 07 §12.1 uses this class by name and fixes both of its arguments for the
    optimistic-lock case: ``raise ConflictError(code="CONCURRENT_MODIFICATION",
    eh_ref="EH-18")``. Those are therefore the defaults here, not invented values.
    """

    status_code = status.HTTP_409_CONFLICT
    code = "CONCURRENT_MODIFICATION"
    eh_ref = "EH-18"


class ValidationError(AppError):
    """422 — doc 07 §10.2 "Validation errors | 422".

    For validation raised *deliberately* by application code. Pydantic's own
    ``RequestValidationError`` is handled separately (and lands on the same status
    and shape) by :func:`_validation_exception_handler`.
    """

    status_code = status.HTTP_422_UNPROCESSABLE_CONTENT
    code = "VALIDATION_FAILED"


class BusinessRuleError(AppError):
    """400 — doc 07 §10.2 "Business rule errors | 400 | Marks mismatch, quota
    exceeded, invalid state transition".

    Doc 07 §8.3 uses this class by name with all three arguments spelled out:
    ``raise BusinessRuleError(code="BLUEPRINT_INFEASIBLE", eh_ref="EH-9",
    detail=gate.reason)``.

    JUDGMENT: the doc's call passes ``detail=`` (singular, a free-text reason),
    whereas the envelope's field is ``details`` (a list of ``{field, issue}``). This
    class accepts ``message`` positionally and ``details`` as the list, so the doc's
    exact call signature does *not* work verbatim. Reconciling that would mean either
    a ``detail=`` alias that duplicates ``message``, or changing the envelope. Left
    as-is and reported: the doc's snippet is illustrative pseudo-code (its
    ``self._assembler`` is not even a constructor argument of the class it appears
    in), so it is not treated as a binding signature.
    """

    status_code = status.HTTP_400_BAD_REQUEST
    code = "BUSINESS_RULE_VIOLATION"


class RateLimitError(AppError):
    """429 — doc 07 §10.2 "Rate limit | 429" and §20.3 "Exhaustion returns 429 with
    ``Retry-After`` header".

    Defined but **not raised anywhere in this task**: the Redis-backed sliding-window
    limiter of doc 07 §20.3 is not built here (see the guard inventory in
    ``deps.py``). The class exists so that the limiter, when it is built, has a
    mapped error to raise rather than a reason to format its own HTTP response —
    which doc 07 §10.1 forbids.

    ``retry_after`` is surfaced as a real header by the handler because doc 08's
    status table has the client "Respect ``Retry-After``"; an envelope field alone
    would not satisfy that.
    """

    status_code = status.HTTP_429_TOO_MANY_REQUESTS
    code = "RATE_LIMITED"

    def __init__(self, message: str, *, retry_after: int = 60, **kwargs: Any) -> None:
        super().__init__(message, **kwargs)
        self.retry_after = retry_after


class UpstreamProviderError(AppError):
    """502 — doc 07 §10.2 "AI errors | 502 | Gemini timeout, invalid response, safety
    block, all retries exhausted" and §22.2 "502 | Upstream provider failure (LLM)".

    Defined, not raised: no AI integration exists until P4. Same rationale as
    :class:`RateLimitError`.
    """

    status_code = status.HTTP_502_BAD_GATEWAY
    code = "UPSTREAM_PROVIDER_FAILED"
    eh_ref = "EH-3"


# ---------------------------------------------------------------------------
# Generic 500 text — the only thing a client ever sees for an unhandled error
# ---------------------------------------------------------------------------
#: Doc 07 §10.1: the catch-all "logs with the request id and returns an opaque 500
#: with an ehRef — **never a stack trace**". This constant is the whole of the
#: client-visible message; it names no exception type, no module, no row, no SQL.
#: Doc 08 §25.3 explains what the client does with the pairing: "requestId is
#: surfaced discreetly on unexpected errors so a user contacting support, and the
#: team investigating, can correlate to the exact request in the logs".
#:
#: JUDGMENT — the ``ehRef`` half of that sentence is NOT satisfiable and is left
#: ``null``. Doc 01's "Error catalogue" defines EH-1 … EH-18 and every one of them is
#: a specific domain failure (OCR failed, generation timeout, LLM failed, plan limit
#: reached, concurrent edit conflict…). There is no entry for "an unexpected internal
#: error", and there is no EH-0 or EH-19. Populating the field would mean minting a
#: catalogue code that doc 01 does not define, which is worse than leaving it null for
#: two reasons: doc 08 §25.3 describes ``ehRef`` as the tie *into that catalogue*, so
#: an unlisted value breaks the only thing it is for; and the machine-readable field
#: the client actually switches on is ``code``, which is populated
#: (``INTERNAL_ERROR``). The key is present-and-null, so the envelope shape is
#: unchanged. Reported as an ambiguity for doc 01 to resolve by adding an entry.
INTERNAL_ERROR_MESSAGE = (
    "An unexpected internal error occurred. Quote the request id when contacting "
    "support."
)
INTERNAL_ERROR_CODE = "INTERNAL_ERROR"


def _json_error(
    *,
    status_code: int,
    code: str,
    message: str,
    request_id: str,
    details: Optional[list[ErrorDetail]] = None,
    eh_ref: Optional[str] = None,
    headers: Optional[dict[str, str]] = None,
) -> JSONResponse:
    """One place that turns a mapped error into an HTTP response.

    Every handler below funnels through here, which is what makes "a client never
    sees two different error response formats depending on which layer raised"
    structurally true rather than a convention four handlers have to remember.

    ``X-Request-Id`` is set here rather than left to
    :class:`~app.common.middleware.request_id.RequestIdMiddleware`, because that
    middleware cannot reach the one response where the id matters most. Starlette
    installs the ``Exception`` handler inside ``ServerErrorMiddleware``, *outside* every
    ``add_middleware`` layer, so the 500 it sends never passes through the
    request-id middleware's ``send`` wrapper and would go out with no header at all.
    The *body* is unaffected either way — ``meta.requestId`` is what doc 07 §19.3
    requires and what doc 08 §10.6 reads, and it is correct on that path already
    because the middleware deliberately does not unbind the contextvar while an
    exception is propagating. The header is the addition (see
    :mod:`app.common.request_context`), and setting it at this funnel is what makes it
    present on *every* error rather than on all but the 500s, which would be the one
    inconsistency an operator grepping proxy logs would actually trip over. The
    middleware still owns generation and inbound propagation, and checks for the header
    before adding it, so there is no duplication.
    """
    response_headers = dict(headers or {})
    response_headers.setdefault(REQUEST_ID_HEADER, request_id)
    return JSONResponse(
        status_code=status_code,
        content=error_payload(
            code=code,
            message=message,
            request_id=request_id,
            details=details,
            eh_ref=eh_ref,
        ),
        headers=response_headers,
    )


# ---------------------------------------------------------------------------
# Handlers
# ---------------------------------------------------------------------------
async def _app_error_handler(request: Request, exc: AppError) -> JSONResponse:
    """Handler for the domain exception base class (doc 07 §10.1)."""
    request_id = get_request_id()

    # 5xx domain errors are logged at error level with their server-only detail;
    # 4xx are the client's fault and logged at info so they do not page anyone.
    log = logger.error if exc.status_code >= 500 else logger.info
    log(
        "app_error status=%s code=%s request_id=%s message=%s log_detail=%s",
        exc.status_code,
        exc.code,
        request_id,
        exc.message,
        exc.log_detail or "-",
    )

    headers: Optional[dict[str, str]] = None
    if isinstance(exc, RateLimitError):
        headers = {"Retry-After": str(exc.retry_after)}

    return _json_error(
        status_code=exc.status_code,
        code=exc.code,
        message=exc.message,
        request_id=request_id,
        details=exc.details,
        eh_ref=exc.eh_ref,
        headers=headers,
    )


async def _db_error_handler(request: Request, exc: Exception) -> JSONResponse:
    """Translate P1-T5's ``app/db/exceptions.py`` classes into the envelope.

    Registered for the three concrete classes rather than a shared base, because
    P1-T5 gave them no common base (``TenantContextMissing`` extends ``RuntimeError``,
    the other two extend ``Exception``) and this task must not edit those files.

    The three mappings, and where each comes from:

    * ``EntityNotFoundError`` → **404**. Doc 07 §10.2: "Not found | 404 | Question
      not found, paper not found". Uncontroversial.

    * ``OptimisticLockError`` → **409** with ``code="CONCURRENT_MODIFICATION"`` and
      ``ehRef="EH-18"``. Not a judgment call: doc 07 §12.1's repository example
      raises exactly ``ConflictError(code="CONCURRENT_MODIFICATION",
      eh_ref="EH-18")`` for this condition, §10.2 maps "Optimistic lock failure" to
      409, and doc 01's EH-18 is "Concurrent edit conflict on a paper". P1-T5 raises
      its own ``OptimisticLockError`` instead of the doc's ``ConflictError``; this
      handler is where the two reconcile.

    * ``TenantContextMissing`` → **500**. JUDGMENT, and a contested one. P1-T5's own
      docstring says "This is a **500-level error**, not a 403: the guard chain
      should have resolved the org context before the code reached a repository. If
      this fires in production, the dependency chain has a hole." Doc 07 §7.5 maps
      ``get_org_context`` failure to 403. Both are right about different events, and
      the resolution here is to honour *where the exception surfaced*:
        - A request that arrives without resolvable org context is rejected by
          ``get_org_context`` in the guard chain with a **403**
          (:class:`AuthorizationError`) — it never reaches a repository, so this
          exception is never constructed.
        - If ``TenantContextMissing`` is nonetheless raised, it escaped from *below*
          the guard, which is P1-T5's "the dependency chain has a hole" — a server
          bug, therefore 500, and deliberately opaque to the client.
      The verification script proves both halves separately (a 403 from the guard,
      and a 500 from a route that reaches the session directly).
    """
    request_id = get_request_id()

    if isinstance(exc, EntityNotFoundError):
        logger.info(
            "entity_not_found entity=%s entity_id=%s request_id=%s",
            exc.entity,
            exc.entity_id,
            request_id,
        )
        # The entity *type* is safe to name; doc 08's table already tells the client
        # 404 may mean "scoped away by tenant isolation", so this leaks nothing that
        # the status code does not already imply.
        return _json_error(
            status_code=status.HTTP_404_NOT_FOUND,
            code="NOT_FOUND",
            message=f"{exc.entity} not found.",
            request_id=request_id,
        )

    if isinstance(exc, OptimisticLockError):
        logger.info(
            "optimistic_lock_conflict entity=%s entity_id=%s expected_version=%s "
            "request_id=%s",
            exc.entity,
            exc.entity_id,
            exc.expected_version,
            request_id,
        )
        return _json_error(
            status_code=status.HTTP_409_CONFLICT,
            code="CONCURRENT_MODIFICATION",
            message=(
                "This record was modified by someone else. Reload it and re-apply "
                "your changes."
            ),
            request_id=request_id,
            eh_ref="EH-18",
        )

    # TenantContextMissing — a guard-chain hole. Log loudly, tell the client nothing.
    logger.error(
        "tenant_context_missing request_id=%s path=%s detail=%s",
        request_id,
        request.url.path,
        getattr(exc, "detail", str(exc)),
        exc_info=True,
    )
    return _json_error(
        status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
        code=INTERNAL_ERROR_CODE,
        message=INTERNAL_ERROR_MESSAGE,
        request_id=request_id,
    )


async def _validation_exception_handler(
    request: Request, exc: RequestValidationError
) -> JSONResponse:
    """Pydantic / FastAPI request validation → 422 in the standard envelope.

    Two decisions worth stating:

    **422, not 400.** Doc 07 contradicts itself here and this is one of the reported
    ambiguities. §2.10 says an unknown field (``extra="forbid"``) "is a **400**, not
    a silently ignored key", while §9's Layer 2 — describing the same ``extra="forbid"``
    mechanism in the same breath — says "Returns **422** with per-field errors", §10.2
    maps every validation error to 422, §22.2 maps 422 to "Validation error
    (malformed input)", and doc 08's client table maps 422 to "map ``error.details``
    onto form fields". 422 is taken as correct on a 4-to-1 reading; §2.10's "400"
    appears to be loose prose rather than a considered exception.

    **``details`` carries the field path, not pydantic's raw error list.** Pydantic's
    ``exc.errors()`` includes an ``input`` key echoing the offending value, and for a
    login body that is the submitted password. Echoing it back would put a credential
    in the response body and in any log that captured it. Only ``loc`` → ``field`` and
    ``msg`` → ``issue`` are copied; ``input``, ``url`` and ``ctx`` are dropped.
    """
    request_id = get_request_id()

    details: list[ErrorDetail] = []
    for err in exc.errors():
        # loc looks like ("body", "sections", 2, "marks") — drop the leading source
        # segment and render the rest the way doc 07 §10.1 shows it:
        # "sections[2].marks".
        loc = list(err.get("loc", ()))
        if loc and loc[0] in ("body", "query", "path", "header", "cookie"):
            loc = loc[1:]
        rendered = ""
        for part in loc:
            if isinstance(part, int):
                rendered += f"[{part}]"
            elif rendered:
                rendered += f".{part}"
            else:
                rendered = str(part)
        details.append(
            ErrorDetail(field=rendered or None, issue=str(err.get("msg", "invalid")))
        )

    logger.info(
        "request_validation_failed request_id=%s path=%s fields=%s",
        request_id,
        request.url.path,
        [d.field for d in details],
    )

    return _json_error(
        status_code=status.HTTP_422_UNPROCESSABLE_CONTENT,
        code="VALIDATION_FAILED",
        message="The request body failed validation.",
        request_id=request_id,
        details=details,
    )


async def _http_exception_handler(
    request: Request, exc: StarletteHTTPException
) -> JSONResponse:
    """``HTTPException`` → the same envelope (doc 07 §10.1 registers it explicitly).

    This is what stops FastAPI's built-in ``{"detail": "Not Found"}`` from ever
    reaching a client. It matters most for responses the framework generates on its
    own, where no application code was involved at all: an unrouted path (404), a
    wrong method (405), an unparseable JSON body (400). Without this handler those
    three are the holes through which the "one shape" guarantee leaks.

    A generic ``code`` is synthesised from the status phrase (404 → ``HTTP_404``)
    because ``HTTPException`` carries no machine-readable code of its own. Application
    code should raise an :class:`AppError` subclass instead, which does.
    """
    request_id = get_request_id()
    detail = exc.detail if isinstance(exc.detail, str) else "Request failed."

    logger.info(
        "http_exception status=%s request_id=%s path=%s detail=%s",
        exc.status_code,
        request_id,
        request.url.path,
        detail,
    )

    headers = dict(exc.headers or {})
    return _json_error(
        status_code=exc.status_code,
        code=f"HTTP_{exc.status_code}",
        message=detail,
        request_id=request_id,
        headers=headers or None,
    )


async def _unhandled_exception_handler(request: Request, exc: Exception) -> JSONResponse:
    """The catch-all — doc 07 §10.1: "logs with the request id and returns an opaque
    500 with an ``ehRef`` — never a stack trace".

    Two Starlette behaviours shape this handler, both verified against the installed
    version (starlette 1.6.0) rather than assumed:

    1. ``Starlette.build_middleware_stack`` (``applications.py``) pulls the handler
       registered for ``Exception`` (or ``500``) out of the ordinary handler map and
       hands it to ``ServerErrorMiddleware``, which wraps **outside** every user
       middleware. So this handler runs where no response middleware can see or
       post-process it — which is precisely why the success envelope cannot be
       implemented as a response-rewriting middleware and still be consistent with
       middleware, and still be consistent with error responses. See
       :mod:`app.common.routing`.
    2. ``ServerErrorMiddleware`` **re-raises** the exception after the response has
       been sent ("We always continue to raise the exception. This allows servers to
       log the error, or allows test clients to optionally raise the error within the
       test case."). The client still receives this envelope; the exception also
       reaches uvicorn, which logs its traceback to the server's stdout. That is the
       "real detail visible in server-side logs" half of the split — and it is why a
       test using httpx/TestClient against the ASGI app must pass
       ``raise_server_exceptions=False`` to observe the response at all.

    ``exc_info=True`` is what puts the traceback in the *server* log. Nothing about
    ``exc`` is interpolated into the response.
    """
    request_id = get_request_id()

    logger.error(
        "unhandled_exception request_id=%s method=%s path=%s exc_type=%s",
        request_id,
        request.method,
        request.url.path,
        type(exc).__name__,
        exc_info=True,
    )

    return _json_error(
        status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
        code=INTERNAL_ERROR_CODE,
        message=INTERNAL_ERROR_MESSAGE,
        request_id=request_id,
    )


def register_exception_handlers(app: FastAPI) -> None:
    """Install every handler doc 07 §10.1 requires.

    Order of registration does not matter to Starlette — it resolves a handler by
    walking the exception's MRO (``_lookup_exception_handler``), so the most specific
    registered class wins regardless of insertion order. The three P1-T5 classes are
    registered individually for that reason: registering only ``Exception`` would
    catch them, but as opaque 500s.
    """
    app.add_exception_handler(AppError, _app_error_handler)  # type: ignore[arg-type]

    # P1-T5's exceptions (app/db/exceptions.py) — untouched by this task.
    app.add_exception_handler(EntityNotFoundError, _db_error_handler)  # type: ignore[arg-type]
    app.add_exception_handler(OptimisticLockError, _db_error_handler)  # type: ignore[arg-type]
    app.add_exception_handler(TenantContextMissing, _db_error_handler)  # type: ignore[arg-type]

    app.add_exception_handler(
        RequestValidationError, _validation_exception_handler  # type: ignore[arg-type]
    )
    app.add_exception_handler(
        StarletteHTTPException, _http_exception_handler  # type: ignore[arg-type]
    )
    # Registered against ``Exception``: Starlette routes this one to
    # ServerErrorMiddleware (see _unhandled_exception_handler's docstring).
    app.add_exception_handler(Exception, _unhandled_exception_handler)  # type: ignore[arg-type]


__all__ = [
    "INTERNAL_ERROR_CODE",
    "INTERNAL_ERROR_MESSAGE",
    "AppError",
    "AuthenticationError",
    "AuthorizationError",
    "BusinessRuleError",
    "ConflictError",
    "NotFoundError",
    "RateLimitError",
    "UpstreamProviderError",
    "ValidationError",
    "register_exception_handlers",
]
