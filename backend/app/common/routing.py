"""``EnvelopeRoute`` — framework-level success-envelope wrapping (P1-T6).

The problem this solves
-----------------------
Doc 07 §6 describes the wrapping as middleware ("Return the response model
(middleware wraps in the envelope)" at the end of the write-path walkthrough;
"Envelope middleware wraps in the success shape" in the read path; and §3's folder
listing puts "response envelope" among ``middleware/``). But doc 07 §22.6 also
promises the OpenAPI spec is "generated automatically by FastAPI from the route
signatures and Pydantic models" and that "DTOs serve as the single source of truth
for request/response schemas".

Those two statements cannot both hold if the envelope is applied by an ASGI response
middleware. A middleware that rewrites the JSON body after FastAPI has serialised it
runs *after* schema generation and knows nothing about it, so the generated
``/api/openapi.json`` would describe the bare DTO while the wire carries the envelope
— and doc 08 §10.6's client generates its Zod parse from that contract. The spec
would be wrong about every single success response.

A custom ``APIRoute`` gets both properties at once: it rewrites the declared
``response_model`` from ``BranchOut`` to ``SuccessEnvelope[BranchOut]`` *before*
FastAPI builds the response field, so the generated schema is the enveloped shape,
and it wraps the handler's return value so the body matches. Handlers still return a
bare DTO and never mention the envelope — which is the actual requirement §6 was
expressing. This is recorded in the P1-T6 report as a deliberate departure from the
letter of §6 in favour of §22.6.

The remaining reason it cannot be a middleware
----------------------------------------------
Error responses do not pass through user middleware at all. Starlette hands the
handler registered for ``Exception`` to ``ServerErrorMiddleware``, which is installed
*outside* every ``add_middleware`` layer (verified in the installed
``starlette/applications.py``). An envelope middleware would therefore wrap success
responses and 4xx responses but not 500s — the exact inconsistency this task exists
to prevent. Building the shape at its source, in the handler and in
``errors.py``'s ``_json_error``, means all paths agree by construction.

Coupling to FastAPI internals
-----------------------------
Two things here are tied to FastAPI's implementation rather than its public API, both
verified against the pinned version (fastapi 0.141.1) rather than assumed:

1. ``fastapi.datastructures.Default`` / ``DefaultPlaceholder`` — the sentinel that
   distinguishes "no ``response_model`` given" from "``response_model=None`` given
   deliberately". ``APIRoute.__init__`` uses exactly this test.
2. ``functools.wraps`` is enough to keep the endpoint's dependency signature intact.
   ``fastapi.dependencies.utils.get_typed_signature`` resolves string annotations
   against ``getattr(inspect.unwrap(call), "__globals__", {})`` — it *unwraps*
   ``__wrapped__`` first, so a wrapper defined in this module still resolves the
   endpoint's own module globals. That is what makes a route module using
   ``from __future__ import annotations`` work; the verification script proves it
   with such a module rather than trusting this note.
"""

from __future__ import annotations

import functools
import inspect
from typing import Any, Callable, Optional

from fastapi import Response
from fastapi.datastructures import Default, DefaultPlaceholder
from fastapi.routing import APIRoute

from app.common.envelope import Meta, Pagination, SuccessEnvelope
from app.common.request_context import get_request_id


class Enveloped:
    """A handler return value carrying envelope extras alongside the payload.

    A handler that needs to set ``message`` or ``meta.pagination`` returns
    ``Enveloped(payload, message=..., pagination=...)`` instead of the bare payload.
    That is still not "constructing the envelope by hand": the handler names only the
    two fields it actually has an opinion about, and never ``success``, ``data`` or
    ``meta.requestId``.

    ``data`` is validated against the route's declared ``response_model``, so the
    OpenAPI schema is unaffected by whether a handler uses this wrapper or returns
    the payload directly.
    """

    __slots__ = ("data", "message", "pagination")

    def __init__(
        self,
        data: Any,
        *,
        message: Optional[str] = None,
        pagination: Optional[Pagination] = None,
    ) -> None:
        self.data = data
        self.message = message
        self.pagination = pagination


def _wrap_endpoint(
    endpoint: Callable[..., Any], envelope_cls: type[SuccessEnvelope[Any]]
) -> Callable[..., Any]:
    """Return a coroutine that calls ``endpoint`` and envelopes its result.

    ``envelope_cls`` is the *exact* parametrized class the route declared —
    ``SuccessEnvelope[BranchOut]``, not ``SuccessEnvelope[Any]``. That precision is
    load-bearing rather than cosmetic:

    ``serialize_response`` in the installed FastAPI validates the handler's return
    value directly against the response field (``field.validate(response_content)``);
    there is no dict-normalisation step in between. Pydantic's model validator accepts
    an input that is already an instance of the target class and otherwise expects a
    mapping. ``SuccessEnvelope[Any]`` and ``SuccessEnvelope[BranchOut]`` are *different
    classes* — pydantic builds and caches one concrete class per parametrization — so
    returning the former where the latter is expected fails validation with
    ``model_type``. Building the declared class here means the instance passes
    ``isinstance`` on the fast path, and ``data`` is still fully validated, because that
    happens at construction.
    """

    if inspect.iscoroutinefunction(endpoint):

        @functools.wraps(endpoint)
        async def async_wrapper(*args: Any, **kwargs: Any) -> Any:
            result = await endpoint(*args, **kwargs)
            return _to_envelope(result, envelope_cls)

        return async_wrapper

    @functools.wraps(endpoint)
    def sync_wrapper(*args: Any, **kwargs: Any) -> Any:
        result = endpoint(*args, **kwargs)
        return _to_envelope(result, envelope_cls)

    return sync_wrapper


def _to_envelope(result: Any, envelope_cls: type[SuccessEnvelope[Any]]) -> Any:
    """Wrap a handler result, unless it is already a raw ``Response``.

    A handler that returns a ``Response`` (a redirect, a file download, a
    ``StreamingResponse``) has opted out of both serialisation and enveloping —
    FastAPI passes those straight through, so wrapping here would produce a
    ``SuccessEnvelope`` whose ``data`` is an ASGI object and fail validation. Doc 07
    §22.5 has exactly such a case (PDF export "streams the file"), so this is a real
    path, not a hypothetical.
    """
    if isinstance(result, Response):
        return result

    if isinstance(result, Enveloped):
        return envelope_cls(
            data=result.data,
            message=result.message,
            meta=Meta(request_id=get_request_id(), pagination=result.pagination),
        )

    return envelope_cls(
        data=result,
        message=None,
        meta=Meta(request_id=get_request_id(), pagination=None),
    )


class EnvelopeRoute(APIRoute):
    """An ``APIRoute`` whose responses are enveloped, in body and in schema.

    Attach it once per router::

        router = APIRouter(route_class=EnvelopeRoute)

    A route can opt out with ``response_model=None``, which is how an endpoint that
    must return a bare body (doc 07 §24.1's health checks) stays bare while sitting on
    the same router. ``DefaultPlaceholder`` is what makes that distinguishable from
    simply not declaring a model.
    """

    def __init__(
        self,
        path: str,
        endpoint: Callable[..., Any],
        *,
        response_model: Any = Default(None),
        **kwargs: Any,
    ) -> None:
        inner_model = response_model

        if isinstance(inner_model, DefaultPlaceholder):
            # Same resolution APIRoute performs: fall back to the return annotation.
            # ``get_type_hints`` is used rather than raw ``__annotations__`` so a
            # module with ``from __future__ import annotations`` resolves properly.
            inner_model = _return_annotation(endpoint)

        # ``typing.get_type_hints`` returns ``type(None)`` (NoneType) for endpoints
        # annotated ``-> None``, not the Python literal ``None``.  Treat both the same.
        if inner_model is None or inner_model is type(None) or isinstance(inner_model, DefaultPlaceholder):
            # No model to wrap: leave the route completely alone. The endpoint is not
            # wrapped either, so its body reaches the client verbatim.
            # Pass ``response_model=None`` explicitly (not the DefaultPlaceholder) so
            # that newer FastAPI versions that assert "status 204 must not have a body"
            # do not mistake the placeholder for a declared body type.
            super().__init__(path, endpoint, response_model=None, **kwargs)
            self.envelope_inner_model: Any = None
            return

        if isinstance(inner_model, type) and issubclass(inner_model, Response):
            # Declared as returning a Response subclass: nothing to serialise.
            super().__init__(path, endpoint, response_model=None, **kwargs)
            self.envelope_inner_model = None
            return

        # Guard: if the caller registered a no-body status code (204, 205, etc.) but
        # forgot to annotate ``-> None``, skip envelope wrapping so FastAPI's strict
        # "status 204 must not have a body" assertion does not fire at startup.
        no_body_status = kwargs.get("status_code", 200)
        if no_body_status in (204, 205):
            super().__init__(path, endpoint, response_model=None, **kwargs)
            self.envelope_inner_model = None
            return

        # Built once and used for both the declared ``response_model`` and the
        # wrapper's construction, so the object the handler returns is an instance of
        # the very class the response field validates against. See ``_wrap_endpoint``.
        envelope_cls = SuccessEnvelope[inner_model]  # type: ignore[valid-type]

        super().__init__(
            path,
            _wrap_endpoint(endpoint, envelope_cls),
            response_model=envelope_cls,
            **kwargs,
        )
        #: The pre-envelope model, kept for introspection by tests and by the
        #: verification script (which asserts the OpenAPI schema really did change)
        #: and by the P2-T3 assertion on OpenAPI path count.
        self.envelope_inner_model = inner_model


def _return_annotation(endpoint: Callable[..., Any]) -> Any:
    """The endpoint's resolved return annotation, or ``None`` if there is none.

    Failure to resolve is swallowed deliberately: an unresolvable annotation (a
    ``TYPE_CHECKING``-only import, say) means "do not envelope" rather than "fail at
    import time". Declaring ``response_model=`` explicitly always works and is the
    documented way to be unambiguous.
    """
    try:
        import typing

        hints = typing.get_type_hints(endpoint, include_extras=True)
    except Exception:  # pragma: no cover - defensive
        return None
    return hints.get("return")


__all__ = ["Enveloped", "EnvelopeRoute"]
