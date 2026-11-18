"""Per-request correlation id, held in a contextvar (P1-T6).

Doc 07 §19.3 "Correlation ID", verbatim:

    "Every request generates (or propagates) an ``X-Request-Id``. This ID is:
     - Attached to all log entries during the request
     - Propagated into queued jobs (``requestId`` in job data)
     - Included in error responses (``meta.requestId``)
     - Used to trace a generation request across API -> queue -> worker -> Gemini -> DB

     The correlation ID is bound to a ``contextvar`` at the start of the request and
     injected into every log line by a structlog processor, so no function has to
     thread it through its arguments."

Note what that list does **not** contain: a response header. Doc 07 §6 mentions
"Correlation ID (X-Request-Id generation/propagation ...)" for the middleware but
names no response header either, and doc 08 §10.3 step 4 has the *client* attaching
``X-Request-Id`` on the way out and then reading ``meta.requestId`` out of the
response body — not a header. So the ``X-Request-Id`` response header this module
supports is a deliberate addition on top of the documented contract (it is what lets
a proxy, load-balancer log, or curl session correlate without parsing the body), and
"propagates" is the part that *is* required: an inbound id from doc 08's client must
survive into the log lines rather than being replaced by a fresh one.

Why a contextvar rather than ``request.state``
----------------------------------------------
``request.state.request_id`` would work everywhere a ``Request`` is in scope, but
that is not everywhere the id is needed. The exception handler registered for
``Exception`` runs inside ``ServerErrorMiddleware``, which sits *outside* the
middleware that would set ``request.state`` — and repository code, the future job
enqueuer, and log records have no ``Request`` at all. A ``ContextVar`` is visible to
all of them, and asyncio copies the context per task so concurrent requests cannot
see each other's id.

Doc 07 §19.1 specifies **structlog**, and §19.3 above names the ``contextvar`` binding
explicitly. structlog is not in this project's dependency set, so this module uses the
stdlib ``contextvars`` primitive structlog itself wraps, and
:class:`RequestIdLogFilter` attaches the id to stdlib log records so the "injected into
every log line" requirement holds today. Swapping in structlog later replaces this
module's internals without touching its callers. Flagged in the P1-T6 report as a
deliberate deviation, not an oversight.
"""

from __future__ import annotations

import logging
import uuid
from contextvars import ContextVar, Token
from typing import Optional

#: Header name — doc 07 §19.3 and doc 08 §10.3 both name ``X-Request-Id``. Doc 08's
#: client sends it on the way *out* (§10.3 step 4, "attach X-Request-Id
#: (client-generated correlation)"), which is why an inbound value is propagated
#: rather than overwritten.
REQUEST_ID_HEADER = "X-Request-Id"

#: Doc 07 §22.3's example value is ``"req_abc123"``, i.e. a ``req_`` prefix rather
#: than a bare UUID. The prefix is kept because it makes an id recognisable on sight
#: in a log grep, and because doc 02 says the id is "propagated into queued jobs"
#: where a prefixed token is easier to spot than one more UUID among many.
REQUEST_ID_PREFIX = "req_"

#: Cap on an inbound client-supplied id. Doc 07 §19.3 says the id is "generated if
#: the client did not supply one", which means untrusted input reaches a response
#: header and every log line. Without a cap a client could push a megabyte of text
#: into both. See :func:`normalize_request_id`.
MAX_REQUEST_ID_LENGTH = 128

_request_id: ContextVar[Optional[str]] = ContextVar("papergen_request_id", default=None)

#: Substituted when no id has been bound — which should only happen outside a request
#: (a CLI command, a test that calls a handler directly, a worker startup log). A
#: sentinel is returned rather than raising, because failing to produce a *log line*
#: is strictly worse than logging one with an unknown correlation id.
UNBOUND_REQUEST_ID = "req_unbound"


def generate_request_id() -> str:
    """A fresh correlation id: ``req_`` + a hex UUID4."""
    return f"{REQUEST_ID_PREFIX}{uuid.uuid4().hex}"


def normalize_request_id(raw: Optional[str]) -> str:
    """Sanitise a client-supplied ``X-Request-Id``, or mint a new one.

    An inbound id is echoed back in a response header and interpolated into log
    lines, so it is treated as untrusted input on three specific grounds:

    * **Length** — truncated to :data:`MAX_REQUEST_ID_LENGTH`.
    * **Control characters** — ``\\r`` and ``\\n`` in a value that is written to a
      response header is response-splitting, and in a value written to a log line is
      log forging (an attacker fabricating extra log records). Any character outside
      a conservative allowlist causes the value to be rejected outright rather than
      stripped, because a stripped id no longer matches what the client sent and is
      therefore useless for correlation anyway.
    * **Emptiness** — a blank or whitespace-only header is treated as absent.

    Doc 07 §19.3 says the id is "generated (or propagate[d])" but says nothing about
    validating an inbound value. This is a deliberate addition, reported as such: doc
    08 §10.3 step 4 has the client supplying the header, so the value is attacker-
    controllable in practice, and doc 07 §20.1 lists "Header injection" among the
    input-validation concerns — rejecting a header that would enable it is consistent
    with the document even though §19.3 is silent.
    """
    if raw is None:
        return generate_request_id()

    candidate = raw.strip()
    if not candidate:
        return generate_request_id()

    if len(candidate) > MAX_REQUEST_ID_LENGTH:
        candidate = candidate[:MAX_REQUEST_ID_LENGTH]

    # Allowlist: the characters that appear in real correlation ids (UUIDs, ULIDs,
    # W3C traceparent values, and the doc's own "req_abc123").
    if not all(c.isalnum() or c in "-_.:" for c in candidate):
        return generate_request_id()

    return candidate


def bind_request_id(request_id: str) -> Token:
    """Bind ``request_id`` for the current context; returns the reset token.

    The caller (the middleware) must reset with :func:`reset_request_id` in a
    ``finally`` block. Leaking a binding would let a later request on the same task
    inherit a stale id.
    """
    return _request_id.set(request_id)


def reset_request_id(token: Token) -> None:
    """Undo a :func:`bind_request_id`."""
    _request_id.reset(token)


def get_request_id() -> str:
    """The current correlation id, or :data:`UNBOUND_REQUEST_ID` outside a request.

    Never raises and never returns None: ``meta.requestId`` is a required envelope
    field (doc 07 §22.3), so a missing id must not be able to turn a 404 into a
    500 from a serialisation failure.
    """
    return _request_id.get() or UNBOUND_REQUEST_ID


class RequestIdLogFilter(logging.Filter):
    """Attach ``request_id`` to every stdlib log record.

    Doc 07 §19.3: the id is "attached to every log line for that request". A
    ``logging.Filter`` is the stdlib mechanism for injecting a field into records
    emitted by code that knows nothing about it — including uvicorn's own loggers,
    which is what makes the traceback of an unhandled exception correlatable to the
    500 envelope the client received.
    """

    def filter(self, record: logging.LogRecord) -> bool:
        record.request_id = get_request_id()  # type: ignore[attr-defined]
        return True


__all__ = [
    "MAX_REQUEST_ID_LENGTH",
    "REQUEST_ID_HEADER",
    "REQUEST_ID_PREFIX",
    "UNBOUND_REQUEST_ID",
    "RequestIdLogFilter",
    "bind_request_id",
    "generate_request_id",
    "get_request_id",
    "normalize_request_id",
    "reset_request_id",
]
