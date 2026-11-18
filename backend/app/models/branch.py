"""``branches`` — optional organization subdivision (doc 03 §5, "branches").

A coaching chain with several centres models each centre as a branch. Branches are
optional: a membership with ``branch_id IS NULL`` has org-wide access.
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
from app.models.organization import ORG_STATUSES

if TYPE_CHECKING:  # pragma: no cover - typing only
    from app.models.membership import Membership
    from app.models.organization import Organization
    from app.models.program import Program

#: doc 03 §5 gives ``branches.status`` the same undocumented "lifecycle values"
#: description as ``organizations.status``, so the two share one vocabulary rather
#: than inventing a second shape. Same caveat as ORG_STATUSES: confirmed with the
#: product owner during P1-T3, pending an addition to doc 03 §7.3.
BRANCH_STATUSES: tuple[str, ...] = ORG_STATUSES


class Branch(
    UUIDPrimaryKeyMixin,
    TimestampMixin,
    SoftDeleteMixin,
    RowVersionMixin,
    Base,
):
    """Optional subdivision of an organization."""

    __tablename__ = "branches"

    # ON DELETE CASCADE per doc 03 §5, "deleting an organization cascades branches".
    # Under ADR-004 that path only fires during an explicit hard-delete maintenance
    # operation (tenant offboarding, GDPR erasure) — ordinary deletion is an UPDATE
    # that sets deleted_at and cascades nothing.
    org_id: Mapped[_uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("organizations.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )
    name: Mapped[str] = mapped_column(Text, nullable=False)
    region: Mapped[Optional[str]] = mapped_column(Text, nullable=True)
    status: Mapped[str] = mapped_column(
        Text,
        nullable=False,
        server_default=text("'active'"),
        index=True,
    )

    organization: Mapped["Organization"] = relationship(
        "Organization",
        back_populates="branches",
    )
    memberships: Mapped[list["Membership"]] = relationship(
        "Membership",
        back_populates="branch",
        cascade="save-update, merge",
    )
    # Added in P1-T4 (doc 03 §5 programs, "deleting a branch should set branch_id to
    # null"). No DDL: the ON DELETE SET NULL lives on programs.branch_id.
    programs: Mapped[list["Program"]] = relationship(
        "Program",
        back_populates="branch",
        cascade="save-update, merge",
    )

    __table_args__ = (
        check_in("status", BRANCH_STATUSES, name="status"),
        row_version_positive(),
        # doc 03 §5 asks for "unique(org_id, name)". It ships as a **partial** unique
        # index rather than a UNIQUE constraint, which is ADR-004's explicitly
        # recorded consequence of soft deletes: "a soft-deleted row still occupies
        # its unique key, so 'delete and recreate with the same name' fails unless
        # the constraint is partial (WHERE deleted_at IS NULL)". Deleting the
        # "Andheri" branch and creating a new one with that name has to work.
        #
        # Trade-off accepted: a partial index is not a UNIQUE *constraint*, so it
        # cannot be the target of a foreign key and ON CONFLICT must name the
        # columns rather than the constraint. Nothing in doc 03 needs either.
        Index(
            "uq_branches_org_id_name",
            "org_id",
            "name",
            unique=True,
            postgresql_where=text("deleted_at IS NULL"),
        ),
    )

    def __repr__(self) -> str:  # pragma: no cover - debugging aid
        return f"<Branch id={self.id!r} org_id={self.org_id!r} name={self.name!r}>"


__all__ = ["BRANCH_STATUSES", "Branch"]
