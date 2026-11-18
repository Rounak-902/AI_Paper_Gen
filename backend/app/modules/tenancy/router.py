"""Tenancy endpoints (P1-T8) — doc 07 §4.2's routes, plus the ones it omitted.

Thin by design, same as ``app/modules/auth/router.py`` (doc 07 §11: the router does
HTTP, the service does work). Nothing here makes a security decision on its own; the
guards are declarations and the checks live in
:mod:`app.modules.tenancy.service`.

Four routers, not one
---------------------
The split is by *what the request is allowed to assume*, and it is the reason this
module has four ``APIRouter`` objects instead of one with per-route dependencies:

``org_router``
    Requires an ``activeOrgId``. Every route takes ``ctx`` from
    ``require_roles(...)`` or ``get_org_context``, and a ``session`` from
    ``get_db`` — so ``app.org_id`` is set and RLS is doing tenant isolation.

``identity_router``
    Requires only authentication. ``POST /orgs`` cannot require an org context
    because it *creates* the org that would provide one, and ``GET /orgs`` is the
    question "which orgs?" — asking it inside one org's context is a contradiction.
    These routes take no ``session``; their services open identity-scoped ones (see
    ``app/db/session.py``'s ``get_identity_session``).

``principal_router``
    ``GET /auth/me`` and ``POST /auth/switch-org``, under an ``/api/v1/auth`` prefix
    and an ``Auth`` OpenAPI tag. Same guarantee as ``identity_router`` —
    authentication only, no org context — but a different prefix, so it is a
    separate object rather than two routes with a hand-written full path.

``public_router``
    ``POST /invitations/accept`` only. Unauthenticated, because the invitee has no
    account yet.

Why two auth-prefixed routes live in the *tenancy* module
--------------------------------------------------------
They look like they belong in ``app/modules/auth/router.py``, and doc 07 §4.2 puts
one of them here explicitly: ``GET /auth/me`` is listed under the **Tenancy**
module's routes, not Auth's. The reason is the dependency direction stated in the
same section — "Tenancy depends on: Auth module" — and it is one-way. Both
endpoints need tenancy data (``memberships``) and tenancy logic
(``authorize_org_switch``), so hosting them in ``auth`` would require
``auth`` to import ``tenancy`` and invert that arrow, producing a circular import
between the two modules that the layering exists to prevent. The URL prefix is a
client-facing grouping; the module boundary is a code-dependency fact, and they are
allowed to disagree.

REPORTED — ``app/modules/tenancy/__init__.py`` originally noted that ``/auth/me``
would live in ``auth/router.py``. That note was written before the import direction
was checked and is corrected there as well as here.

Doc 07 §7.5's "Nothing is public unless it is explicitly declared so" is honoured by
``public_router`` existing as a separate, named, one-route object with this docstring
attached, rather than by an absent ``dependencies=[...]`` on a router that also
carries data-bearing endpoints.

Role assignments, and where they come from
------------------------------------------
Doc 07 §4.2 annotates **no roles on any of its eight routes**, so every
``require_roles(...)`` below is derived from §8.1's permission matrix and listed in
the P1-T8 report. The matrix row that does most of the work is::

    | Manage users | No (teacher) | Yes (own org) (coaching_admin) | Yes (any org) (super_admin) |

which gives: reads open to any member, writes to ``coaching_admin``/``super_admin``,
and — because §8.1 defines ``super_admin`` as the cross-tenant platform role —
organization deletion to ``super_admin`` alone.

REPORTED GAP — doc 07 §4.2 documents no DELETE route for anything, and no list route
for invitations or memberships. The brief requires soft-delete and
"create/list/revoke", and ``PATCH /memberships/:id`` needs an id and a ``rowVersion``
the client has no documented way to obtain. Every added route is marked ADDED in its
docstring.
"""

from __future__ import annotations

import logging
import uuid
from typing import Optional

from fastapi import APIRouter, Depends, Path, Query, Request, status
from sqlalchemy.ext.asyncio import AsyncSession

from app.common.deps import (
    BEARER_SCHEME,
    AuthenticatedUser,
    OrgContext,
    get_current_user,
    get_db,
    get_org_context,
    require_roles,
)
from app.common.envelope import Pagination
from app.common.errors import AuthenticationError
from app.common.routing import Enveloped, EnvelopeRoute
from app.core.config import Settings, get_settings
from app.modules.auth.schemas import ActiveOrgOut, SwitchOrgIn, SwitchOrgOut
from app.modules.auth.security import (
    AccessTokenClaims,
    InvalidToken,
    TokenExpired,
    decode_access_token,
)
from app.modules.tenancy import service
from app.modules.tenancy.schemas import (
    BranchCreateIn,
    BranchDeleteIn,
    BranchOut,
    BranchUpdateIn,
    DeletedOut,
    InvitationAcceptedOut,
    InvitationAcceptIn,
    InvitationCreatedOut,
    InvitationCreateIn,
    InvitationOut,
    MembershipOut,
    MembershipUpdateIn,
    OrgCreateIn,
    OrgDeleteIn,
    OrgMembershipOut,
    OrgOut,
    OrgUpdateIn,
    PrincipalOut,
)

logger = logging.getLogger("papergen.api.tenancy")

#: Doc 07 §8.1's "Manage users → Yes (own org) / Yes (any org)". Everything that
#: changes an organization, a branch, an invitation or a membership.
_ADMIN_ROLES = ("coaching_admin", "super_admin")

#: Doc 07 §22.4: "``pageSize`` default 20, max 100".
_PAGE_SIZE_DEFAULT = 20
_PAGE_SIZE_MAX = 100


class PageParams:
    """``?page=&pageSize=`` — doc 07 §22.4's caps, as a dependency.

    P1-T6's ``app/common/envelope.py`` notes that this dependency was deliberately
    left unbuilt because "it has no caller until P1-T8's list routes exist, and the
    cap belongs next to the query it constrains". These are those routes, so here it
    is.

    ``page`` is 1-based because doc 07 §22.3's ``pagination`` object is
    (``page``, ``pageSize``, ``total``, ``hasNext``) with ``"page": 1`` in its
    example. ``offset`` is derived rather than accepted, so a client cannot request
    a page boundary that does not line up with ``pageSize``.

    ``limit`` fetches **one row more** than the page size. That extra row is never
    returned; its presence is how ``hasNext`` is computed without a second
    ``COUNT(*)`` query. See :func:`_paginate`.
    """

    def __init__(
        self,
        page: int = Query(default=1, ge=1),
        page_size: int = Query(
            default=_PAGE_SIZE_DEFAULT,
            ge=1,
            le=_PAGE_SIZE_MAX,
            alias="pageSize",
        ),
    ) -> None:
        self.page = page
        self.page_size = page_size

    @property
    def offset(self) -> int:
        return (self.page - 1) * self.page_size

    @property
    def probe_limit(self) -> int:
        return self.page_size + 1


def _paginate(items: list, params: PageParams) -> Enveloped:
    """Trim the look-ahead row and build ``meta.pagination``.

    REPORTED GAP — ``total`` is not a true total. Doc 07 §22.3's ``pagination``
    object has a ``total`` field, and computing it honestly needs a ``COUNT(*)``
    over the same filters, which ``BaseRepository`` has no method for. Adding one
    changes ``app/db/repository.py``, a P1-T5 file this task has no mandate over.

    What is reported instead is stated rather than faked: ``total`` carries the
    number of rows *on this page*, and ``hasNext`` is **exact** because it is
    derived from the look-ahead row. A client paginating with ``hasNext`` gets
    correct behaviour; a client rendering "showing 20 of N" would be misled, so the
    report lists ``count()`` on the base repository as the follow-up.
    """
    has_next = len(items) > params.page_size
    page_items = items[: params.page_size]
    return Enveloped(
        page_items,
        pagination=Pagination(
            page=params.page,
            page_size=params.page_size,
            total=len(page_items),
            has_next=has_next,
        ),
    )


def client_ip(request: Request) -> Optional[str]:
    """The caller's IP for ``audit_logs.ip``, or None.

    ``request.client.host`` only — ``X-Forwarded-For`` is deliberately **not**
    consulted. That header is caller-supplied and trivially forged, and this value
    goes into an append-only table with no UPDATE grant, so a forged entry is
    permanent. Behind a reverse proxy this therefore records the proxy's address,
    which is honest-but-useless rather than wrong.

    The correct fix is Uvicorn's ``--proxy-headers`` with ``--forwarded-allow-ips``
    set to the actual proxy, which makes ``request.client.host`` itself the real
    client address and keeps the trust decision in deployment configuration where it
    belongs. Recorded in the P1-T8 report as a deployment follow-up rather than
    papered over with a header read here.

    ``audit_logs.ip`` is ``INET``, so an unparseable value would be a database
    error; ``request.client`` is None for ASGI transports without a peer (an
    in-process ``ASGITransport`` test client, for instance), which is why the return
    is optional and the column is nullable.
    """
    return request.client.host if request.client else None


# ===========================================================================
# Identity-scoped routes — authentication only, no org context
# ===========================================================================
identity_router = APIRouter(
    prefix="/api/v1",
    tags=["Tenancy"],
    route_class=EnvelopeRoute,
    dependencies=[Depends(get_current_user)],
)


@identity_router.post("/orgs", status_code=status.HTTP_201_CREATED)
async def create_org(
    payload: OrgCreateIn,
    request: Request,
    current_user: AuthenticatedUser = Depends(get_current_user),
) -> OrgOut:
    """Doc 07 §4.2 ``POST /orgs``. Guarded by authentication alone.

    There is no role that could be required here: in the bootstrap case the caller
    has no membership anywhere, so ``get_org_context`` would have nothing to resolve.
    REPORTED — this means any authenticated user can create organizations without
    limit. A per-user cap belongs with the login rate-limiting work the brief
    reserves for a separate task, so it is recommended in the report rather than
    added here.

    201 with the created resource, per doc 07 §4.2's REST conventions. No
    ``Location`` header: doc 07 §22 does not use them and doc 08's client reads the
    id out of ``data``.
    """
    return await service.create_organization(
        actor_user_id=current_user.user_id,
        payload=payload,
        ip=client_ip(request),
    )


@identity_router.get("/orgs")
async def list_orgs(
    current_user: AuthenticatedUser = Depends(get_current_user),
) -> list[OrgMembershipOut]:
    """Doc 07 §4.2 ``GET /orgs`` — "list user's organizations".

    Not paginated, deliberately. Pagination exists to bound an unbounded result set,
    and this one is bounded by how many organizations one person belongs to. Adding
    ``?page=`` would also mean the org-picker (doc 08 §6.5) could render an
    incomplete list of the caller's own orgs, which is a worse failure than a large
    response.

    The identity this answers for comes from the verified JWT ``sub`` claim and is
    never a parameter — see
    :meth:`app.modules.tenancy.repository.IdentityRepository._self_memberships`.
    """
    return await service.list_own_orgs(actor_user_id=current_user.user_id)


# ===========================================================================
# Principal routes — /api/v1/auth/{me,switch-org}; authentication only
# ===========================================================================
async def access_claims(
    request: Request,
    settings: Settings = Depends(get_settings),
) -> AccessTokenClaims:
    """Re-decode the bearer token to recover the ``sid`` claim.

    REPORTED COST OF A CONSTRAINT, not a design preference. ``POST /switch-org``
    must call ``create_access_token``, whose signature requires ``session_id``, and
    the ``sid`` claim is the only place that value exists on a request.
    :class:`app.common.deps.AuthenticatedUser` does not carry it — it exposes
    ``user_id``, ``roles``, ``requested_org_id`` and ``is_stub`` — so the token is
    decoded a second time here purely to read one field the first decode already
    had in hand.

    The one-line fix is to add ``session_id: str`` to ``AuthenticatedUser`` and set
    it from ``claims.session_id`` in ``get_current_user``. That is a two-token edit
    to ``get_current_user``'s return statement, which this task's brief forbids
    ("Don't modify ``get_current_user`` ... in ``deps.py`` — only ``get_scope``"),
    so the cost is paid here and named in the report rather than the constraint being
    quietly broken. Recommended as the first cleanup in P1-T9.

    This is redundant work, **not a second security decision.** It calls the same
    ``decode_access_token`` with the same ``settings`` that ``get_current_user``
    already called, so it cannot reach a different verdict about the same token —
    there is no way for a token to pass one and fail the other. What it does not
    repeat is the Redis revocation check, and it does not need to: the router-level
    ``Depends(get_current_user)`` has already performed it and rejected the request
    if the session was revoked.

    The ``except`` arms are therefore unreachable in practice — ``get_current_user``
    is prepended to this route's dependency list by FastAPI and runs first. They
    exist so that if the ordering ever changes, the failure is a 401 with the
    module's standard generic message rather than an unhandled ``InvalidToken``
    surfacing as a 500.
    """
    header = request.headers.get("Authorization") or ""
    scheme, _, raw_token = header.partition(" ")
    token = raw_token.strip()
    if scheme.lower() != BEARER_SCHEME or not token:
        raise AuthenticationError(
            "Authentication is required.",
            log_detail="access_claims: no usable bearer token on an authenticated route",
        )
    try:
        return decode_access_token(token, settings)
    except TokenExpired as exc:
        raise AuthenticationError(
            "Authentication is required.", eh_ref="EH-15", log_detail=f"token expired: {exc}"
        ) from exc
    except InvalidToken as exc:
        raise AuthenticationError(
            "Authentication is required.", log_detail=f"token rejected: {exc}"
        ) from exc


principal_router = APIRouter(
    prefix="/api/v1/auth",
    # Tagged ``Auth`` rather than ``Tenancy`` so the generated OpenAPI groups these
    # with login/refresh/logout, which is where a client author looks for them. The
    # module docstring explains why the *code* lives here regardless.
    tags=["Auth"],
    route_class=EnvelopeRoute,
    dependencies=[Depends(get_current_user)],
)


@principal_router.get("/me")
async def read_me(
    current_user: AuthenticatedUser = Depends(get_current_user),
) -> PrincipalOut:
    """Doc 07 §4.2 ``GET /auth/me`` — listed under the Tenancy module's routes.

    Authentication only, and deliberately **not** ``get_org_context``. A user who
    belongs to more than one organization logs in without an ``activeOrgId`` (see
    ``app/modules/auth/schemas.py``'s ``LoginIn.org_id``), so requiring an org
    context here would 403 exactly the callers who most need to ask "who am I and
    what orgs do I have?" before they can choose one. Doc 08 §6.5's org picker is
    that flow.

    ``activeOrgId`` and ``activeRole`` are read straight off the resolved
    ``AuthenticatedUser`` — that is, off the verified JWT claims — and are ``None``
    for an org-less token. They are reported as what the *token* says, not
    re-derived from ``memberships``; the ``memberships`` list beside them is the
    authoritative set, and a disagreement between the two is a real thing a client
    should be able to see (it means the caller's role changed since the token was
    minted, and ``POST /switch-org`` is the fix).

    No re-decode here: ``AuthenticatedUser`` already carries everything this route
    needs, so it does not pay :func:`access_claims`' redundant cost.
    """
    profile, memberships = await service.describe_principal(
        actor_user_id=current_user.user_id
    )
    return PrincipalOut(
        id=profile["id"],
        email=profile["email"],
        full_name=profile["full_name"],
        status=profile["status"],
        active_org_id=current_user.requested_org_id,
        # At most one role in a real token — doc 07 §7.1's payload carries a single
        # ``role`` — so ``max`` is not needed; an org-less token yields ``None``.
        active_role=current_user.roles[0] if current_user.roles else None,
        memberships=memberships,
    )


@principal_router.post("/switch-org")
async def switch_org(
    payload: SwitchOrgIn,
    request: Request,
    claims: AccessTokenClaims = Depends(access_claims),
    settings: Settings = Depends(get_settings),
) -> SwitchOrgOut:
    """Doc 08 lines 934-938 ``POST /auth/switch-org`` — re-mint with a new ``activeOrgId``.

    Doc 08, verbatim: "User selects org → ``POST /auth/switch-org`` → API validates
    membership → new access token with new ``activeOrgId``". The middle clause is the
    whole endpoint. :func:`app.modules.tenancy.service.switch_active_org` performs it
    against a real ``memberships`` row *before* minting anything, and derives the new
    token's ``role`` from that same row — so this cannot be used to carry a role from
    one org into another.

    Authentication only, and again **not** ``get_org_context``: the primary caller is
    a multi-org user holding an org-less token, and requiring an org here would make
    the endpoint unreachable from the state it exists to resolve.

    No ``Set-Cookie``. The refresh cookie is unchanged and stays valid — its stored
    session is rewritten in place by ``tokens.retarget_family`` so a later refresh
    does not silently revert the switch. See that function for why rewriting beats
    revoke-and-reissue.
    """
    access_token, org_id, role, expires_in = await service.switch_active_org(
        actor_user_id=claims.user_id,
        current_org_id=claims.active_org_id,
        session_id=claims.session_id,
        target_org_id=payload.org_id,
        settings=settings,
        ip=client_ip(request),
    )
    return SwitchOrgOut(
        access_token=access_token,
        expires_in=expires_in,
        active_org=ActiveOrgOut(org_id=org_id, role=role),
    )


# ===========================================================================
# Org-scoped routes — require a verified activeOrgId
# ===========================================================================
org_router = APIRouter(
    prefix="/api/v1",
    tags=["Tenancy"],
    route_class=EnvelopeRoute,
    # Every route below also declares its own context dependency (either
    # ``get_org_context`` or ``require_roles``), so this router-level entry is
    # belt-and-braces: it guarantees that a route added later without a context
    # parameter still cannot be reached without an org.
    dependencies=[Depends(get_org_context)],
)


# --- Organizations ---------------------------------------------------------
@org_router.get("/orgs/{org_id}")
async def get_org(
    org_id: uuid.UUID = Path(...),
    ctx: OrgContext = Depends(get_org_context),
    session: AsyncSession = Depends(get_db),
) -> OrgOut:
    """ADDED (doc 07 §4.2 has ``GET /orgs`` but no single-org read).

    Open to any member: doc 07 §8.1 has no row restricting an org's own members from
    seeing the org they belong to, and a ``teacher`` needs the org's name to render a
    header.
    """
    return await service.get_organization(session=session, ctx=ctx, org_id=org_id)


@org_router.patch("/orgs/{org_id}")
async def patch_org(
    payload: OrgUpdateIn,
    request: Request,
    org_id: uuid.UUID = Path(...),
    # Declared before ``session`` on purpose. FastAPI solves a dependant's
    # dependencies in declaration order, so the role check runs — and a 403 is
    # raised — before ``get_db`` opens a transaction and checks out a connection for
    # a request that was going to be refused. Doc 07 §6's "get_db opens the
    # transaction last", made true by ordering rather than by comment.
    ctx: OrgContext = Depends(require_roles(*_ADMIN_ROLES)),
    session: AsyncSession = Depends(get_db),
) -> OrgOut:
    """Doc 07 §4.2 ``PATCH /orgs/:id``, emits ``org.settings_updated``."""
    return await service.update_organization(
        session=session,
        ctx=ctx,
        org_id=org_id,
        payload=payload,
        ip=client_ip(request),
    )


@org_router.delete("/orgs/{org_id}")
async def delete_org(
    payload: OrgDeleteIn,
    request: Request,
    org_id: uuid.UUID = Path(...),
    ctx: OrgContext = Depends(require_roles("super_admin")),
    session: AsyncSession = Depends(get_db),
) -> DeletedOut:
    """ADDED — soft delete. ``super_admin`` only.

    Doc 07 §4.2 documents no DELETE route for anything. The most restrictive
    available role is required because this is the most destructive operation in the
    module, and because ``super_admin`` is doc 07 §8.1's platform role — deleting a
    tenant is a platform action, not a tenant one.

    See :func:`app.modules.tenancy.service.delete_organization` for what does *not*
    cascade and the live consequence of that, which is reported rather than
    half-fixed.
    """
    await service.delete_organization(
        session=session,
        ctx=ctx,
        org_id=org_id,
        row_version=payload.row_version,
        ip=client_ip(request),
    )
    return DeletedOut(id=org_id)


# --- Branches --------------------------------------------------------------
@org_router.post("/orgs/{org_id}/branches", status_code=status.HTTP_201_CREATED)
async def create_branch(
    payload: BranchCreateIn,
    request: Request,
    org_id: uuid.UUID = Path(...),
    ctx: OrgContext = Depends(require_roles(*_ADMIN_ROLES)),
    session: AsyncSession = Depends(get_db),
) -> BranchOut:
    """Doc 07 §4.2 ``POST /orgs/:id/branches``."""
    return await service.create_branch(
        session=session,
        ctx=ctx,
        org_id=org_id,
        payload=payload,
        ip=client_ip(request),
    )


@org_router.get("/orgs/{org_id}/branches")
async def list_branches(
    org_id: uuid.UUID = Path(...),
    ctx: OrgContext = Depends(get_org_context),
    session: AsyncSession = Depends(get_db),
    page: PageParams = Depends(),
) -> list[BranchOut]:
    """Doc 07 §4.2 ``GET /orgs/:id/branches``. Open to any member.

    A ``teacher`` scoped to one branch still sees the whole list here. That is
    deliberate and worth being explicit about: doc 07 §8.2's ``branch_id`` narrows
    which *data* a member may act on, not whether they may know the organization has
    other branches — and a branch name is not tenant-sensitive within its own tenant.
    Narrowing this list to the caller's own branch is a product decision, not a
    security one, and doc 07 §8.1 does not ask for it. Reported.
    """
    items, _ = await service.list_branches(
        session=session,
        ctx=ctx,
        org_id=org_id,
        limit=page.probe_limit,
        offset=page.offset,
    )
    return _paginate(items, page)


@org_router.patch("/branches/{branch_id}")
async def patch_branch(
    payload: BranchUpdateIn,
    request: Request,
    branch_id: uuid.UUID = Path(...),
    ctx: OrgContext = Depends(require_roles(*_ADMIN_ROLES)),
    session: AsyncSession = Depends(get_db),
) -> BranchOut:
    """ADDED (doc 07 §4.2 has no branch update).

    No ``org_id`` in the path, so there is no path-vs-token check to make — and none
    is needed. The branch is looked up through ``BranchRepository`` scoped to
    ``ctx.org_id``, so a branch id from another tenant simply is not found (404,
    which doc 08's status table explicitly wants for "resource absent, **or scoped
    away by tenant isolation**"). RLS is the second layer behind that.
    """
    return await service.update_branch(
        session=session,
        ctx=ctx,
        branch_id=branch_id,
        payload=payload,
        ip=client_ip(request),
    )


@org_router.delete("/branches/{branch_id}")
async def delete_branch(
    payload: BranchDeleteIn,
    request: Request,
    branch_id: uuid.UUID = Path(...),
    ctx: OrgContext = Depends(require_roles(*_ADMIN_ROLES)),
    session: AsyncSession = Depends(get_db),
) -> DeletedOut:
    """ADDED — soft delete."""
    await service.delete_branch(
        session=session,
        ctx=ctx,
        branch_id=branch_id,
        row_version=payload.row_version,
        ip=client_ip(request),
    )
    return DeletedOut(id=branch_id)


# --- Invitations -----------------------------------------------------------
@org_router.post("/orgs/{org_id}/invitations", status_code=status.HTTP_201_CREATED)
async def create_invitation(
    payload: InvitationCreateIn,
    request: Request,
    org_id: uuid.UUID = Path(...),
    ctx: OrgContext = Depends(require_roles(*_ADMIN_ROLES)),
    session: AsyncSession = Depends(get_db),
) -> InvitationCreatedOut:
    """Doc 07 §4.2 ``POST /orgs/:id/invitations``, emits ``invitation.sent``.

    The response carries the plaintext token because P1 has no mail sender. See
    :class:`app.modules.tenancy.schemas.InvitationCreatedOut` for why that is stated
    as a gap rather than presented as a feature.
    """
    return await service.create_invitation(
        session=session,
        ctx=ctx,
        org_id=org_id,
        payload=payload,
        ip=client_ip(request),
    )


@org_router.get("/orgs/{org_id}/invitations")
async def list_invitations(
    org_id: uuid.UUID = Path(...),
    invitation_status: Optional[str] = Query(default=None, alias="status"),
    ctx: OrgContext = Depends(require_roles(*_ADMIN_ROLES)),
    session: AsyncSession = Depends(get_db),
    page: PageParams = Depends(),
) -> list[InvitationOut]:
    """ADDED — the brief's "create/list/revoke"; §4.2 lists only create.

    Admin-only, unlike the branch list. An invitation row carries an email address
    that has not yet joined the organization, which is personal data about a
    non-member; ``teacher`` has no reason to enumerate it.

    The parameter is named ``invitation_status`` in Python and ``status`` on the
    wire. ``status`` alone would shadow the ``fastapi.status`` module imported for
    the ``status_code=`` arguments above — a name collision that would surface as a
    confusing ``AttributeError`` inside an unrelated route.
    """
    items, _ = await service.list_invitations(
        session=session,
        ctx=ctx,
        org_id=org_id,
        status=invitation_status,
        limit=page.probe_limit,
        offset=page.offset,
    )
    return _paginate(items, page)


@org_router.post("/invitations/{invitation_id}/revoke")
async def revoke_invitation(
    request: Request,
    invitation_id: uuid.UUID = Path(...),
    ctx: OrgContext = Depends(require_roles(*_ADMIN_ROLES)),
    session: AsyncSession = Depends(get_db),
) -> InvitationOut:
    """ADDED — ``POST ... /revoke``, not ``DELETE /invitations/{id}``.

    Revision 0024 grants ``app_user`` ``SELECT, INSERT, UPDATE`` and never grants
    ``DELETE``, and ADR-004 forbids hard deletes in application code regardless. So
    revocation is a status transition and the route names it. See
    :func:`app.modules.tenancy.service.revoke_invitation`.

    Registered **after** ``POST /invitations/accept`` is registered on
    ``public_router``, but the two cannot collide in either order: ``/invitations/
    accept`` is two path segments and ``/invitations/{id}/revoke`` is three. Noted
    because a future ``POST /invitations/{id}`` *would* collide with ``accept``, and
    the resolution then is registration order — public first.
    """
    return await service.revoke_invitation(
        session=session,
        ctx=ctx,
        invitation_id=invitation_id,
        ip=client_ip(request),
    )


# --- Memberships -----------------------------------------------------------
@org_router.get("/orgs/{org_id}/memberships")
async def list_memberships(
    org_id: uuid.UUID = Path(...),
    ctx: OrgContext = Depends(require_roles(*_ADMIN_ROLES)),
    session: AsyncSession = Depends(get_db),
    page: PageParams = Depends(),
) -> list[MembershipOut]:
    """ADDED — doc 07 §4.2 has ``PATCH /memberships/:id`` and no way to find one.

    Admin-only per §8.1's "Manage users" row. This is the route that supplies both
    the membership id and the ``rowVersion`` that ``PATCH`` requires.
    """
    items, _ = await service.list_memberships(
        session=session,
        ctx=ctx,
        org_id=org_id,
        limit=page.probe_limit,
        offset=page.offset,
    )
    return _paginate(items, page)


@org_router.patch("/memberships/{membership_id}")
async def patch_membership(
    payload: MembershipUpdateIn,
    request: Request,
    membership_id: uuid.UUID = Path(...),
    ctx: OrgContext = Depends(require_roles(*_ADMIN_ROLES)),
    session: AsyncSession = Depends(get_db),
) -> MembershipOut:
    """Doc 07 §4.2 ``PATCH /memberships/:id``, emits ``membership.role_changed``.

    ``require_roles`` is necessary but not sufficient here — three further checks
    live in the service (no self-modification, only a ``super_admin`` may touch
    ``super_admin``, and ``branch_id`` must name a live branch in this org). See
    :func:`app.modules.tenancy.service.update_membership`; none of the three is in
    any document and all three are in the report.
    """
    return await service.update_membership(
        session=session,
        ctx=ctx,
        membership_id=membership_id,
        payload=payload,
        ip=client_ip(request),
    )


# ===========================================================================
# Public route — one, declared as such
# ===========================================================================
public_router = APIRouter(
    prefix="/api/v1",
    tags=["Tenancy"],
    route_class=EnvelopeRoute,
    # No `dependencies=[...]`, on purpose and only for this router. Doc 07 §7.5:
    # "Nothing is public unless it is explicitly declared so." This separate,
    # one-route router with this comment is that declaration. Adding a second route
    # here should require justifying it against the paragraph below.
)


@public_router.post("/invitations/accept", status_code=status.HTTP_200_OK)
async def accept_invitation(
    payload: InvitationAcceptIn,
    request: Request,
    settings: Settings = Depends(get_settings),
) -> InvitationAcceptedOut:
    """ADDED, and **unauthenticated by necessity**.

    The invitee has no account, so there is no credential to require — the
    invitation token *is* the credential, and it is verified by hashing it and
    matching the stored digest. See
    :func:`app.modules.tenancy.service.accept_invitation` for how the token's org
    prefix is used only to satisfy RLS and is not an authorization input.

    200 rather than 201. Acceptance may create a user row, or may join an existing
    user to the org; the caller cannot be told which without learning whether the
    invited address is registered, and a status code that varies by that is exactly
    such a disclosure.

    Returns no token pair. Reported as a judgment call: acceptance proves control of
    a link, not of a password, and issuing a session here would make a forwarded
    invitation email a bearer credential for the account it just created.
    """
    return await service.accept_invitation(
        payload=payload,
        settings=settings,
        ip=client_ip(request),
    )


__all__ = [
    "PageParams",
    "access_claims",
    "client_ip",
    "identity_router",
    "org_router",
    "principal_router",
    "public_router",
]
