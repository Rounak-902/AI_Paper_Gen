"""Request/response DTOs for the tenancy endpoints (P1-T8).

Same two bases as ``app/modules/auth/schemas.py`` and for the same reasons: requests
are ``extra="forbid"`` so that a mass-assignment attempt
(``{"name": "X", "status": "active", "row_version": 99}``) is a 422 rather than a
silently-ignored key (doc 07 §20.1), and responses are explicit allowlists so a
column added to ``organizations`` later cannot leak into a response by accident
(doc 07 §11.4).

Both bases are re-imported from the auth module rather than redefined. A second
copy of ``ConfigDict(alias_generator=to_camel, ...)`` is a second place for the
wire casing to drift, and doc 08's client is written against camelCase
unconditionally.

Doc coverage
------------
Doc 07 §4.2 names the routes but specifies **no request or response bodies** for
any of them, and doc 08 shows only ``POST /auth/switch-org``'s
``{ orgId }`` → ``{ accessToken' }``. Every field below is authored. The two that
matter most, both listed in the P1-T8 report:

* ``rowVersion`` is **required** on every update and every delete. P1-T5 built
  optimistic locking into ``BaseRepository.update`` and doc 07 §12.2 requires a 409
  on mismatch; a schema with an optional ``rowVersion`` would let a client opt out
  of concurrency control by omitting a field, which is the same as not having it.
* Invitation expiry is expressed as a server-side TTL constant, not a
  client-supplied ``expiresAt``. Doc 01 FR-1.5 says only "Pending invites expire".
  Letting the caller pick would let a ``coaching_admin`` mint a hundred-year invite
  link; see ``app.modules.tenancy.service.INVITATION_TTL_DAYS``.
"""

from __future__ import annotations

import datetime as _dt
import uuid
from typing import Any, Optional

from pydantic import Field

from app.models.branch import BRANCH_STATUSES
from app.models.membership import MEMBERSHIP_ROLES
from app.models.organization import ORG_STATUSES, ORG_TYPES
from app.modules.auth.schemas import AuthRequest as _RequestBase
from app.modules.auth.schemas import AuthResponse as _ResponseBase

#: Same loose shape check as ``LoginIn.email``, and for the same reason recorded
#: there: pydantic's ``EmailStr`` needs the uninstalled ``email-validator``
#: package, and ``users.email`` has no format constraint in the database either.
#: An invitation to a well-formed-but-nonexistent address and one to a malformed
#: address both simply never get accepted.
_EMAIL_PATTERN = r"^[^@\s]+@[^@\s]+\.[^@\s]+$"

_ROLE_PATTERN = "^(" + "|".join(MEMBERSHIP_ROLES) + ")$"
_ORG_TYPE_PATTERN = "^(" + "|".join(ORG_TYPES) + ")$"
_ORG_STATUS_PATTERN = "^(" + "|".join(ORG_STATUSES) + ")$"
_BRANCH_STATUS_PATTERN = "^(" + "|".join(BRANCH_STATUSES) + ")$"


class TenancyRequest(_RequestBase):
    """Request base — camelCase in, unknown fields rejected."""


class TenancyResponse(_ResponseBase):
    """Response base — camelCase out, explicit fields only."""


# ---------------------------------------------------------------------------
# Organizations
# ---------------------------------------------------------------------------
class OrgCreateIn(TenancyRequest):
    """``POST /api/v1/orgs`` body.

    ``status`` is deliberately absent: a newly created organization is ``active``,
    and letting the creator pick ``archived`` at creation time is a state with no
    meaning. ``region`` is optional and defaults to the column's
    ``'ap-south-1'`` (doc 03 §5).

    ``settings`` is accepted as free-form JSONB because doc 03 §5 defines it as
    ``jsonb NOT NULL DEFAULT '{}'`` with no schema. A size cap is applied — an
    unbounded JSONB body on a create endpoint is a storage-amplification vector,
    and P1-T6's ``BodySizeLimitMiddleware`` caps the *request* but not this one
    field's share of it.
    """

    name: str = Field(min_length=1, max_length=200)
    org_type: str = Field(pattern=_ORG_TYPE_PATTERN)
    region: Optional[str] = Field(default=None, max_length=64)
    settings: Optional[dict[str, Any]] = None


class OrgUpdateIn(TenancyRequest):
    """``PATCH /api/v1/orgs/{org_id}`` body.

    Every mutable field is optional so that a caller can change one thing, but
    ``row_version`` is not — see the module docstring. A body that sets nothing but
    ``rowVersion`` is rejected by the service with a 422 rather than silently
    bumping the version for no change.
    """

    row_version: int = Field(ge=1)
    name: Optional[str] = Field(default=None, min_length=1, max_length=200)
    region: Optional[str] = Field(default=None, max_length=64)
    status: Optional[str] = Field(default=None, pattern=_ORG_STATUS_PATTERN)
    settings: Optional[dict[str, Any]] = None


class OrgDeleteIn(TenancyRequest):
    """``DELETE /api/v1/orgs/{org_id}`` body.

    A body on a DELETE is unusual and is here for one reason: optimistic locking.
    ADR-004 makes deletion an UPDATE, and an UPDATE without a ``row_version`` guard
    can silently discard a concurrent edit. Sending it as a query parameter was the
    alternative and was rejected — a version number in a URL ends up in access
    logs, and DELETE with a body is permitted by RFC 9110 §9.3.5.
    """

    row_version: int = Field(ge=1)


class OrgOut(TenancyResponse):
    """An organization, as an allowlist.

    ``subscription_id`` is included because the billing module (P2) needs it and it
    is not sensitive; ``deleted_at`` is not, because every read path filters it and
    a non-null value would mean the row should not have been returned at all.
    """

    id: uuid.UUID
    name: str
    org_type: str
    region: str
    status: str
    settings: dict[str, Any]
    subscription_id: Optional[uuid.UUID] = None
    row_version: int
    created_at: _dt.datetime
    updated_at: _dt.datetime


class OrgMembershipOut(TenancyResponse):
    """One entry of ``GET /api/v1/orgs`` — an org plus the caller's place in it.

    This is the shape doc 08 line 913's login ``orgs`` array wants and the
    org-picker screen (doc 08 §6.5) renders: the org's identity, plus *the caller's*
    role and branch within it, so the client can label the picker without a second
    round trip per org.

    ``membership_status`` is always ``"active"`` — revision 0027's
    ``memberships_self_select`` policy makes any other value invisible. It is
    carried anyway so that a future relaxation of the policy cannot silently change
    what this endpoint means.
    """

    org_id: uuid.UUID
    org_name: str
    org_type: str
    org_status: str
    membership_id: uuid.UUID
    role: str
    branch_id: Optional[uuid.UUID] = None
    membership_status: str


# ---------------------------------------------------------------------------
# Branches
# ---------------------------------------------------------------------------
class BranchCreateIn(TenancyRequest):
    name: str = Field(min_length=1, max_length=200)
    region: Optional[str] = Field(default=None, max_length=64)


class BranchUpdateIn(TenancyRequest):
    row_version: int = Field(ge=1)
    name: Optional[str] = Field(default=None, min_length=1, max_length=200)
    region: Optional[str] = Field(default=None, max_length=64)
    status: Optional[str] = Field(default=None, pattern=_BRANCH_STATUS_PATTERN)


class BranchDeleteIn(TenancyRequest):
    row_version: int = Field(ge=1)


class BranchOut(TenancyResponse):
    id: uuid.UUID
    org_id: uuid.UUID
    name: str
    region: Optional[str] = None
    status: str
    row_version: int
    created_at: _dt.datetime
    updated_at: _dt.datetime


# ---------------------------------------------------------------------------
# Invitations
# ---------------------------------------------------------------------------
class InvitationCreateIn(TenancyRequest):
    """``POST /api/v1/orgs/{org_id}/invitations`` body.

    No ``expiresAt`` field — see the module docstring. No ``status`` field either:
    an invitation is created ``pending`` and every other value is reached through
    an action (accept, revoke), not by assignment.
    """

    email: str = Field(min_length=3, max_length=320, pattern=_EMAIL_PATTERN)
    role: str = Field(pattern=_ROLE_PATTERN)
    branch_id: Optional[uuid.UUID] = None


class InvitationOut(TenancyResponse):
    """An invitation, as an allowlist.

    ``token_hash`` is **absent**, permanently. It is a credential digest, and a
    listing endpoint that returned it would let any ``coaching_admin`` who can read
    the list also verify guesses at the token offline. The plaintext token appears
    exactly once, in :class:`InvitationCreatedOut`, and is never stored.
    """

    id: uuid.UUID
    org_id: uuid.UUID
    email: str
    role: str
    branch_id: Optional[uuid.UUID] = None
    invited_by: Optional[uuid.UUID] = None
    status: str
    expires_at: _dt.datetime
    accepted_at: Optional[_dt.datetime] = None
    row_version: int
    created_at: _dt.datetime


class InvitationCreatedOut(TenancyResponse):
    """The response to creating an invitation — the only place the token appears.

    P1 has no outbound email (doc 07 §4.x lists no mail provider and no queue for
    it), so the token is returned to the caller to deliver out of band. Stated as a
    gap rather than presented as a design: once a mail sender exists, ``token``
    should be removed from this response and the link emailed to the invitee
    instead, because returning it to the *inviter* means the inviter can accept
    their own invitation and create an account for someone else's address.
    """

    invitation: InvitationOut
    token: str


class InvitationAcceptIn(TenancyRequest):
    """``POST /api/v1/invitations/accept`` body — a **public** endpoint.

    ``full_name`` and ``password`` are ignored when the invited address already has
    a user row; see
    :func:`app.modules.tenancy.service.accept_invitation` for why the endpoint does
    not branch on their presence (it would tell an anonymous caller whether an email
    is registered).

    ``password`` carries the same ``max_length`` as ``LoginIn.password`` and for the
    same reason — Argon2id's cost scales with input length, so an unbounded field is
    a memory-and-CPU amplification vector on an unauthenticated route.
    """

    token: str = Field(min_length=16, max_length=512)
    full_name: str = Field(min_length=1, max_length=200)
    password: str = Field(min_length=12, max_length=1024)


class InvitationAcceptedOut(TenancyResponse):
    """What acceptance returns.

    Deliberately **not** a token pair. Acceptance proves control of an invitation
    link, not of a password, and issuing a session here would make the emailed link
    a bearer credential for the account it just created. The client is expected to
    send the user to the login screen, which is also what doc 08's flow does after
    registration. Reported as a judgment call.
    """

    org_id: uuid.UUID
    user_id: uuid.UUID
    email: str
    role: str
    branch_id: Optional[uuid.UUID] = None
    user_created: bool


# ---------------------------------------------------------------------------
# Memberships
# ---------------------------------------------------------------------------
class MembershipUpdateIn(TenancyRequest):
    """``PATCH /api/v1/memberships/{membership_id}`` body.

    ``role`` and ``status`` are both here because doc 07 §4.2's emitted events
    include ``membership.role_changed`` and doc 03 §5 gives ``memberships.status``
    the ``active``/``invited``/``suspended`` vocabulary — suspending a member is
    the only way to revoke access without deleting the row, and ``app_user`` has no
    DELETE grant.
    """

    row_version: int = Field(ge=1)
    role: Optional[str] = Field(default=None, pattern=_ROLE_PATTERN)
    status: Optional[str] = Field(
        default=None, pattern="^(active|invited|suspended)$"
    )
    branch_id: Optional[uuid.UUID] = None


class MembershipOut(TenancyResponse):
    id: uuid.UUID
    org_id: uuid.UUID
    user_id: uuid.UUID
    role: str
    status: str
    branch_id: Optional[uuid.UUID] = None
    row_version: int
    created_at: _dt.datetime
    updated_at: _dt.datetime


# ---------------------------------------------------------------------------
# Shared
# ---------------------------------------------------------------------------
class DeletedOut(TenancyResponse):
    """What a soft-delete route returns.

    The delete routes answer **200 with this body**, not 204. Two reasons, both
    consequences of decisions made earlier rather than preferences:

    * A 204 must have no body, and every response in this application is wrapped in
      doc 07 §22.3's ``{ success, data, meta }`` envelope by ``EnvelopeRoute``. A 204
      would be the one response in the API with no ``meta.requestId``, which is the
      field doc 08 §10.6 extracts from *every* response for correlation.
    * ADR-004 deletion is an UPDATE, so there is something true to report: the row
      still exists and is now marked deleted. ``deleted: true`` says that; 204 "no
      content" would imply the resource is gone.
    """

    id: uuid.UUID
    deleted: bool = True


class PrincipalOut(TenancyResponse):
    """``GET /api/v1/auth/me`` — doc 07 §4.2 lists the route and no body for it.

    Composed of a profile and the caller's memberships, which is the union of what
    doc 08 line 913's login response carries (``user`` + ``orgs``) minus the token.
    That symmetry is deliberate: a client that has just refreshed and lost its
    in-memory user object can call this and repopulate exactly the same state it had
    after login, without a second round trip per org.

    ``active_org_id`` and ``active_role`` come from the *token's* verified claims,
    not from the membership list, so the response also answers "which org am I
    currently acting in" — the question doc 08 §9.7's org-switch flow needs answered
    after a hard reset.
    """

    id: uuid.UUID
    email: str
    full_name: str
    status: str
    active_org_id: Optional[uuid.UUID] = None
    active_role: Optional[str] = None
    memberships: list[OrgMembershipOut]


__all__ = [
    "BranchCreateIn",
    "BranchDeleteIn",
    "BranchOut",
    "BranchUpdateIn",
    "DeletedOut",
    "InvitationAcceptIn",
    "InvitationAcceptedOut",
    "InvitationCreateIn",
    "InvitationCreatedOut",
    "InvitationOut",
    "MembershipOut",
    "MembershipUpdateIn",
    "OrgCreateIn",
    "OrgDeleteIn",
    "OrgMembershipOut",
    "OrgOut",
    "OrgUpdateIn",
    "PrincipalOut",
    "TenancyRequest",
    "TenancyResponse",
]
