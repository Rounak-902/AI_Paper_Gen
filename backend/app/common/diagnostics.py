"""Infrastructure probe routes (P1-T6) — **not** production API surface.

Why these exist
---------------
P1-T6's verification has to prove five things "against a real running app, not a
description of expected behavior": the success envelope, each of P1-T5's three
exceptions producing the right status and error envelope, the guard chain rejecting a
request before the handler runs, a Pydantic 422 in the same envelope, and an unhandled
exception producing an opaque 500 while the real detail reaches the log.

None of that can be shown without routes. P1-T8 owns the real tenancy CRUD and this
task must not pre-empt it, so these are deliberately *not* CRUD: they are the smallest
set of endpoints that exercise the plumbing this task built, several of them existing
only to fail in a specific way.

Safety
------
The router is mounted only when ``ENVIRONMENT`` is in
:data:`app.common.deps.STUB_AUTH_ENVIRONMENTS` (development, test, local) — see
``create_app``. ``/_diag/boom`` and ``/_diag/tenant-context-hole`` deliberately produce
500s and must never be reachable in a deployed environment. When P1-T8 lands real
routes, this module should be deleted rather than extended.

Every read here goes through the real chain — ``get_org_context`` -> ``get_db`` ->
``get_tenant_session`` (``SET LOCAL app.org_id``) -> ``BaseRepository`` -> DTO ->
``EnvelopeRoute`` — so a passing proof exercises P1-T5's session mechanism and P1-T6's
envelope together, not a stub of either.
"""

from __future__ import annotations

import datetime as _dt
import logging
import uuid
from typing import Optional

from fastapi import APIRouter, Depends
from pydantic import BaseModel, ConfigDict, Field
from pydantic.alias_generators import to_camel
from sqlalchemy.ext.asyncio import AsyncSession

from app.common.deps import (
    OrgContext,
    RequestScope,
    get_db,
    get_current_user,
    get_org_context,
    get_scope,
    require_roles,
)
from app.common.envelope import Pagination
from app.common.routing import Enveloped, EnvelopeRoute
from app.db.repository import BaseRepository
from app.db.session import get_tenant_session
from app.models.branch import Branch

logger = logging.getLogger("papergen.api.diagnostics")

#: Emitted by every probe handler *body*. Proof 3 asserts this marker is absent from
#: the server log for a request the guard chain rejected, and present for one it
#: allowed — which is what turns "the guard returned 403" into "the handler never ran".
HANDLER_MARKER = "diag_handler_body_executed"


class DiagModel(BaseModel):
    """Response DTOs: camelCase on the wire, like the envelope around them."""

    model_config = ConfigDict(alias_generator=to_camel, populate_by_name=True)


class BranchOut(DiagModel):
    """A branch, as an explicit field allowlist.

    Doc 07 §3 requires mappers with an "explicit field ALLOWLIST" and §11.4 the same
    for responses. Listing fields rather than using ``from_attributes`` on the ORM
    model is what stops a column added later (an internal note, a billing flag) from
    silently appearing in an API response.
    """

    id: uuid.UUID
    name: str
    region: Optional[str] = None
    status: str
    row_version: int
    created_at: _dt.datetime


class ContextOut(DiagModel):
    """The resolved guard-chain output, echoed back so it can be asserted on."""

    user_id: uuid.UUID
    org_id: uuid.UUID
    role: str
    roles: list[str]
    branch_id: Optional[uuid.UUID] = None
    subject_ids: Optional[list[uuid.UUID]] = None
    is_org_wide: bool


class PingOut(DiagModel):
    """The smallest possible enveloped payload."""

    pong: bool
    environment: str


class BranchUpdateIn(BaseModel):
    """A request model in ``extra="forbid"`` mode — doc 07 §6 and §9 Layer 2.

    ``extra="forbid"`` is the point of this model. Doc 07 §2.10: "An unknown field is
    a 400, not a silently ignored key"; §9: "unknown fields are rejected, not
    stripped ... Reject: 422 with per-field error details". The status contradiction
    between those two sentences is one of this task's reported ambiguities; the
    handler for ``RequestValidationError`` resolves it to 422.

    Doc 07 §20.1 gives the security reason the mode is not merely tidy: it is the
    mass-assignment defence. Without it, ``{"name": "x", "org_id": "<other tenant>"}``
    is a request the server would have to remember to ignore.
    """

    model_config = ConfigDict(
        alias_generator=to_camel,
        populate_by_name=True,
        extra="forbid",
    )

    name: str = Field(min_length=1, max_length=200)
    expected_version: int = Field(ge=1)


# ---------------------------------------------------------------------------
# Unguarded router — proves the envelope without any auth in the way
# ---------------------------------------------------------------------------
public_router = APIRouter(
    prefix="/api/v1/_diag",
    tags=["Diagnostics"],
    route_class=EnvelopeRoute,
)


@public_router.get("/ping", response_model=PingOut, summary="Enveloped liveness probe")
async def ping() -> PingOut:
    """A success envelope with no guards involved.

    Contrast with ``/health``, which is deliberately **not** enveloped: doc 07 §24.1
    describes health checks as an operator/load-balancer surface rather than part of
    the API contract, and ``tests/test_health.py`` asserts its bare body.
    """
    from app.core.config import get_settings

    logger.info("%s route=ping", HANDLER_MARKER)
    return PingOut(pong=True, environment=get_settings().ENVIRONMENT)


@public_router.get(
    "/boom",
    response_model=PingOut,
    summary="Deliberately raises — proves the opaque 500",
)
async def boom() -> PingOut:
    """Raise an exception whose message must never reach the client.

    The message is written to look like a leak (a connection string with a password)
    so that proof 5 is checking something real: the verification script asserts this
    exact substring is present in the server log and absent from the response body.
    """
    logger.info("%s route=boom", HANDLER_MARKER)
    raise RuntimeError(
        "SENTINEL_LEAK_CANARY: connection to "
        "postgresql://app_user:hunter2@db.internal:5432/papergen failed"
    )


# ---------------------------------------------------------------------------
# Guarded router — deny by default (doc 07 §7.5)
# ---------------------------------------------------------------------------
#: The chain is declared once, on the router. Doc 07 §7.5: "Routers declare the chain
#: once ... Nothing is public unless it is explicitly declared so." Declaring it here
#: rather than per-endpoint means a new route added to this router is guarded by
#: omission, which is the safe direction to be forgetful in.
guarded_router = APIRouter(
    prefix="/api/v1/_diag",
    tags=["Diagnostics"],
    route_class=EnvelopeRoute,
    dependencies=[Depends(get_current_user), Depends(get_org_context)],
)


@guarded_router.get(
    "/context",
    response_model=ContextOut,
    summary="Echo the resolved guard-chain context",
)
async def read_context(scope: RequestScope = Depends(get_scope)) -> ContextOut:
    """Proves ``get_current_user`` -> ``get_org_context`` -> ``get_scope`` all ran.

    Also the negative half of proof 3: when the guard chain rejects the request, the
    :data:`HANDLER_MARKER` line below is absent from the log.
    """
    logger.info("%s route=context org_id=%s", HANDLER_MARKER, scope.org_id)
    return ContextOut(
        user_id=scope.user_id,
        org_id=scope.org_id,
        role=scope.role,
        roles=[scope.role],
        branch_id=scope.branch_id,
        subject_ids=list(scope.subject_ids) if scope.subject_ids is not None else None,
        is_org_wide=scope.is_org_wide,
    )


@guarded_router.get(
    "/branches",
    response_model=list[BranchOut],
    summary="Real tenant-scoped read through P1-T5's session + repository",
)
async def list_branches(
    org_context: OrgContext = Depends(get_org_context),
    session: AsyncSession = Depends(get_db),
) -> Enveloped:
    """Proof 1: a success envelope over data that came out of PostgreSQL.

    The path exercised is the whole stack: the guard chain resolved the org, ``get_db``
    opened a transaction and issued ``set_config('app.org_id', ..., true)``,
    ``BaseRepository`` applied the ``org_id`` predicate and the ``deleted_at IS NULL``
    filter, and ``EnvelopeRoute`` wrapped the result. Nothing here is mocked.

    ``Enveloped`` is used so ``meta.pagination`` is populated — the only place in P1-T6
    where a handler has an opinion about an envelope field. It still never names
    ``success``, ``data`` or ``meta.requestId``.
    """
    logger.info("%s route=branches org_id=%s", HANDLER_MARKER, org_context.org_id)

    repo: BaseRepository[Branch] = BaseRepository(
        session, org_context.org_id, model=Branch
    )
    rows = await repo.find_many(order_by=Branch.name)
    items = [
        BranchOut(
            id=row.id,
            name=row.name,
            region=row.region,
            status=row.status,
            row_version=row.row_version,
            created_at=row.created_at,
        )
        for row in rows
    ]
    return Enveloped(
        items,
        message="Branches listed",
        pagination=Pagination(
            page=1, page_size=len(items) or 1, total=len(items), has_next=False
        ),
    )


@guarded_router.get(
    "/branches/{branch_id}",
    response_model=BranchOut,
    summary="Raises EntityNotFoundError for real when the id does not exist",
)
async def get_branch(
    branch_id: uuid.UUID,
    org_context: OrgContext = Depends(get_org_context),
    session: AsyncSession = Depends(get_db),
) -> BranchOut:
    """Proof 2a: ``EntityNotFoundError`` -> 404.

    Raised by ``BaseRepository.get_by_id_or_raise`` against a real query — not
    constructed by hand here — so the proof covers P1-T5's code path, not a re-creation
    of it.
    """
    logger.info("%s route=get_branch id=%s", HANDLER_MARKER, branch_id)
    repo: BaseRepository[Branch] = BaseRepository(
        session, org_context.org_id, model=Branch
    )
    row = await repo.get_by_id_or_raise(branch_id)
    return BranchOut(
        id=row.id,
        name=row.name,
        region=row.region,
        status=row.status,
        row_version=row.row_version,
        created_at=row.created_at,
    )


@guarded_router.patch(
    "/branches/{branch_id}",
    response_model=BranchOut,
    summary="Raises OptimisticLockError for real on a row_version mismatch",
)
async def update_branch(
    branch_id: uuid.UUID,
    payload: BranchUpdateIn,
    org_context: OrgContext = Depends(get_org_context),
    session: AsyncSession = Depends(get_db),
) -> BranchOut:
    """Proof 2b (409) and proof 4 (422), from one endpoint.

    Sending a stale ``expectedVersion`` against an existing row makes
    ``BaseRepository.update`` issue a real ``UPDATE ... WHERE row_version = :n`` that
    matches nothing, and P1-T5's own code then distinguishes "absent" from "stale" and
    raises ``OptimisticLockError``.

    Sending an unknown field, or a wrong-typed one, trips ``BranchUpdateIn``'s
    ``extra="forbid"`` and produces a ``RequestValidationError`` — the 422 half.
    """
    logger.info("%s route=update_branch id=%s", HANDLER_MARKER, branch_id)
    repo: BaseRepository[Branch] = BaseRepository(
        session, org_context.org_id, model=Branch
    )
    row = await repo.update(
        branch_id, payload.expected_version, name=payload.name
    )
    return BranchOut(
        id=row.id,
        name=row.name,
        region=row.region,
        status=row.status,
        row_version=row.row_version,
        created_at=row.created_at,
    )


@guarded_router.get(
    "/tenant-context-hole",
    response_model=PingOut,
    summary="Raises TenantContextMissing for real, bypassing the guard chain",
)
async def tenant_context_hole() -> PingOut:
    """Proof 2c: ``TenantContextMissing`` -> 500.

    This route simulates the only situation in which that exception can occur: code
    that opens a session **without** going through ``get_db``, and therefore without a
    resolved org. It calls P1-T5's ``get_tenant_session(None)`` directly, which is
    exactly what P1-T5's docstring warns about — "the guard chain should have resolved
    the org context before the code reached a repository. If this fires in production,
    the dependency chain has a hole."

    Note what this route does *not* do: it is on the guarded router, so the guard chain
    ran and succeeded. The hole is below the guard, which is why the mapping is 500 and
    not the 403 that a guard-level failure produces. Proof 3 shows the 403 side.
    """
    logger.info("%s route=tenant_context_hole", HANDLER_MARKER)
    async with get_tenant_session(None):  # type: ignore[arg-type]
        pass  # pragma: no cover - unreachable, the context manager raises
    return PingOut(pong=False, environment="unreachable")


@guarded_router.get(
    "/admin-only",
    response_model=ContextOut,
    summary="require_roles guard — 403 for a teacher",
    dependencies=[Depends(require_roles("coaching_admin", "super_admin"))],
)
async def admin_only(scope: RequestScope = Depends(get_scope)) -> ContextOut:
    """Proves ``require_roles`` narrows the router default (doc 07 §7.5)."""
    logger.info("%s route=admin_only role=%s", HANDLER_MARKER, scope.role)
    return ContextOut(
        user_id=scope.user_id,
        org_id=scope.org_id,
        role=scope.role,
        roles=[scope.role],
        branch_id=scope.branch_id,
        subject_ids=list(scope.subject_ids) if scope.subject_ids is not None else None,
        is_org_wide=scope.is_org_wide,
    )


__all__ = [
    "HANDLER_MARKER",
    "BranchOut",
    "BranchUpdateIn",
    "ContextOut",
    "PingOut",
    "guarded_router",
    "public_router",
]
