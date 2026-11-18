"""The response envelope (P1-T6).

Every API response — success and error — conforms to one of two shapes. This module
defines both, plus the ``ehRef`` vocabulary they carry.

Sources (all three are named by ``IMPLEMENTATION_PLAN.md`` P1-T6)
----------------------------------------------------------------
* doc 07 §22.3 "Response Envelope" — the two JSON shapes, verbatim below.
* doc 02 "API Design Philosophy" → "Success envelope" / "Error envelope" — the same
  two shapes, field for field.
* doc 08 §10.6 / §25.3 — the *consumer* contract: the Zod parse at the client
  boundary, and the ``ApiError`` the client normalises every failure into.

Doc 07 §22.3 success:

    {
      "success": true,
      "data": { },
      "message": "Paper generated",
      "meta": {
        "requestId": "req_abc123",
        "pagination": { "page": 1, "pageSize": 20, "total": 142, "hasNext": true }
      }
    }

Doc 07 §22.3 error:

    {
      "success": false,
      "error": {
        "code": "QUOTA_EXCEEDED",
        "message": "Monthly paper generation limit reached.",
        "details": [],
        "ehRef": "EH-13"
      },
      "meta": { "requestId": "req_abc123" }
    }

camelCase, and why it is done with an alias generator
-----------------------------------------------------
The JSON is camelCase (``requestId``, ``pageSize``, ``hasNext``, ``ehRef``) while
the Python is snake_case — and doc 07's own backend code uses snake_case keyword
arguments for the same values (``eh_ref="EH-18"`` at §12.1, ``eh_ref="EH-9"`` at
§8.3). So the case conversion belongs at the serialization boundary, not in the
attribute names. ``alias_generator=to_camel`` with ``populate_by_name=True`` gives
both: construct with ``request_id=...``, serialise to ``requestId``.

``ehRef`` is the one field that alias generation gets *wrong* on its own: ``eh_ref``
camelises to ``ehRef`` correctly, but only because pydantic's ``to_camel`` uppercases
the letter after each underscore. That is verified by a unit assertion in the
verification script rather than assumed, because a silent drift to ``ehref`` would
break the client's Zod parse (doc 08 §10.6) with no server-side symptom.

Why ``details`` is a list
-------------------------
Doc 07 §10.1 shows ``"details": [{"field": "sections[2].marks", "issue": "..."}]``
and §22.3 shows ``"details": []`` — a list in both. Doc 08 §25.3 shows
``details: { … }`` (an object) in its illustrative snippet, but doc 08 line 6832
states it is "describing the contract it consumes" and that "when this document and
a sibling disagree, the sibling that owns the concept wins" — doc 07 owns the
envelope. List it is. Doc 08 §10.7's own ``ApiError.details`` comment ("drives 422 →
form field mapping") is satisfied by a list of ``{field, issue}`` objects.

Every envelope key is always present
------------------------------------
An earlier draft dumped with ``exclude_none=True`` so that an absent ``message``,
``pagination`` or ``ehRef`` disappeared rather than serialising as ``null``. That was
wrong, for a reason worth recording because it is not obvious:

Success responses are serialised by FastAPI from the route's ``response_model`` (see
``routing.EnvelopeRoute``), and FastAPI's default is ``exclude_none=False``. Error
responses are serialised here. Had this module excluded nulls, a 200 would carry
``"message": null`` while a 404 omitted ``ehRef`` entirely — two different shapes,
which is precisely the failure mode this task exists to eliminate. Turning
``response_model_exclude_none=True`` on instead would have been worse: it strips nulls
from ``data`` as well, so a nullable domain field (``Branch.region``, say) would
vanish from the payload and break a client whose schema declares it nullable rather
than optional.

So: no ``exclude_none`` anywhere, and doc 07 §22.3's shape is emitted key-for-key on
every response. ``null`` means "not applicable to this response"; a missing key means
a bug.
"""

from __future__ import annotations

from typing import Any, Generic, Optional, TypeVar

from pydantic import BaseModel, ConfigDict, Field
from pydantic.alias_generators import to_camel

DataT = TypeVar("DataT")


class EnvelopeModel(BaseModel):
    """Base for every envelope part: camelCase JSON, snake_case Python."""

    model_config = ConfigDict(
        alias_generator=to_camel,
        populate_by_name=True,
        # The envelope is a *response* shape. ``extra="forbid"`` (doc 07 §2.10) is a
        # rule for request models — applying it here would only make the server
        # brittle against its own additive changes, which doc 08 §10.15 explicitly
        # requires the client to tolerate ("Validation is tolerant on unknown
        # fields — additive backend changes must not break an older client").
        extra="ignore",
    )


class Pagination(EnvelopeModel):
    """``meta.pagination`` — doc 07 §22.3.

    Field names are taken from the doc's JSON exactly: ``page``, ``pageSize``,
    ``total``, ``hasNext``. Doc 07 §22.4 adds the server-side caps (``pageSize``
    default 20, max 100) and cursor pagination (``?cursor=...``). The *dependency* that
    parses and clamps those query parameters is deliberately not built in P1-T6: it has
    no caller until P1-T8's list routes exist, and the cap belongs next to the query it
    constrains. This model only carries the numbers a handler has already decided.
    """

    page: int = Field(ge=1)
    page_size: int = Field(ge=1)
    total: int = Field(ge=0)
    has_next: bool


class Meta(EnvelopeModel):
    """``meta`` — carries the correlation id and, on list responses, pagination.

    Doc 07 §19.3: the ``X-Request-Id`` correlation id is "Included in error
    responses (``meta.requestId``)". Doc 08 §10.6 extracts exactly three things from
    every response — ``data``, ``meta.pagination``, and ``meta.requestId`` — so both
    keys must be present under ``meta`` and nowhere else.

    ``pagination`` is ``null`` on a single-resource response rather than absent. See
    the module note "Every envelope key is always present" for why absence was
    rejected.
    """

    request_id: str
    pagination: Optional[Pagination] = None


class SuccessEnvelope(EnvelopeModel, Generic[DataT]):
    """The success envelope — doc 07 §22.3, doc 02 "Success envelope".

    Generic over the payload so that a route declaring
    ``response_model=SuccessEnvelope[BranchOut]`` produces a *correct* OpenAPI
    schema for the wrapped shape. This is what makes doc 07 §22.6's promise hold —
    "OpenAPI 3.1 spec generated automatically by FastAPI from the route signatures
    and Pydantic models" / "DTOs serve as the single source of truth for
    request/response schemas". A middleware that rewrote the body after the fact
    would leave the generated spec describing the unwrapped payload, i.e. lying.
    See :mod:`app.common.routing` for the full argument.
    """

    success: bool = True
    data: DataT
    message: Optional[str] = None
    meta: Meta


class ErrorDetail(EnvelopeModel):
    """One entry in ``error.details`` — doc 07 §10.1's ``{"field": ..., "issue": ...}``.

    Used for per-field validation errors (doc 07 §9 Layer 2: "Returns 422 with
    per-field errors") and by doc 08 §15.7 to map a 422 back onto form fields.
    """

    field: Optional[str] = None
    issue: str


class ErrorBody(EnvelopeModel):
    """``error`` — doc 07 §22.3 / §10.1.

    Doc 08 §25.3 spells out what each field is *for*, which is why all four are
    required rather than optional:

    * ``code`` "drives behaviour — the client switches on it to decide class,
      whether to retry, and which recovery to offer. It is stable across releases;
      message text is not."
    * ``message`` is "the default human-readable text".
    * ``details`` is "optional, field errors etc." — modelled as an empty list
      rather than a null, matching doc 07 §22.3's ``"details": []``.
    * ``ehRef`` "ties the failure to the platform's error-handling catalogue
      (EH-1 … EH-18)".
    """

    code: str
    message: str
    details: list[ErrorDetail] = Field(default_factory=list)
    eh_ref: Optional[str] = None


class ErrorEnvelope(EnvelopeModel):
    """The error envelope — doc 07 §22.3, doc 02 "Error envelope"."""

    success: bool = False
    error: ErrorBody
    meta: Meta


def success_payload(
    data: Any,
    *,
    request_id: str,
    message: Optional[str] = None,
    pagination: Optional[Pagination] = None,
) -> dict[str, Any]:
    """Build a success envelope as a JSON-ready dict.

    Only needed where a success response is *not* going through a route's
    ``response_model`` — currently nothing in P1-T6 does, so this exists for parity
    with :func:`error_payload` and for direct unit assertions on the shape.
    ``by_alias=True`` is what emits camelCase; nulls are kept (see the module note).
    """
    return SuccessEnvelope[Any](
        data=data,
        message=message,
        meta=Meta(request_id=request_id, pagination=pagination),
    ).model_dump(by_alias=True)


def error_payload(
    *,
    code: str,
    message: str,
    request_id: str,
    details: Optional[list[ErrorDetail]] = None,
    eh_ref: Optional[str] = None,
) -> dict[str, Any]:
    """Build an error envelope as a JSON-ready dict.

    The single construction point for every error body in the application — see
    ``errors._json_error``, which is the only caller. ``ehRef`` serialises as ``null``
    when the error class has no catalogue entry (see the module note on why it is not
    omitted); ``details`` is never None, so an empty list is always present, exactly
    as doc 07 §22.3 shows it.
    """
    return ErrorEnvelope(
        error=ErrorBody(
            code=code,
            message=message,
            details=details or [],
            eh_ref=eh_ref,
        ),
        meta=Meta(request_id=request_id),
    ).model_dump(by_alias=True)


__all__ = [
    "DataT",
    "EnvelopeModel",
    "ErrorBody",
    "ErrorDetail",
    "ErrorEnvelope",
    "Meta",
    "Pagination",
    "SuccessEnvelope",
    "error_payload",
    "success_payload",
]
