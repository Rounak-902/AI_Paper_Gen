"""``memberships`` — organization-scoped access and role assignment (doc 03 §5).

This is where authority lives. A ``users`` row says *who* someone is; a
``memberships`` row says *what they may do, and in which organization*.
"""

from __future__ import annotations

import uuid as _uuid
from typing import TYPE_CHECKING, Optional

from sqlalchemy import ForeignKey, Index, Text, text
from sqlalchemy.dialects.postgresql import UUID
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

if TYPE_CHECKING:  # pragma: no cover - typing only
    from app.models.branch import Branch
    from app.models.organization import Organization
    from app.models.user import User

#: The three implemented roles — ADR-011, doc 03 §7.2. Exactly three, no more.
#:
#: ADR-011 is emphatic that this is a **constrained text column, not a native
#: PostgreSQL ENUM**: "adding a value to a native ENUM requires ALTER TYPE ... ADD
#: VALUE, which cannot be run inside a transaction block in older versions, cannot
#: be reversed cleanly, and cannot remove a value at all." Extending the vocabulary
#: must stay a one-line CHECK-constraint migration.
MEMBERSHIP_ROLES: tuple[str, ...] = ("teacher", "coaching_admin", "super_admin")

#: Reserved by ADR-011 and doc 03 §7.2, and deliberately **absent from the CHECK
#: constraint**. Kept here as executable documentation so nobody has to guess
#: whether their omission was an oversight:
#:   ``hod``      — departmental approver; needs a department dimension the schema
#:                  does not have.
#:   ``reviewer`` — approve-only; BR-6 already enforces author != approver
#:                  behaviourally, so a separate role adds a permission axis with no
#:                  distinct workflow.
#:   ``viewer``   — read-only observer; no recorded customer requirement.
#: Adding one means: extend MEMBERSHIP_ROLES, write the CHECK migration, extend the
#: capability matrix in doc 07 §8.1 and the client mirror in doc 08 §7.2.
RESERVED_MEMBERSHIP_ROLES: tuple[str, ...] = ("hod", "reviewer", "viewer")

#: doc 03 §5 / §7.1 / ADR-012 §3. ``invited`` is the load-bearing value: it is a
#: membership row that exists and deliberately grants nothing yet, which is how
#: FR-1.5's "pending invites expire" is modelled without a separate table. ADR-012
#: rejects overloading ``deleted_at`` for this, because the two mean different
#: things — ``deleted_at`` is "this record is gone", ``invited`` is "this record
#: exists and grants nothing".
MEMBERSHIP_STATUSES: tuple[str, ...] = ("active", "invited", "suspended")


class Membership(
    UUIDPrimaryKeyMixin,
    TimestampMixin,
    SoftDeleteMixin,
    RowVersionMixin,
    Base,
):
    """A user's role within one organization, optionally narrowed to one branch."""

    __tablename__ = "memberships"

    # doc 03 §5: "organization and user deletion may cascade only in hard-delete
    # maintenance scenarios". A database ON DELETE CASCADE fires on exactly that and
    # nothing else, since ordinary deletion under ADR-004 is an UPDATE.
    user_id: Mapped[_uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("users.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )
    org_id: Mapped[_uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("organizations.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )
    # Nullable: null means org-wide access (doc 03 §5, §7.1). ON DELETE SET NULL per
    # doc 03 §5, "deleting a branch should set branch_id to null" — losing a branch
    # widens the member's scope to the org rather than revoking their access.
    branch_id: Mapped[Optional[_uuid.UUID]] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("branches.id", ondelete="SET NULL"),
        nullable=True,
        index=True,
    )
    role: Mapped[str] = mapped_column(Text, nullable=False, index=True)
    status: Mapped[str] = mapped_column(
        Text,
        nullable=False,
        server_default=text("'active'"),
        index=True,
    )

    user: Mapped["User"] = relationship("User", back_populates="memberships")
    organization: Mapped["Organization"] = relationship(
        "Organization",
        back_populates="memberships",
    )
    branch: Mapped[Optional["Branch"]] = relationship(
        "Branch",
        back_populates="memberships",
    )

    __table_args__ = (
        # ADR-011: exactly three values, as a named CHECK on a text column.
        check_in("role", MEMBERSHIP_ROLES, name="role"),
        check_in("status", MEMBERSHIP_STATUSES, name="status"),
        row_version_positive(),
        # doc 03 §5 asks for "unique(user_id, org_id)" — one membership per person
        # per organization. Partial, per ADR-004, so that a revoked-then-reinstated
        # member can be re-added instead of colliding with their own tombstone.
        Index(
            "uq_memberships_user_id_org_id",
            "user_id",
            "org_id",
            unique=True,
            postgresql_where=text("deleted_at IS NULL"),
        ),
        # Revision 0027 added this partial index for the identity-context
        # membership lookup used before an active organization is selected.
        # Keep it in metadata as well so Alembic's model/schema drift check sees
        # the intentionally migrated index rather than proposing its removal.
        Index(
            "ix_memberships_user_id_status",
            "user_id",
            "status",
            postgresql_where=text("deleted_at IS NULL"),
        ),
    )

    def __repr__(self) -> str:  # pragma: no cover - debugging aid
        return (
            f"<Membership id={self.id!r} user_id={self.user_id!r} "
            f"org_id={self.org_id!r} role={self.role!r} status={self.status!r}>"
        )


__all__ = [
    "MEMBERSHIP_ROLES",
    "MEMBERSHIP_STATUSES",
    "RESERVED_MEMBERSHIP_ROLES",
    "Membership",
]
