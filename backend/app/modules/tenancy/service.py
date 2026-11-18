"""Tenancy service (P1-T8) — doc 07 §11: routers do HTTP, services do work.

Every security-relevant decision in this module is here rather than in the router,
so there is exactly one place to audit. The router's only jobs are the guard
declarations, the path-vs-token org check's *call*, and turning DTOs into
arguments.

Session ownership, and why it is split
--------------------------------------
Two kinds of function live here, and the difference is which transaction they run
in:

* **Org-scoped operations** take an ``AsyncSession`` parameter. The router obtains
  it from ``Depends(get_db)``, which is P1-T6's adapter over
  ``get_tenant_session(ctx.org_id)``. Doc 07 §6: "``get_db`` opens the transaction
  last, after the org context is known" — so a request that fails the role guard
  never opens one.
* **Identity-scoped and public operations** open their own session, because there
  is no org context for ``get_db`` to use. ``POST /orgs`` creates the org that
  would have provided it; ``GET /orgs`` is the question "which orgs?"; and
  ``POST /invitations/accept`` is unauthenticated by necessity. These use
  ``get_identity_session`` or ``get_tenant_session`` directly and say so at each
  definition.

Return types
------------
Services return **pydantic DTOs, not ORM instances**. The session factory is
``expire_on_commit=False`` so ORM objects would survive the commit, but a returned
instance still carries a live relationship loader; touching
``membership.organization`` in a router after the transaction closed would emit a
lazy load against a closed session and become a 500 that looks like a template
bug. Building the DTO inside the transaction makes that impossible by
construction. It is also the pattern ``app/modules/auth/service.py`` established
with ``IssuedCredentials``.

Doc gaps, all reported
----------------------
Doc 07 §4.2 names eight routes and specifies for them: no request bodies, no
response bodies, no role requirements on seven of the eight, and no DELETE route at
all. Doc 03 defines no ``invitations`` table. Doc 08 specifies only
``POST /auth/switch-org``'s wire shape. The judgment calls those gaps forced are
listed in the P1-T8 report, most consequential first; each one is also argued at
its call site below.
"""

from __future__ import annotations

import datetime as _dt
import hashlib
import logging
import secrets
import uuid
from typing import Any, Optional

from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from app.common.audit_log import ACTIONS, record_audit
from app.common.deps import OrgContext
from app.common.errors import (
    AuthenticationError,
    AuthorizationError,
    BusinessRuleError,
    ConflictError,
    NotFoundError,
    ValidationError,
)
from app.core.config import Settings
from app.db.exceptions import EntityNotFoundError, OptimisticLockError
from app.db.session import get_identity_session, get_tenant_session
from app.models.invitation import INVITATION_OPEN_STATUS
from app.modules.auth import security, tokens
from app.modules.auth.repository import UserRepository
from app.modules.tenancy import schemas
from app.modules.tenancy.repository import (
    BranchRepository,
    IdentityRepository,
    InvitationRepository,
    MembershipRepository,
    OrganizationRepository,
)

logger = logging.getLogger("papergen.tenancy.service")

# ---------------------------------------------------------------------------
# Authored constants — none of these appears in doc 03, 07 or 08
# ---------------------------------------------------------------------------

#: The role the creator of an organization receives.
#:
#: AUTHORED. Doc 07 §8.1's permission matrix has three columns
#: ("Manage users | No | Yes (own org) | Yes (any org)") and doc 07 §4.2 annotates
#: ``POST /orgs`` with no role at all. ``coaching_admin`` rather than
#: ``super_admin`` because §8.1 defines ``super_admin`` as *cross-tenant* ("any
#: org", "View audit logs ... cross-tenant"): handing it out on self-serve org
#: creation would make every signup a platform administrator. ``coaching_admin``
#: gives the creator everything §8.1 grants "own org" and nothing beyond it.
ORG_CREATOR_ROLE = "coaching_admin"

#: How long an invitation stays acceptable.
#:
#: AUTHORED. Doc 01 FR-1.5 says only "Pending invites expire" and names no
#: interval; doc 03's ``memberships`` drift note repeats the requirement without a
#: number. Seven days matches ``JWT_REFRESH_EXPIRY_DAYS``'s default so there is one
#: "about a week" in the system rather than two, and it is short enough that a link
#: forwarded to the wrong address stops working before it is forgotten.
#:
#: Deliberately **not** client-supplied. An ``expiresAt`` field in
#: ``InvitationCreateIn`` would let a ``coaching_admin`` mint a link valid for a
#: century. If this needs to be tunable it belongs in ``Settings`` alongside the
#: JWT lifetimes, which is a config change rather than a request field.
INVITATION_TTL_DAYS = 7

#: The single client-visible message for every invitation-acceptance failure:
#: malformed token, unparseable org prefix, unknown hash, already accepted,
#: revoked, or expired. Same discipline as ``_GENERIC_LOGIN_FAILURE`` in
#: ``app/modules/auth/service.py`` and for the same reason — this endpoint is
#: **public**, so a caller who can distinguish "no such invitation" from "expired"
#: has an oracle for guessing tokens. The real reason goes to ``log_detail``, which
#: ``errors.py`` never serialises.
_GENERIC_INVITATION_FAILURE = "This invitation link is invalid or has expired."

#: Roles permitted to grant or revoke ``super_admin``. See
#: :func:`update_membership`.
_SUPER_ADMIN_GRANTORS = frozenset({"super_admin"})


# ---------------------------------------------------------------------------
# Internal helpers
# ---------------------------------------------------------------------------
def assert_path_org(ctx: OrgContext, path_org_id: uuid.UUID) -> None:
    """Refuse a request whose path names an org other than the token's.

    Doc 07 §4.2's routes are shaped ``/orgs/{id}/branches``, so the org appears
    **twice** in every such request: once in the path and once in the verified
    ``activeOrgId`` claim. This function is what makes the path decorative rather
    than authoritative.

    Without it the failure mode is subtle rather than dramatic: ``get_db`` sets
    ``app.org_id`` from the *token*, and the repositories filter on the *token's*
    org too, so a request to ``/orgs/{someone-elses-org}/branches`` would not leak
    another tenant's rows — it would silently return and mutate the caller's own
    org while appearing to address another. That is arguably worse than a leak,
    because a client could delete its own branch believing it deleted a
    competitor's. 403, loudly, is the correct answer.

    Reported as a judgment call: doc 07 §4.2 does not say what should happen when
    the path org and the token org disagree.
    """
    if path_org_id != ctx.org_id:
        logger.warning(
            "path_org_mismatch user_id=%s token_org=%s path_org=%s",
            ctx.user_id,
            ctx.org_id,
            path_org_id,
        )
        raise AuthorizationError(
            "You do not have access to this resource.",
            log_detail=(
                f"path org {path_org_id} does not match token activeOrgId "
                f"{ctx.org_id} for user {ctx.user_id}"
            ),
        )


def _org_out(org: Any) -> schemas.OrgOut:
    return schemas.OrgOut(
        id=org.id,
        name=org.name,
        org_type=org.org_type,
        region=org.region,
        status=org.status,
        # ``settings`` is JSONB NOT NULL DEFAULT '{}', but a row inserted by a
        # migration or a seed script could still surface None through a stale
        # identity-map entry; ``or {}`` keeps the response schema honest without
        # asserting a column invariant the API cannot enforce.
        settings=org.settings or {},
        subscription_id=org.subscription_id,
        row_version=org.row_version,
        created_at=org.created_at,
        updated_at=org.updated_at,
    )


def _branch_out(branch: Any) -> schemas.BranchOut:
    return schemas.BranchOut(
        id=branch.id,
        org_id=branch.org_id,
        name=branch.name,
        region=branch.region,
        status=branch.status,
        row_version=branch.row_version,
        created_at=branch.created_at,
        updated_at=branch.updated_at,
    )


def _invitation_out(inv: Any) -> schemas.InvitationOut:
    return schemas.InvitationOut(
        id=inv.id,
        org_id=inv.org_id,
        email=inv.email,
        role=inv.role,
        branch_id=inv.branch_id,
        invited_by=inv.invited_by,
        status=inv.status,
        expires_at=inv.expires_at,
        accepted_at=inv.accepted_at,
        row_version=inv.row_version,
        created_at=inv.created_at,
    )


def _membership_out(m: Any) -> schemas.MembershipOut:
    return schemas.MembershipOut(
        id=m.id,
        org_id=m.org_id,
        user_id=m.user_id,
        role=m.role,
        status=m.status,
        branch_id=m.branch_id,
        row_version=m.row_version,
        created_at=m.created_at,
        updated_at=m.updated_at,
    )


def _mint_invitation_token(org_id: uuid.UUID) -> tuple[str, str]:
    """Return ``(plaintext_token, sha256_hex)``.

    Format: ``<org_uuid>.<secret>``, where ``secret`` is 32 CSPRNG bytes in
    URL-safe base64 (43 characters, 256 bits of entropy).

    The org prefix exists for exactly one reason and it is worth being precise
    about, because "put the tenant id in the credential" usually *is* a bug.
    ``POST /invitations/accept`` is public, so it has no ``activeOrgId`` claim and
    therefore no way to set ``app.org_id`` — and without ``app.org_id`` the
    ``invitations`` table's RLS policy (revision 0026) matches no rows, so the
    lookup is impossible as ``app_user``. The prefix supplies that value.

    It is **not** an authorization input. It selects which tenant's rows are
    visible; the 256-bit secret is what proves the caller holds a real invitation.
    Rewriting the prefix scopes the lookup to an org where ``sha256(token)`` does
    not exist, which returns the same generic failure as a fabricated token. The
    alternatives were worse: a superuser connection for this one read defeats
    P1-T5's entire premise, and an RLS policy keyed on a query parameter is not
    something RLS can express safely.

    ``token_urlsafe`` emits only ``[A-Za-z0-9_-]``, so ``.`` is an unambiguous
    separator and ``partition('.')`` cannot split inside the secret.
    """
    secret = secrets.token_urlsafe(32)
    token = f"{org_id}.{secret}"
    return token, hashlib.sha256(token.encode("utf-8")).hexdigest()


def _parse_invitation_token(token: str) -> tuple[uuid.UUID, str]:
    """Split a token into ``(org_id, sha256_hex)`` or raise the generic failure.

    Every malformed shape produces the same ``BusinessRuleError`` with the same
    message as a valid-looking token that matches nothing, so the endpoint cannot
    be used to probe which org ids exist.
    """
    org_part, sep, secret = token.partition(".")
    if not sep or not secret:
        raise BusinessRuleError(
            _GENERIC_INVITATION_FAILURE,
            log_detail="invitation token has no '<org>.<secret>' separator",
        )
    try:
        org_id = uuid.UUID(org_part)
    except ValueError as exc:
        raise BusinessRuleError(
            _GENERIC_INVITATION_FAILURE,
            log_detail=f"invitation token org prefix is not a uuid: {org_part!r}",
        ) from exc
    return org_id, hashlib.sha256(token.encode("utf-8")).hexdigest()


def _utcnow() -> _dt.datetime:
    return _dt.datetime.now(_dt.timezone.utc)


# ---------------------------------------------------------------------------
# Organizations
# ---------------------------------------------------------------------------
async def create_organization(
    *,
    actor_user_id: uuid.UUID,
    payload: schemas.OrgCreateIn,
    ip: Optional[str] = None,
) -> schemas.OrgOut:
    """``POST /api/v1/orgs`` — create an org and make the caller its admin.

    Opens its own session, because there is no org context yet: this call is what
    creates one. ``get_db`` cannot be used and neither can ``get_org_context``, so
    the route is guarded by ``get_current_user`` alone.

    Why the UUID is generated in Python
    -----------------------------------
    Three writes have to be one transaction — the ``organizations`` row, the
    creator's ``memberships`` row, and the ``audit_logs`` row — and two of the
    three are RLS-protected on ``app.org_id``. ``audit_logs``' ``WITH CHECK`` is
    ``org_id = current_setting('app.org_id')::uuid`` with **no** ``OR org_id IS
    NULL`` (revision 0025), so the GUC has to be set to the new org's id *before*
    the transaction opens, which means the id has to exist before the INSERT. So it
    is minted here and passed to ``get_identity_session(..., org_id=...)``.
    Letting ``gen_random_uuid()`` assign it would have required either a second
    transaction for the audit row (losing atomicity) or a session-level ``SET``
    mid-transaction (which is what ``set_config(..., true)`` already is, but would
    have to be issued after the first INSERT and would read as an accident).

    REPORTED GAP — anyone authenticated can call this, and there is no cap. Doc 07
    §4.2 annotates ``POST /orgs`` with no role, and there is no role that could be
    required: the caller has no membership anywhere yet in the bootstrap case. The
    P1-T8 report recommends a per-user creation limit, and deliberately does not add
    one here, because the brief reserves rate limiting for a separate task.
    """
    new_org_id = uuid.uuid4()

    async with get_identity_session(actor_user_id, org_id=new_org_id) as session:
        org_repo = OrganizationRepository(session, new_org_id)

        create_kwargs: dict[str, Any] = {
            "name": payload.name,
            "org_type": payload.org_type,
        }
        # Omitted rather than passed as None so the column's server default
        # ('ap-south-1' for region, '{}' for settings) applies. Passing None would
        # violate NOT NULL on settings and blank out region's default.
        if payload.region is not None:
            create_kwargs["region"] = payload.region
        if payload.settings is not None:
            create_kwargs["settings"] = payload.settings

        org = await org_repo.create(**create_kwargs)

        membership = await MembershipRepository(session, new_org_id).create(
            user_id=actor_user_id,
            role=ORG_CREATOR_ROLE,
            status="active",
        )

        record_audit(
            session,
            org_id=new_org_id,
            actor_id=actor_user_id,
            action=ACTIONS.ORG_CREATED,
            entity="organizations",
            entity_id=new_org_id,
            meta={"name": payload.name, "orgType": payload.org_type},
            ip=ip,
        )
        record_audit(
            session,
            org_id=new_org_id,
            actor_id=actor_user_id,
            action=ACTIONS.MEMBERSHIP_CREATED,
            entity="memberships",
            entity_id=membership.id,
            meta={
                "userId": str(actor_user_id),
                "role": ORG_CREATOR_ROLE,
                "reason": "organization_creator",
            },
            ip=ip,
        )

        # Server defaults (status, region, settings, created_at, row_version) are
        # assigned by PostgreSQL, and ``flush()`` does not fetch them. Refresh
        # inside the transaction so the response reports what was actually stored
        # rather than Python-side Nones.
        await session.refresh(org)
        result = _org_out(org)

    logger.info(
        "org_created org_id=%s actor_id=%s role=%s",
        new_org_id,
        actor_user_id,
        ORG_CREATOR_ROLE,
    )
    return result


async def get_organization(
    *, session: AsyncSession, ctx: OrgContext, org_id: uuid.UUID
) -> schemas.OrgOut:
    """``GET /api/v1/orgs/{org_id}`` — the caller's own org.

    Not in doc 07 §4.2's route list, which has ``GET /orgs`` (plural) but no
    single-org read. Added because ``PATCH /orgs/{id}`` requires a ``rowVersion``
    the client has to obtain from somewhere, and because it is the shortest
    possible demonstration that cross-org reads are refused. Reported as an added
    route.
    """
    assert_path_org(ctx, org_id)
    org = await OrganizationRepository(session, ctx.org_id).get_by_id(org_id)
    if org is None:
        raise NotFoundError(
            "Organization not found.",
            log_detail=f"org {org_id} not visible to user {ctx.user_id}",
        )
    return _org_out(org)


async def update_organization(
    *,
    session: AsyncSession,
    ctx: OrgContext,
    org_id: uuid.UUID,
    payload: schemas.OrgUpdateIn,
    ip: Optional[str] = None,
) -> schemas.OrgOut:
    """``PATCH /api/v1/orgs/{org_id}`` — doc 07 §4.2, emitting ``org.settings_updated``.

    ``settings`` replaces wholesale rather than deep-merging. Doc 03 §5 gives the
    column no schema, and a merge over an unschematised object has no way to
    express *removal* — ``{"a": null}`` is ambiguous between "set a to null" and
    "delete a". A client that wants to change one key reads the org first (that is
    what ``GET /orgs/{id}`` is for) and sends the whole object back, which is also
    what the ``rowVersion`` it needs anyway implies.
    """
    assert_path_org(ctx, org_id)

    changes: dict[str, Any] = {}
    if payload.name is not None:
        changes["name"] = payload.name
    if payload.region is not None:
        changes["region"] = payload.region
    if payload.status is not None:
        changes["status"] = payload.status
    if payload.settings is not None:
        changes["settings"] = payload.settings

    if not changes:
        # 422 rather than a no-op 200. A no-op would still bump ``row_version``
        # through ``BaseRepository.update``, invalidating every other client's
        # cached version for a request that changed nothing.
        raise ValidationError(
            "Provide at least one field to update.",
            log_detail=f"empty PATCH body for org {org_id}",
        )

    repo = OrganizationRepository(session, ctx.org_id)
    before = await repo.get_by_id(org_id)
    if before is None:
        raise NotFoundError(
            "Organization not found.",
            log_detail=f"org {org_id} not visible to user {ctx.user_id}",
        )
    before_snapshot = {
        "name": before.name,
        "region": before.region,
        "status": before.status,
    }

    org = await _update_or_translate(
        repo, org_id, payload.row_version, changes, entity="organization"
    )

    record_audit(
        session,
        org_id=ctx.org_id,
        actor_id=ctx.user_id,
        action=ACTIONS.ORG_SETTINGS_UPDATED,
        entity="organizations",
        entity_id=org_id,
        meta={
            "changed": sorted(changes.keys()),
            # Old and new values for the scalar fields only. ``settings`` is
            # excluded on purpose: it is caller-controlled free-form JSONB and
            # copying it into an append-only table that cannot be corrected is how
            # a secret someone put in ``settings`` becomes permanent.
            "before": before_snapshot,
            "after": {
                "name": org.name,
                "region": org.region,
                "status": org.status,
            },
        },
        ip=ip,
    )
    return _org_out(org)


async def delete_organization(
    *,
    session: AsyncSession,
    ctx: OrgContext,
    org_id: uuid.UUID,
    row_version: int,
    ip: Optional[str] = None,
) -> None:
    """``DELETE /api/v1/orgs/{org_id}`` — soft delete. ``super_admin`` only.

    REPORTED GAP — doc 07 §4.2 documents **no DELETE route for anything**, and doc
    03 §5 describes the cascade behaviour of a hard delete that ADR-004 forbids
    application code from performing. The brief asks for soft-delete, so it is
    implemented, and the role is the most restrictive one available because this is
    the single most destructive operation in the module.

    Note what soft-deleting an org does **not** do: nothing cascades.
    ``deleted_at`` on the ``organizations`` row does not propagate to branches,
    memberships, papers or anything else, because ADR-004's deletion is an UPDATE
    and an UPDATE cascades nothing. The concrete consequence, stated because it is
    a live hole rather than a theoretical one: a member of a soft-deleted org still
    holds a valid token naming it, and ``get_org_context`` does not consult
    ``organizations.deleted_at``. Their next request still succeeds.
    ``IdentityRepository`` filters ``Organization.deleted_at IS NULL`` so the org
    stops appearing in ``GET /orgs`` and cannot be switched to, which closes the
    *new*-session path but not existing ones. Closing it properly means either a
    tenant-status check in ``get_org_context`` — a function this task is forbidden
    to modify — or revoking every session family for the org's members, which
    needs a members-to-sessions index that does not exist. Recommended as
    follow-up in the report; not silently half-done here.
    """
    repo = OrganizationRepository(session, ctx.org_id)
    org = await repo.get_by_id(org_id)
    if org is None:
        raise NotFoundError(
            "Organization not found.",
            log_detail=f"org {org_id} not visible to user {ctx.user_id}",
        )
    assert_path_org(ctx, org_id)

    if org.row_version != row_version:
        raise ConflictError(
            "This record was modified by someone else. Reload and try again.",
            log_detail=(
                f"org {org_id} row_version is {org.row_version}, "
                f"caller sent {row_version}"
            ),
        )

    # The audit row is written BEFORE the soft delete so that both are in the same
    # transaction and the FK from ``audit_logs.org_id`` still resolves. (It would
    # resolve either way — a soft delete leaves the row present — but the ordering
    # also means a failure in the delete rolls back the claim that it happened.)
    record_audit(
        session,
        org_id=ctx.org_id,
        actor_id=ctx.user_id,
        action=ACTIONS.ORG_DELETED,
        entity="organizations",
        entity_id=org_id,
        meta={"name": org.name, "rowVersion": row_version},
        ip=ip,
    )
    try:
        await repo.soft_delete(org_id)
    except EntityNotFoundError as exc:
        raise NotFoundError(
            "Organization not found.", log_detail=str(exc)
        ) from exc

    logger.warning("org_soft_deleted org_id=%s actor_id=%s", org_id, ctx.user_id)


# ---------------------------------------------------------------------------
# Branches
# ---------------------------------------------------------------------------
async def create_branch(
    *,
    session: AsyncSession,
    ctx: OrgContext,
    org_id: uuid.UUID,
    payload: schemas.BranchCreateIn,
    ip: Optional[str] = None,
) -> schemas.BranchOut:
    """``POST /api/v1/orgs/{org_id}/branches`` — doc 07 §4.2."""
    assert_path_org(ctx, org_id)

    repo = BranchRepository(session, ctx.org_id)
    if await repo.name_exists(payload.name):
        raise ConflictError(
            "A branch with that name already exists.",
            log_detail=f"branch name {payload.name!r} taken in org {ctx.org_id}",
        )

    create_kwargs: dict[str, Any] = {"name": payload.name}
    if payload.region is not None:
        create_kwargs["region"] = payload.region

    try:
        branch = await repo.create(**create_kwargs)
    except IntegrityError as exc:
        # ``name_exists`` above is racy by nature; ``uq_branches_org_id_name`` is
        # the authority and this is the same conflict, reported the same way.
        raise ConflictError(
            "A branch with that name already exists.",
            log_detail=f"uq_branches_org_id_name violated: {exc.orig}",
        ) from exc

    record_audit(
        session,
        org_id=ctx.org_id,
        actor_id=ctx.user_id,
        action=ACTIONS.BRANCH_CREATED,
        entity="branches",
        entity_id=branch.id,
        meta={"name": payload.name, "region": payload.region},
        ip=ip,
    )
    await session.refresh(branch)
    return _branch_out(branch)


async def list_branches(
    *,
    session: AsyncSession,
    ctx: OrgContext,
    org_id: uuid.UUID,
    limit: int,
    offset: int,
) -> tuple[list[schemas.BranchOut], int]:
    """``GET /api/v1/orgs/{org_id}/branches`` — doc 07 §4.2.

    Returns ``(items, count)``. ``count`` is the length of the page, not a total:
    doc 07 §9's envelope has ``pagination`` but the repository base class has no
    ``count`` method, and adding one is a P1-T5 change rather than a P1-T8 one.
    Reported as a known incompleteness of the pagination meta.
    """
    assert_path_org(ctx, org_id)
    branches = await BranchRepository(session, ctx.org_id).list_for_org(
        limit=limit, offset=offset
    )
    items = [_branch_out(b) for b in branches]
    return items, len(items)


async def update_branch(
    *,
    session: AsyncSession,
    ctx: OrgContext,
    branch_id: uuid.UUID,
    payload: schemas.BranchUpdateIn,
    ip: Optional[str] = None,
) -> schemas.BranchOut:
    """``PATCH /api/v1/branches/{branch_id}`` — an added route (§4.2 has no branch update)."""
    changes: dict[str, Any] = {}
    if payload.name is not None:
        changes["name"] = payload.name
    if payload.region is not None:
        changes["region"] = payload.region
    if payload.status is not None:
        changes["status"] = payload.status
    if not changes:
        raise ValidationError(
            "Provide at least one field to update.",
            log_detail=f"empty PATCH body for branch {branch_id}",
        )

    repo = BranchRepository(session, ctx.org_id)
    before = await repo.get_by_id(branch_id)
    if before is None:
        raise NotFoundError(
            "Branch not found.",
            log_detail=f"branch {branch_id} not visible in org {ctx.org_id}",
        )

    if payload.name is not None and await repo.name_exists(
        payload.name, exclude_id=branch_id
    ):
        raise ConflictError(
            "A branch with that name already exists.",
            log_detail=f"branch name {payload.name!r} taken in org {ctx.org_id}",
        )

    branch = await _update_or_translate(
        repo, branch_id, payload.row_version, changes, entity="branch"
    )

    record_audit(
        session,
        org_id=ctx.org_id,
        actor_id=ctx.user_id,
        action=ACTIONS.BRANCH_UPDATED,
        entity="branches",
        entity_id=branch_id,
        meta={
            "changed": sorted(changes.keys()),
            "before": {
                "name": before.name,
                "region": before.region,
                "status": before.status,
            },
            "after": {
                "name": branch.name,
                "region": branch.region,
                "status": branch.status,
            },
        },
        ip=ip,
    )
    return _branch_out(branch)


async def delete_branch(
    *,
    session: AsyncSession,
    ctx: OrgContext,
    branch_id: uuid.UUID,
    row_version: int,
    ip: Optional[str] = None,
) -> None:
    """``DELETE /api/v1/branches/{branch_id}`` — soft delete.

    ``memberships.branch_id`` is ``ON DELETE SET NULL``, but a soft delete is an
    UPDATE and fires no referential action, so members of a soft-deleted branch
    keep pointing at it. That is deliberate rather than overlooked: nulling their
    ``branch_id`` would silently *widen* every affected member's access from one
    branch to the whole org (doc 07 §8.2: "null means org-wide access"), which is a
    privilege escalation triggered by a delete. Leaving the pointer means
    ``get_scope`` continues to narrow them to a branch that no longer accepts new
    data, which fails closed. Reported as a judgment call.
    """
    repo = BranchRepository(session, ctx.org_id)
    branch = await repo.get_by_id(branch_id)
    if branch is None:
        raise NotFoundError(
            "Branch not found.",
            log_detail=f"branch {branch_id} not visible in org {ctx.org_id}",
        )
    if branch.row_version != row_version:
        raise ConflictError(
            "This record was modified by someone else. Reload and try again.",
            log_detail=(
                f"branch {branch_id} row_version is {branch.row_version}, "
                f"caller sent {row_version}"
            ),
        )

    record_audit(
        session,
        org_id=ctx.org_id,
        actor_id=ctx.user_id,
        action=ACTIONS.BRANCH_DELETED,
        entity="branches",
        entity_id=branch_id,
        meta={"name": branch.name, "rowVersion": row_version},
        ip=ip,
    )
    try:
        await repo.soft_delete(branch_id)
    except EntityNotFoundError as exc:
        raise NotFoundError("Branch not found.", log_detail=str(exc)) from exc


# ---------------------------------------------------------------------------
# Invitations
# ---------------------------------------------------------------------------
async def create_invitation(
    *,
    session: AsyncSession,
    ctx: OrgContext,
    org_id: uuid.UUID,
    payload: schemas.InvitationCreateIn,
    ip: Optional[str] = None,
) -> schemas.InvitationCreatedOut:
    """``POST /api/v1/orgs/{org_id}/invitations`` — doc 07 §4.2, emits ``invitation.sent``.

    Two rows may be written, and the second one is how ADR-012's model is honoured
    rather than discarded. If the invited address **already has a user row**, a
    ``memberships`` row is created with ``status = 'invited'`` — exactly what
    ADR-012 §3 and doc 01 FR-1.5 describe ("the membership is created with
    ``status = invited`` and grants no access until accepted"). If it does not,
    only the invitation exists, because ``memberships.user_id`` is NOT NULL and
    there is no user to point at. See ``app/models/invitation.py`` for the full
    argument.

    ``status = 'invited'`` grants no access, and that is enforced rather than
    asserted: ``MembershipRepository.get_active_for_user`` filters
    ``status = 'active'``, ``auth.service._resolve_membership`` uses it, and
    revision 0027's ``memberships_self_select`` policy also requires
    ``status = 'active'``, so an invited row is invisible to ``GET /orgs`` and
    cannot be switched into.

    Escalation guard: an inviter cannot invite at a role above their own. Doc 07
    §8.1 says nothing about invitation roles, but without this a
    ``coaching_admin`` could invite an accomplice as ``super_admin`` and hold
    cross-tenant access one acceptance later.
    """
    assert_path_org(ctx, org_id)

    if payload.role == "super_admin" and ctx.role not in _SUPER_ADMIN_GRANTORS:
        logger.warning(
            "invitation_role_escalation_refused actor=%s org=%s actor_role=%s",
            ctx.user_id,
            ctx.org_id,
            ctx.role,
        )
        raise AuthorizationError(
            "You do not have access to this resource.",
            log_detail=(
                f"role {ctx.role!r} cannot invite at role 'super_admin' "
                f"in org {ctx.org_id}"
            ),
        )

    inv_repo = InvitationRepository(session, ctx.org_id)
    if await inv_repo.get_open_for_email(payload.email) is not None:
        raise ConflictError(
            "An invitation for that email is already pending.",
            log_detail=(
                f"pending invitation already exists for {payload.email!r} "
                f"in org {ctx.org_id}"
            ),
        )

    if payload.branch_id is not None:
        branch = await BranchRepository(session, ctx.org_id).get_by_id(
            payload.branch_id
        )
        if branch is None:
            # 422, not 404: the branch id is a *field* of this request, and the
            # resource being addressed (the org's invitation collection) exists.
            raise ValidationError(
                "That branch does not exist in this organization.",
                log_detail=(
                    f"branch {payload.branch_id} not visible in org {ctx.org_id}"
                ),
            )

    token, token_hash = _mint_invitation_token(ctx.org_id)
    expires_at = _utcnow() + _dt.timedelta(days=INVITATION_TTL_DAYS)

    try:
        invitation = await inv_repo.create(
            email=payload.email,
            role=payload.role,
            branch_id=payload.branch_id,
            invited_by=ctx.user_id,
            token_hash=token_hash,
            expires_at=expires_at,
            status=INVITATION_OPEN_STATUS,
        )
    except IntegrityError as exc:
        raise ConflictError(
            "An invitation for that email is already pending.",
            log_detail=f"uq_invitations_org_id_email violated: {exc.orig}",
        ) from exc

    # ADR-012's half of the model, when it is representable.
    existing_user = await UserRepository(session).get_by_email(payload.email)
    membership_id: Optional[uuid.UUID] = None
    if existing_user is not None:
        mem_repo = MembershipRepository(session, ctx.org_id)
        existing_membership = await mem_repo.get_for_user(existing_user.id)
        if existing_membership is None:
            invited = await mem_repo.create(
                user_id=existing_user.id,
                role=payload.role,
                branch_id=payload.branch_id,
                status="invited",
            )
            membership_id = invited.id
        else:
            # Already a member (active, invited or suspended). The invitation row
            # still stands as the record of the ask, but no second membership is
            # written — ``uq_memberships_user_id_org_id`` would reject it, and
            # overwriting an existing *active* membership's role from an invite
            # would be a role change disguised as an invitation.
            membership_id = existing_membership.id
            logger.info(
                "invitation_for_existing_member org=%s user=%s status=%s",
                ctx.org_id,
                existing_user.id,
                existing_membership.status,
            )

    record_audit(
        session,
        org_id=ctx.org_id,
        actor_id=ctx.user_id,
        action=ACTIONS.INVITATION_SENT,
        entity="invitations",
        entity_id=invitation.id,
        meta={
            "email": payload.email,
            "role": payload.role,
            "branchId": str(payload.branch_id) if payload.branch_id else None,
            "expiresAt": expires_at.isoformat(),
            "existingUser": existing_user is not None,
            "membershipId": str(membership_id) if membership_id else None,
            # The token and its hash are deliberately absent. ``audit_logs`` is
            # append-only with no UPDATE grant, so anything written here is
            # permanent — a credential digest included "for traceability" could
            # never be removed.
        },
        ip=ip,
    )

    await session.refresh(invitation)
    return schemas.InvitationCreatedOut(
        invitation=_invitation_out(invitation), token=token
    )


async def list_invitations(
    *,
    session: AsyncSession,
    ctx: OrgContext,
    org_id: uuid.UUID,
    status: Optional[str],
    limit: int,
    offset: int,
) -> tuple[list[schemas.InvitationOut], int]:
    """``GET /api/v1/orgs/{org_id}/invitations`` — an added route.

    Doc 07 §4.2 lists ``POST /orgs/:id/invitations`` but no list route; the brief
    requires "create/list/revoke", so this is added. Reported.
    """
    assert_path_org(ctx, org_id)
    rows = await InvitationRepository(session, ctx.org_id).list_for_org(
        status=status, limit=limit, offset=offset
    )
    items = [_invitation_out(r) for r in rows]
    return items, len(items)


async def revoke_invitation(
    *,
    session: AsyncSession,
    ctx: OrgContext,
    invitation_id: uuid.UUID,
    ip: Optional[str] = None,
) -> schemas.InvitationOut:
    """``POST /api/v1/invitations/{invitation_id}/revoke``.

    A ``POST``-with-a-verb rather than ``DELETE /invitations/{id}``, for a concrete
    reason: revision 0024 grants ``app_user`` ``SELECT, INSERT, UPDATE`` and
    **never grants DELETE on anything**, and ADR-004 forbids hard deletes in
    application code regardless. Revocation is a status transition, so the route
    names the transition. A ``DELETE`` route would either have to soft-delete (and
    then "revoked" and "deleted" would be two different states meaning the same
    thing) or fail with a privilege error.

    No ``rowVersion`` is required here, unlike the update routes. Revocation is
    idempotent in effect — the only transition it performs is
    ``pending -> revoked`` — so a lost-update race between two admins revoking the
    same invitation has no incorrect outcome to protect against. A non-pending
    invitation is refused explicitly instead.
    """
    repo = InvitationRepository(session, ctx.org_id)
    invitation = await repo.get_by_id(invitation_id)
    if invitation is None:
        raise NotFoundError(
            "Invitation not found.",
            log_detail=(
                f"invitation {invitation_id} not visible in org {ctx.org_id}"
            ),
        )
    if invitation.status != INVITATION_OPEN_STATUS:
        raise BusinessRuleError(
            "That invitation is no longer pending.",
            log_detail=(
                f"invitation {invitation_id} status is {invitation.status!r}"
            ),
        )

    updated = await _update_or_translate(
        repo,
        invitation_id,
        invitation.row_version,
        {"status": "revoked"},
        entity="invitation",
    )

    record_audit(
        session,
        org_id=ctx.org_id,
        actor_id=ctx.user_id,
        action=ACTIONS.INVITATION_REVOKED,
        entity="invitations",
        entity_id=invitation_id,
        meta={"email": invitation.email, "role": invitation.role},
        ip=ip,
    )
    return _invitation_out(updated)


async def accept_invitation(
    *,
    payload: schemas.InvitationAcceptIn,
    settings: Settings,
    ip: Optional[str] = None,
) -> schemas.InvitationAcceptedOut:
    """``POST /api/v1/invitations/accept`` — **public**, by necessity.

    The invitee has no account and therefore no token, so this route cannot be
    guarded. It is the only unauthenticated route in the tenancy module and the
    only one outside ``/api/v1/auth`` that is public at all; ``router.py`` declares
    that exception explicitly rather than leaving it as an absent
    ``dependencies=[...]``.

    What proves the caller
    ----------------------
    Possession of a 256-bit token whose SHA-256 matches a stored ``token_hash`` in
    the org named by the token's own prefix. See :func:`_mint_invitation_token` for
    why the prefix is present and why it is not an authorization input.

    Password hashing
    ----------------
    ``security.hash_password_async`` — the ``_async`` wrapper, never the bare
    ``hash_password``. Argon2id at m=64MiB takes roughly 250ms of memory-hard C
    computation, and awaiting that directly on the event loop stalls the whole
    process rather than one request. This matters more here than at login, because
    this route is unauthenticated: an attacker who can call it can schedule those
    250ms deliberately.

    Why the response contains no tokens
    -----------------------------------
    Acceptance proves control of an invitation *link*, not of a password. Returning
    a session would make the link a bearer credential for the account it just
    created — forward the email and you have handed over the account. The client
    sends the user to the login screen, where the password they just chose is the
    credential. Reported as a judgment call; doc 08 shows no accept flow at all.

    The already-registered case
    ---------------------------
    When the invited address already has a user row, ``full_name`` and ``password``
    are **ignored** and the existing account is joined to the org. The endpoint does
    not report which branch it took and does not reject the request for sending a
    password, because either behaviour would tell an anonymous caller holding a
    valid invitation whether that address is registered. It does not touch the
    existing ``password_hash``, which is the part that would otherwise be an account
    takeover: an invitation to a known address must never be able to reset its
    password.
    """
    org_id, token_hash = _parse_invitation_token(payload.token)

    # No identity yet, so this is a plain tenant session. The org comes from the
    # token; RLS then restricts every read and write below to that org, and the
    # hash match is what authenticates.
    async with get_tenant_session(org_id) as session:
        inv_repo = InvitationRepository(session, org_id)
        invitation = await inv_repo.get_by_token_hash(token_hash)

        if invitation is None:
            logger.info("invitation_accept_failed reason=no_match org=%s", org_id)
            raise BusinessRuleError(
                _GENERIC_INVITATION_FAILURE,
                log_detail=(
                    f"no live invitation with that token hash in org {org_id}"
                ),
            )
        if invitation.status != INVITATION_OPEN_STATUS:
            logger.info(
                "invitation_accept_failed reason=status id=%s status=%s",
                invitation.id,
                invitation.status,
            )
            raise BusinessRuleError(
                _GENERIC_INVITATION_FAILURE,
                log_detail=(
                    f"invitation {invitation.id} status is {invitation.status!r}"
                ),
            )
        if invitation.expires_at <= _utcnow():
            logger.info(
                "invitation_accept_failed reason=expired id=%s expired_at=%s",
                invitation.id,
                invitation.expires_at,
            )
            raise BusinessRuleError(
                _GENERIC_INVITATION_FAILURE,
                log_detail=(
                    f"invitation {invitation.id} expired at {invitation.expires_at}"
                ),
            )

        invited_email = invitation.email
        invited_role = invitation.role
        invited_branch_id = invitation.branch_id

        user_repo = UserRepository(session)
        user = await user_repo.get_by_email(invited_email)
        user_created = False

        if user is None:
            password_hash = await security.hash_password_async(
                payload.password, settings
            )
            user = await user_repo.create(
                email=invited_email,
                full_name=payload.full_name,
                password_hash=password_hash,
                status="active",
            )
            user_created = True
            record_audit(
                session,
                org_id=org_id,
                actor_id=user.id,
                action=ACTIONS.USER_CREATED,
                entity="users",
                entity_id=user.id,
                meta={
                    "email": invited_email,
                    "via": "invitation",
                    "invitationId": str(invitation.id),
                },
                ip=ip,
            )

        user_id = user.id

        mem_repo = MembershipRepository(session, org_id)
        membership = await mem_repo.get_for_user(user_id)
        if membership is None:
            membership = await mem_repo.create(
                user_id=user_id,
                role=invited_role,
                branch_id=invited_branch_id,
                status="active",
            )
            membership_action = ACTIONS.MEMBERSHIP_CREATED
        else:
            # The ``status='invited'`` row written at invite time for an
            # already-registered address. Activate it, and apply the invitation's
            # role and branch — which is a role *change* if the row was already
            # active, so that case is refused rather than silently applied.
            if membership.status == "active":
                logger.info(
                    "invitation_accept_already_member org=%s user=%s",
                    org_id,
                    user_id,
                )
                raise BusinessRuleError(
                    _GENERIC_INVITATION_FAILURE,
                    log_detail=(
                        f"user {user_id} is already an active member of org "
                        f"{org_id}; refusing to re-apply role {invited_role!r} "
                        f"from invitation {invitation.id}"
                    ),
                )
            membership = await _update_or_translate(
                mem_repo,
                membership.id,
                membership.row_version,
                {
                    "status": "active",
                    "role": invited_role,
                    "branch_id": invited_branch_id,
                },
                entity="membership",
            )
            # Not MEMBERSHIP_CREATED: the row already existed. See ACTIONS.
            membership_action = ACTIONS.MEMBERSHIP_ACTIVATED

        await _update_or_translate(
            inv_repo,
            invitation.id,
            invitation.row_version,
            {
                "status": "accepted",
                "accepted_at": _utcnow(),
                "accepted_user_id": user_id,
            },
            entity="invitation",
        )

        record_audit(
            session,
            org_id=org_id,
            actor_id=user_id,
            action=membership_action,
            entity="memberships",
            entity_id=membership.id,
            meta={
                "userId": str(user_id),
                "role": invited_role,
                "reason": "invitation_accepted",
                "invitationId": str(invitation.id),
            },
            ip=ip,
        )
        record_audit(
            session,
            org_id=org_id,
            # The actor is the invitee. They are unauthenticated at the moment of
            # the request, but they are not anonymous: the row id here is either
            # the account they proved control of the invitation for, or the one
            # this very transaction created for them. This is a real actor, not a
            # header-supplied string.
            actor_id=user_id,
            action=ACTIONS.INVITATION_ACCEPTED,
            entity="invitations",
            entity_id=invitation.id,
            meta={
                "email": invited_email,
                "role": invited_role,
                "userCreated": user_created,
            },
            ip=ip,
        )

        result = schemas.InvitationAcceptedOut(
            org_id=org_id,
            user_id=user_id,
            email=invited_email,
            role=invited_role,
            branch_id=invited_branch_id,
            user_created=user_created,
        )

    logger.info(
        "invitation_accepted org=%s user=%s user_created=%s",
        org_id,
        result.user_id,
        user_created,
    )
    return result


# ---------------------------------------------------------------------------
# Memberships
# ---------------------------------------------------------------------------
async def update_membership(
    *,
    session: AsyncSession,
    ctx: OrgContext,
    membership_id: uuid.UUID,
    payload: schemas.MembershipUpdateIn,
    ip: Optional[str] = None,
) -> schemas.MembershipOut:
    """``PATCH /api/v1/memberships/{membership_id}`` — doc 07 §4.2.

    Emits ``membership.role_changed`` (§4.2's own event name) when the role
    actually changes, and ``membership.updated`` otherwise.

    Three authorization rules beyond ``require_roles``, none of them in any
    document, all listed in the P1-T8 report:

    1. **No self-modification.** A caller cannot change their own membership. This
       blocks the escalation ``coaching_admin -> super_admin`` on your own row,
       which ``require_roles("coaching_admin", "super_admin")`` alone would permit,
       and it also blocks the accidental self-lockout of demoting yourself out of
       the role that let you make the change.
    2. **Only a ``super_admin`` may grant or revoke ``super_admin``.** Rule 1
       stops you escalating *yourself*; without this, two ``coaching_admin``
       accounts could escalate *each other* in two requests.
    3. **``branch_id`` must name a live branch in this org.** The FK would catch a
       branch in another org only because RLS makes it invisible — the resulting
       error would be an opaque 500 rather than a 422 naming the field.
    """
    repo = MembershipRepository(session, ctx.org_id)
    membership = await repo.get_by_id(membership_id)
    if membership is None:
        raise NotFoundError(
            "Membership not found.",
            log_detail=(
                f"membership {membership_id} not visible in org {ctx.org_id}"
            ),
        )

    if membership.user_id == ctx.user_id:
        logger.warning(
            "membership_self_edit_refused actor=%s membership=%s org=%s",
            ctx.user_id,
            membership_id,
            ctx.org_id,
        )
        raise AuthorizationError(
            "You cannot change your own membership.",
            log_detail=(
                f"user {ctx.user_id} attempted to modify their own membership "
                f"{membership_id}"
            ),
        )

    touches_super_admin = (
        payload.role == "super_admin" or membership.role == "super_admin"
    )
    if touches_super_admin and ctx.role not in _SUPER_ADMIN_GRANTORS:
        logger.warning(
            "membership_super_admin_change_refused actor=%s actor_role=%s "
            "membership=%s target_role=%s new_role=%s",
            ctx.user_id,
            ctx.role,
            membership_id,
            membership.role,
            payload.role,
        )
        raise AuthorizationError(
            "You do not have access to this resource.",
            log_detail=(
                f"role {ctx.role!r} cannot change a super_admin membership or "
                f"grant super_admin (membership {membership_id})"
            ),
        )

    changes: dict[str, Any] = {}
    if payload.role is not None:
        changes["role"] = payload.role
    if payload.status is not None:
        changes["status"] = payload.status
    if payload.branch_id is not None:
        branch = await BranchRepository(session, ctx.org_id).get_by_id(
            payload.branch_id
        )
        if branch is None:
            raise ValidationError(
                "That branch does not exist in this organization.",
                log_detail=(
                    f"branch {payload.branch_id} not visible in org {ctx.org_id}"
                ),
            )
        changes["branch_id"] = payload.branch_id

    if not changes:
        raise ValidationError(
            "Provide at least one field to update.",
            log_detail=f"empty PATCH body for membership {membership_id}",
        )

    before = {
        "role": membership.role,
        "status": membership.status,
        "branchId": (
            str(membership.branch_id) if membership.branch_id else None
        ),
    }
    role_changed = "role" in changes and changes["role"] != membership.role

    updated = await _update_or_translate(
        repo, membership_id, payload.row_version, changes, entity="membership"
    )

    record_audit(
        session,
        org_id=ctx.org_id,
        actor_id=ctx.user_id,
        action=(
            ACTIONS.MEMBERSHIP_ROLE_CHANGED
            if role_changed
            else ACTIONS.MEMBERSHIP_UPDATED
        ),
        entity="memberships",
        entity_id=membership_id,
        meta={
            "targetUserId": str(updated.user_id),
            "changed": sorted(changes.keys()),
            "before": before,
            "after": {
                "role": updated.role,
                "status": updated.status,
                "branchId": (
                    str(updated.branch_id) if updated.branch_id else None
                ),
            },
        },
        ip=ip,
    )
    logger.info(
        "membership_updated membership=%s org=%s actor=%s changed=%s",
        membership_id,
        ctx.org_id,
        ctx.user_id,
        sorted(changes.keys()),
    )
    return _membership_out(updated)


async def list_memberships(
    *,
    session: AsyncSession,
    ctx: OrgContext,
    org_id: uuid.UUID,
    limit: int,
    offset: int,
) -> tuple[list[schemas.MembershipOut], int]:
    """``GET /api/v1/orgs/{org_id}/memberships`` — an added route.

    Needed because ``PATCH /memberships/{id}`` requires an id and a ``rowVersion``
    the client has to get from somewhere, and doc 07 §4.2 provides no route that
    returns either. Doc 07 §8.1's "Manage users" row gates it to
    ``coaching_admin``/``super_admin``. Reported as an added route.
    """
    assert_path_org(ctx, org_id)
    rows = await MembershipRepository(session, ctx.org_id).list_for_org(
        limit=limit, offset=offset
    )
    items = [_membership_out(m) for m in rows]
    return items, len(items)


# ---------------------------------------------------------------------------
# Identity reads — no org context
# ---------------------------------------------------------------------------
async def list_own_orgs(
    *, actor_user_id: uuid.UUID
) -> list[schemas.OrgMembershipOut]:
    """``GET /api/v1/orgs`` — doc 07 §4.2's "list user's organizations".

    Runs in ``get_identity_session(actor_user_id)`` with **no** org, so
    ``app.org_id`` is unset and only revision 0027's ``memberships_self_select``
    policy admits rows. Two things follow, and both are proof-4 assertions rather
    than claims:

    * The result is exactly the caller's own active memberships. No ``user_id``
      appears in the query at all (see
      :meth:`app.modules.tenancy.repository.IdentityRepository._self_memberships`),
      so there is no parameter through which a caller could ask about someone else.
    * With ``app.user_id`` unset the policy predicate is ``NULL`` and the query
      returns zero rows rather than raising or leaking. That only works because
      revision 0027 also rewrote the org policy's predicate with
      ``NULLIF(current_setting('app.org_id', true), '')`` — the unhardened
      ``current_setting('app.org_id')::uuid`` **raises** on an unset GUC, and an
      erroring policy predicate aborts the whole query no matter how many other
      permissive policies would have passed.
    """
    async with get_identity_session(actor_user_id) as session:
        rows = await IdentityRepository(session).list_own_memberships()
        result = [
            schemas.OrgMembershipOut(
                org_id=row.org_id,
                org_name=row.org_name,
                org_type=row.org_type,
                org_status=row.org_status,
                membership_id=row.membership_id,
                role=row.role,
                branch_id=row.branch_id,
                membership_status=row.membership_status,
            )
            for row in rows
        ]

    logger.info("orgs_listed user_id=%s count=%s", actor_user_id, len(result))
    return result


async def authorize_org_switch(
    *, actor_user_id: uuid.UUID, target_org_id: uuid.UUID
) -> schemas.OrgMembershipOut:
    """Verify an active membership in ``target_org_id`` and return it.

    This is the check that keeps ``POST /auth/switch-org`` from being an
    authorization bypass. Doc 08 line 937 says only "API validates membership";
    this is that validation, and it is deliberately a separate function from the
    token minting so that no future edit to the router can mint first and check
    afterwards.

    Returns the membership so the caller can re-derive the **role** from the target
    org rather than carrying the old claim forward. That distinction is not in any
    document and is the single most important thing about this function: ``role``
    is a property of a *membership*, not of a user, so copying the old token's role
    into the new one would let a ``coaching_admin`` in org A act as a
    ``coaching_admin`` in org B where they are only a ``teacher``.

    Raises ``AuthorizationError`` (403), never 404. "You are not a member of that
    org" and "that org does not exist" must be indistinguishable, or this endpoint
    becomes an org-existence oracle for anyone with an account.
    """
    async with get_identity_session(actor_user_id) as session:
        row = await IdentityRepository(session).get_own_membership_in(target_org_id)

    if row is None:
        logger.warning(
            "org_switch_denied user_id=%s target_org=%s reason=no_active_membership",
            actor_user_id,
            target_org_id,
        )
        raise AuthorizationError(
            "You do not have access to this resource.",
            log_detail=(
                f"user {actor_user_id} has no active membership in org "
                f"{target_org_id} (or the org does not exist / is deleted)"
            ),
        )

    return schemas.OrgMembershipOut(
        org_id=row.org_id,
        org_name=row.org_name,
        org_type=row.org_type,
        org_status=row.org_status,
        membership_id=row.membership_id,
        role=row.role,
        branch_id=row.branch_id,
        membership_status=row.membership_status,
    )


async def switch_active_org(
    *,
    actor_user_id: uuid.UUID,
    current_org_id: Optional[uuid.UUID],
    session_id: str,
    target_org_id: uuid.UUID,
    settings: Settings,
    ip: Optional[str] = None,
) -> tuple[str, uuid.UUID, str, int]:
    """Doc 08 lines 934-938 — re-mint an access token for a different active org.

    Returns ``(access_token, org_id, role, expires_in)``.

    Order of operations, and why it is this order
    --------------------------------------------
    1. :func:`authorize_org_switch` — verify a real **active** membership in the
       target org. Raises 403 and returns nothing if there is none.
    2. Write the durable ``auth.org_switched`` audit row.
    3. Retarget the refresh family so a later refresh does not revert the switch.
    4. Mint the new access token.

    The check is first, in its own function, and its result is what supplies the role
    for step 4 — so there is no code path that mints a token for an org the caller was
    not verified against. Doc 08 line 937 says only "API validates membership"; this
    is that sentence made executable, and it is the difference between this endpoint
    and an authorization bypass that hands any authenticated user any tenant.

    The role is re-derived, never carried forward
    --------------------------------------------
    ``role`` for the new token comes from the **target** org's membership row, not
    from the caller's current token. No document states this and it is the single
    most important property of this function: ``role`` is a property of a
    *membership*, so copying the old claim forward would make a ``coaching_admin`` in
    org A into a ``coaching_admin`` in org B where they are only a ``teacher``. That
    is a full privilege escalation reachable by anyone who is an admin of any single
    organization — including one they created themselves through ``POST /orgs``, which
    any authenticated user may do. Reported as a judgment call.

    Switching to the org you are already in is permitted rather than rejected. It is
    the natural way for a client to pick up a role that changed underneath it (an
    admin demoted them via ``PATCH /memberships/:id``, whose effect is otherwise
    invisible until the access token expires), and refusing it would buy nothing.
    """
    membership = await authorize_org_switch(
        actor_user_id=actor_user_id, target_org_id=target_org_id
    )

    await record_org_switch(
        actor_user_id=actor_user_id,
        from_org_id=current_org_id,
        to_org_id=target_org_id,
        to_role=membership.role,
        ip=ip,
    )

    await tokens.retarget_family(
        session_id=session_id,
        org_id=target_org_id,
        role=membership.role,
    )

    access_token = security.create_access_token(
        user_id=actor_user_id,
        active_org_id=target_org_id,
        role=membership.role,
        # The same ``sid``, deliberately. A new session id would orphan the refresh
        # family — ``get_current_user`` checks the revocation tombstone by ``sid``,
        # so a token bearing an ``sid`` no family knows about could never be revoked
        # by logout or by reuse detection. An org switch is a change of scope within
        # one session, not a new session.
        session_id=session_id,
        settings=settings,
    )

    logger.info(
        "org_switched user_id=%s from_org=%s to_org=%s role=%s sid=%s",
        actor_user_id,
        current_org_id,
        target_org_id,
        membership.role,
        session_id,
    )
    return (
        access_token,
        target_org_id,
        membership.role,
        settings.JWT_ACCESS_EXPIRY_MINUTES * 60,
    )


async def record_org_switch(
    *,
    actor_user_id: uuid.UUID,
    from_org_id: Optional[uuid.UUID],
    to_org_id: uuid.UUID,
    to_role: str,
    ip: Optional[str] = None,
) -> None:
    """Write the durable ``auth.org_switched`` audit row.

    Its own transaction, and it has to be: the mutation an org switch performs is
    to a *token*, not to a table, so there is no other transaction to join. The
    consequence is stated plainly rather than glossed — this is the one audit row
    in P1-T8 that is **not** atomic with the thing it records. A failure between
    the audit commit and the token mint records a switch that did not happen. The
    ordering is chosen so that is the direction the error goes: an over-recorded
    switch is a false entry in a log, while minting the token first and failing to
    record it would be an unlogged privilege change.

    ``org_id`` on the row is the **target** org, because that is the tenant whose
    access was granted and the tenant whose administrators should see the event in
    their audit view. It also has to be, mechanically: ``audit_logs``' RLS
    ``WITH CHECK`` requires ``org_id = current_setting('app.org_id')``, and the
    session below is opened on the target.
    """
    async with get_identity_session(actor_user_id, org_id=to_org_id) as session:
        record_audit(
            session,
            org_id=to_org_id,
            actor_id=actor_user_id,
            action=ACTIONS.AUTH_ORG_SWITCHED,
            entity="memberships",
            entity_id=None,
            meta={
                "fromOrgId": str(from_org_id) if from_org_id else None,
                "toOrgId": str(to_org_id),
                "role": to_role,
            },
            ip=ip,
        )


async def describe_principal(
    *, actor_user_id: uuid.UUID
) -> tuple[Any, list[schemas.OrgMembershipOut]]:
    """The data behind ``GET /api/v1/auth/me`` — doc 07 §4.2 lists it under Tenancy.

    Returns ``(user_row_snapshot, memberships)``. ``users`` is RLS-exempt, so the
    profile read needs no policy; the membership list is the same identity-scoped
    read as :func:`list_own_orgs` and carries the same guarantee that it can only
    ever describe the session's own principal.

    The user row is returned as a small dataclass-free tuple of plain values rather
    than the ORM instance, for the reason given in the module docstring.
    """
    async with get_identity_session(actor_user_id) as session:
        user = await UserRepository(session).get_by_id(actor_user_id)
        if user is None:
            # A valid, unexpired, unrevoked token for a user row that is gone or
            # soft-deleted. 401 rather than 404: the credential is the thing that
            # is no longer valid, and ``AuthenticationError`` is what tells the
            # client to log in again.
            logger.warning("me_user_row_missing user_id=%s", actor_user_id)
            raise AuthenticationError(
                "Authentication is required.",
                eh_ref="EH-15",
                log_detail=(
                    f"token subject {actor_user_id} has no live users row"
                ),
            )

        profile = {
            "id": user.id,
            "email": user.email,
            "full_name": user.full_name,
            "status": user.status,
        }
        rows = await IdentityRepository(session).list_own_memberships()
        memberships = [
            schemas.OrgMembershipOut(
                org_id=row.org_id,
                org_name=row.org_name,
                org_type=row.org_type,
                org_status=row.org_status,
                membership_id=row.membership_id,
                role=row.role,
                branch_id=row.branch_id,
                membership_status=row.membership_status,
            )
            for row in rows
        ]

    return profile, memberships


async def resolve_branch_id(
    *, org_id: uuid.UUID, user_id: uuid.UUID
) -> Optional[uuid.UUID]:
    """The membership-derived ``branch_id`` for ``app/common/deps.py``'s ``get_scope``.

    Doc 07 §8.2: "``memberships.branch_id`` narrows a member to one branch; null
    means org-wide access." That sentence is the entire specification for this
    function, and it is worth noting where it *is not*: the brief cites §7.3, which
    is Refresh Token Rotation and says nothing about scope, and ``deps.py``'s own
    docstring quotes §7.5 as "Resolves branch/subject scope for filtering" when
    §7.5 actually reads "Resolves the teacher's **program/subject** scope for query
    narrowing". Both citations are wrong; §8.2 is the real one. Recorded in the
    P1-T8 report as a premise correction rather than patched silently.

    A plain ``get_tenant_session`` is enough — the caller's org is known (that is
    what ``get_org_context`` produced) and the membership being read is in it, so
    ``tenant_isolation_policy`` admits the row without needing ``app.user_id``.

    Returns ``None`` both for "org-wide access" and for "no active membership".
    Those are different situations and this function cannot distinguish them, which
    is safe here only because of where it is called: ``get_scope`` runs *after*
    ``get_org_context``, which has already established that the caller has a role
    in this org. If it is ever called from somewhere that has not, "no membership"
    would silently read as "org-wide" — the widest possible scope — so the ordering
    is load-bearing and is asserted by proof 6.
    """
    async with get_tenant_session(org_id) as session:
        membership = await MembershipRepository(session, org_id).get_active_for_user(
            user_id
        )
        if membership is None:
            logger.warning(
                "scope_no_active_membership user_id=%s org_id=%s", user_id, org_id
            )
            return None
        return membership.branch_id


# ---------------------------------------------------------------------------
# Error translation
# ---------------------------------------------------------------------------
async def _update_or_translate(
    repo: Any,
    entity_id: uuid.UUID,
    expected_version: int,
    changes: dict[str, Any],
    *,
    entity: str,
) -> Any:
    """``repo.update`` with P1-T5's exceptions mapped to HTTP-shaped ones.

    ``errors.py`` already registers handlers for ``OptimisticLockError`` and
    ``EntityNotFoundError``, so this translation is not strictly required for a
    correct status code. It exists so the *message* is written for the person
    reading it: the repository's exception text names a table and a row version,
    which is a log line, not something to show a user. The specific numbers still
    reach the log through ``log_detail``.
    """
    try:
        return await repo.update(entity_id, expected_version, **changes)
    except OptimisticLockError as exc:
        raise ConflictError(
            "This record was modified by someone else. Reload and try again.",
            log_detail=str(exc),
        ) from exc
    except EntityNotFoundError as exc:
        raise NotFoundError(
            f"{entity.capitalize()} not found.", log_detail=str(exc)
        ) from exc
    except IntegrityError as exc:
        raise ConflictError(
            "That change conflicts with an existing record.",
            log_detail=f"integrity error updating {entity} {entity_id}: {exc.orig}",
        ) from exc


__all__ = [
    "INVITATION_TTL_DAYS",
    "ORG_CREATOR_ROLE",
    "accept_invitation",
    "assert_path_org",
    "authorize_org_switch",
    "create_branch",
    "create_invitation",
    "create_organization",
    "delete_branch",
    "delete_organization",
    "describe_principal",
    "get_organization",
    "list_branches",
    "list_invitations",
    "list_memberships",
    "list_own_orgs",
    "record_org_switch",
    "resolve_branch_id",
    "revoke_invitation",
    "switch_active_org",
    "update_branch",
    "update_membership",
    "update_organization",
]
