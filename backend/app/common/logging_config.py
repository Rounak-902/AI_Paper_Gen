"""Logging configuration (P1-T6) — doc 07 §19.1.

    "Structured JSON logging with **structlog**, written to **stdout**. Every log entry
     includes the correlation ID for end-to-end tracing. Nothing writes to a log file —
     the process emits to stdout and the platform (Docker, ECS, or Kubernetes) owns
     collection, rotation, and shipping."

Two parts of that are honoured exactly: JSON, and stdout only. One is not.

structlog is not installed
--------------------------
``structlog`` is absent from this project's dependency set (checked against the
installed ``backend/.venv``, not against ``pyproject.toml``'s intentions). Rather than
add a dependency in a task whose scope is the guard chain and envelope, this module
implements the two properties doc 07 actually depends on — JSON records on stdout, each
carrying the correlation id — using the stdlib. The correlation id is injected by
:class:`~app.common.request_context.RequestIdLogFilter`, which is the stdlib equivalent
of the "structlog processor" §19.3 describes.

This is reported as a deliberate deviation. Swapping in structlog later is a change to
this file and nothing else: no call site formats its own log line, and no module reads
the handler configuration.

Why uvicorn's own loggers are re-parented
-----------------------------------------
An unhandled exception's traceback is emitted by ``uvicorn.error``, not by application
code (see ``errors._unhandled_exception_handler``: Starlette's
``ServerErrorMiddleware`` re-raises after responding, and uvicorn logs what reaches
it). If uvicorn's loggers keep their default handlers, that traceback lands on stdout in
a different format and *without* the correlation id — which breaks the one thing doc 08
§25.3 promises a user contacting support: "requestId is surfaced discreetly on
unexpected errors so a user contacting support, and the team investigating, can
correlate to the exact request in the logs". Re-parenting them onto the same handler is
what makes the 500 envelope's ``requestId`` findable in the log.
"""

from __future__ import annotations

import datetime as _dt
import json
import logging
import sys
from typing import Any, Optional

from app.common.request_context import UNBOUND_REQUEST_ID, RequestIdLogFilter

#: Loggers whose records must carry the correlation id and use this formatter.
#: ``uvicorn.access`` is deliberately excluded: TimingMiddleware already emits one
#: line per request with method, path, status and duration (doc 07 §19.2's "Request
#: logs" row), so uvicorn's access log would duplicate it without the request id.
_MANAGED_LOGGERS = ("papergen", "uvicorn", "uvicorn.error", "sqlalchemy.engine")

#: Keys already present on every ``LogRecord``. Anything else in ``record.__dict__``
#: was attached by a filter or an ``extra=`` and belongs in the JSON output.
_STANDARD_RECORD_KEYS = frozenset(
    logging.LogRecord("", 0, "", 0, "", None, None).__dict__
) | {"message", "asctime", "taskName"}


class JsonFormatter(logging.Formatter):
    """One JSON object per line, on stdout."""

    def format(self, record: logging.LogRecord) -> str:
        payload: dict[str, Any] = {
            "ts": _dt.datetime.fromtimestamp(
                record.created, tz=_dt.timezone.utc
            ).isoformat(),
            "level": record.levelname,
            "logger": record.name,
            "message": record.getMessage(),
            "requestId": getattr(record, "request_id", UNBOUND_REQUEST_ID),
        }

        if record.exc_info:
            # The traceback goes to the log and only to the log. errors.py is the
            # counterpart: it never puts exception text into a response body.
            payload["exception"] = self.formatException(record.exc_info)

        for key, value in record.__dict__.items():
            if key in _STANDARD_RECORD_KEYS or key == "request_id":
                continue
            try:
                json.dumps(value)
            except (TypeError, ValueError):
                value = repr(value)
            payload[key] = value

        # ``default=str`` is a backstop: a log line must never be the thing that raises.
        return json.dumps(payload, default=str)


def configure_logging(
    level: str = "INFO", *, force: bool = True, stream: Optional[Any] = None
) -> logging.Handler:
    """Install the JSON stdout handler and the correlation-id filter.

    Returns the handler so a caller (a test, the verification script's in-process
    checks) can inspect or redirect it.

    ``force=True`` replaces existing handlers on the managed loggers. Without it,
    running under uvicorn produces every line twice — once in uvicorn's default format
    without a request id, once in JSON — which is exactly the ambiguity that makes a
    log unusable for correlation.
    """
    handler = logging.StreamHandler(stream or sys.stdout)
    handler.setFormatter(JsonFormatter())
    handler.addFilter(RequestIdLogFilter())

    numeric_level = getattr(logging, level.upper(), logging.INFO)

    root = logging.getLogger()
    if force:
        for existing in list(root.handlers):
            root.removeHandler(existing)
    root.addHandler(handler)
    root.setLevel(numeric_level)

    for name in _MANAGED_LOGGERS:
        logger = logging.getLogger(name)
        logger.setLevel(numeric_level)
        if force:
            for existing in list(logger.handlers):
                logger.removeHandler(existing)
        # propagate=True routes these through the root handler above, so there is
        # exactly one place that formats and exactly one place that writes.
        logger.propagate = True

    # SQLAlchemy echoes every statement at INFO when enabled. Doc 07 §19.2 wants slow
    # queries, not all queries, and P1-T6 has no slow-query hook — so the engine logger
    # is pinned to WARNING rather than left to inherit INFO and bury everything else.
    logging.getLogger("sqlalchemy.engine").setLevel(max(numeric_level, logging.WARNING))

    return handler


__all__ = ["JsonFormatter", "configure_logging"]
