"""``invitations`` — pending membership invites (P1-T8).

REPORTED OVERRIDE — this table is not in doc 03, and ADR-012 argues against it
--------------------------------------------------------------------------------
Stated plainly, because it is the largest design decision in P1-T8:

* ``IMPLEMENTATION_PLAN.md`` line 143 names "invitations" as part of the tenancy
  module's deliverable.
* Doc 03 has **no §5 section** for an ``invitations`` table. A grep across
  ``docs/`` finds no column list, no constraint list and no data-dictionary entry
  for one.
* Doc 07 §4.2's "Owns tables" line for the Tenancy module reads
  ``organizations``, ``branches``, ``users``, ``memberships`` — ``invitations`` is
  **absent**.
* ADR-012 §3 explicitly *rejects* a separate invitations table and models the
  invite flow as ``memberships.status = 'invited'``. Doc 01 FR-1.5 agrees: "the
  membership is created with ``status = invited`` and grants no access until
  accepted."

The decisive fact against ADR-012's shape: ``memberships.user_id`` is
``NOT NULL``. An invitation addressed to an email that has **no user row yet** —
which is the flow P1-T8 is required to implement, since accepting an invite
creates the user and hashes their password — cannot be represented as a
``memberships`` row at all. There is no user id to put in it. ADR-012's model
works only for inviting someone who already has an account.

Resolution taken, so that neither source is simply ignored:

1. This table exists, and is the record of the *invitation* (who was asked, by
   whom, to what role, with what expiry, and whether the ask is still open).
2. When the invited email **already has a user row**, the service *also* writes
   the ``memberships`` row with ``status = 'invited'``, exactly as ADR-012 and
   FR-1.5 describe. The two representations agree rather than compete.
3. ADR-012 needs an amendment recording the ``user_id NOT NULL`` consequence.
   Flagged in the P1-T8 report; not edited here, because amending an ADR is not
   this task's authority.

Every vocabulary and column below that doc 03 does not specify is listed in the
P1-T8 report as an authored decision.

The token is never stored
-------------------------
``token_hash`` holds ``sha256(token)`` and the plaintext exists only in the
response to ``POST /orgs/{org_id}/invitations``. This mirrors
``app/modules/auth/tokens.py``'s treatment of refresh tokens and for the same
reason: a database dump must not contain credentials that grant org membership.

The token's *format* is load-bearing, and documented on
``app.modules.tenancy.service.create_invitation``: it is ``<org_uuid>.<secret>``.
The org prefix is not an authorization input — it exists so that the **public**
accept endpoint can set ``app.org_id`` and therefore satisfy this table's RLS
policy before it looks anything up. A caller who alters the prefix simply scopes
the lookup to an org where the hash does not exist, and gets the same 404 as a
caller who invented the whole token.
"""

from __future__ import annotations

import datetime as _dt
import uuid as _uuid
from typing import TYPE_CHECKING, Optional

from sqlalchemy import DateTime, ForeignKey, Index, Text, text
from sqlalchemy.dialects.postgresql import CITEXT, UUID
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.db.base import (
    Base,
    RowVersionMixin,
    SoftDeleteMixin,
    TimestampMixin,
    UUIDPrimaryKeyMixin,
    check_in,
    row_version_positive,
)
from app.models.membership import MEMBERSHIP_ROLES

if TYPE_CHECKING:  # pragma: no cover - typing only
    from app.models.branch import Branch
    from app.models.organization import Organization
    from app.models.user import User

#: AUTHORED VOCABULARY — doc 03 and doc 07 specify none.
#:
#: ``pending`` and ``expired`` are the two the docs imply: doc 01 FR-1.5 says
#: "Pending invites expire". ``accepted`` and ``revoked`` are the two terminal
#: states the brief's "create/list/revoke" plus "accept" flow requires.
#:
#: ``expired`` is a *materialised* state with no writer in P1-T8. Expiry is
#: evaluated at accept time by comparing ``expires_at`` against ``now()``, so an
#: invitation past its expiry is still stored as ``pending`` and is still
#: correctly refused. The value exists so that a future sweeper job has somewhere
#: to record the transition without a migration. Reported rather than left for a
#: reader to infer from an unused CHECK value.
INVITATION_STATUSES: tuple[str, ...] = ("pending", "accepted", "revoked", "expired")

#: The only status in which an invitation can be accepted or revoked.
INVITATION_OPEN_STATUS: str = "pending"


class Invitation(
    UUIDPrimaryKeyMixin,
    TimestampMixin,
    SoftDeleteMixin,
    RowVersionMixin,
    Base,
):
    """An outstanding request for a person to join an organization."""

    __tablename__ = "invitations"

    # NOT NULL and org-scoped: this is what puts the table under revision 0025's
    # ``_DIRECT_ORG_ID_NOT_NULL`` policy shape (created in 0026 for this table,
    # since 0025 predates it).
    org_id: Mapped[_uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("organizations.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )
    # CITEXT to match ``users.email`` exactly. If this were plain ``text`` then
    # inviting "Asha@example.com" and matching it against a stored
    # "asha@example.com" user row would depend on which side of the comparison
    # the cast landed on — a case-sensitivity bug that only shows up for users
    # who capitalise their own email.
    email: Mapped[str] = mapped_column(CITEXT, nullable=False, index=True)
    role: Mapped[str] = mapped_column(Text, nullable=False)
    # Carried so that an invite can pre-assign a branch, which is what makes
    # ``memberships.branch_id`` (and therefore ``get_scope``'s branch resolution)
    # non-null for an invited teacher. Doc 07 §8.2: "memberships.branch_id
    # narrows a member to one branch; null means org-wide access."
    branch_id: Mapped[Optional[_uuid.UUID]] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("branches.id", ondelete="SET NULL"),
        nullable=True,
    )
    # ON DELETE SET NULL rather than RESTRICT. ``audit_logs.actor_id`` uses
    # RESTRICT because an audit row that loses its actor is a broken audit trail;
    # an invitation that loses its inviter is merely less informative, and
    # blocking a GDPR erasure because the user once sent an invite would be the
    # wrong trade. Nullable for the same reason.
    invited_by: Mapped[Optional[_uuid.UUID]] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("users.id", ondelete="SET NULL"),
        nullable=True,
    )
    #: ``sha256(token)`` as 64 lowercase hex characters. Never the plaintext.
    token_hash: Mapped[str] = mapped_column(Text, nullable=False)
    expires_at: Mapped[_dt.datetime] = mapped_column(
        DateTime(timezone=True), nullable=False
    )
    status: Mapped[str] = mapped_column(
        Text,
        nullable=False,
        server_default=text("'pending'"),
        index=True,
    )
    accepted_at: Mapped[Optional[_dt.datetime]] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
    #: Which user row the acceptance resolved to — either a pre-existing account
    #: or the one the accept flow created. Makes "who actually walked through this
    #: invitation" answerable without correlating timestamps.
    accepted_user_id: Mapped[Optional[_uuid.UUID]] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("users.id", ondelete="SET NULL"),
        nullable=True,
    )

    organization: Mapped["Organization"] = relationship(
        "Organization",
        foreign_keys=[org_id],
    )
    branch: Mapped[Optional["Branch"]] = relationship(
        "Branch",
        foreign_keys=[branch_id],
    )
    inviter: Mapped[Optional["User"]] = relationship(
        "User",
        foreign_keys=[invited_by],
    )
    accepted_user: Mapped[Optional["User"]] = relationship(
        "User",
        foreign_keys=[accepted_user_id],
    )

    __table_args__ = (
        # Same three-value vocabulary as ``memberships.role``. Deliberately
        # ``MEMBERSHIP_ROLES`` by reference rather than a copied literal: an
        # invitation that can grant a role ``memberships`` cannot store would be
        # an invitation that is impossible to accept.
        check_in("role", MEMBERSHIP_ROLES, name="role"),
        check_in("status", INVITATION_STATUSES, name="status"),
        row_version_positive(),
        # A token must identify at most one invitation. Partial on
        # ``deleted_at IS NULL`` per ADR-004's rule for every uniqueness
        # constraint in this schema.
        Index(
            "uq_invitations_token_hash",
            "token_hash",
            unique=True,
            postgresql_where=text("deleted_at IS NULL"),
        ),
        # At most one OPEN invitation per (org, email). Narrower than a plain
        # partial-unique on the pair, on purpose: revoking an invite and sending a
        # new one to the same address has to work, and so does re-inviting someone
        # whose earlier membership ended. Only two live *pending* asks for the
        # same address collide.
        Index(
            "uq_invitations_org_id_email",
            "org_id",
            "email",
            unique=True,
            postgresql_where=text("deleted_at IS NULL AND status = 'pending'"),
        ),
        # The accept path's only lookup: hash first, then org (RLS has already
        # narrowed to the org). Covered by uq_invitations_token_hash, so no
        # separate index is created for it.
        Index("ix_invitations_expires_at", "expires_at"),
    )

    def __repr__(self) -> str:  # pragma: no cover - debugging aid
        return (
            f"<Invitation id={self.id!r} org_id={self.org_id!r} "
            f"email={self.email!r} status={self.status!r}>"
        )


__all__ = ["INVITATION_OPEN_STATUS", "INVITATION_STATUSES", "Invitation"]
