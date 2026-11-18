"""``users`` — the identity record (doc 03 §5, "users").

A ``users`` row is global, not tenant-scoped: one person may hold memberships in
several organizations (a teacher tutoring for two coaching centres, a platform
operator). Authority over *what* they may do lives entirely on ``memberships``.
"""

from __future__ import annotations

from typing import TYPE_CHECKING, Optional

from sqlalchemy import Index, Text, text
from sqlalchemy.dialects.postgresql import CITEXT
from sqlalchemy.orm import Mapped, mapped_column, relationship, synonym

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
    from app.models.membership import Membership
    from app.models.notification import Notification
    from app.models.paper import GeneratedPaper
    from app.models.program import ProgramTeacher
    from app.models.question import QuestionBank
    from app.models.template import Template

#: doc 03 §5 and §7.1 both enumerate these, and ADR-012 §2 explains why the column
#: exists at all: FR-1.6 and AC-1 need a way to disable an identity *without*
#: deleting it, because the row is referenced by authored questions, papers, and
#: approvals.
USER_STATUSES: tuple[str, ...] = ("active", "disabled", "suspended")


class User(
    UUIDPrimaryKeyMixin,
    TimestampMixin,
    SoftDeleteMixin,
    RowVersionMixin,
    Base,
):
    """Identity record.

    Note what is deliberately absent: **there is no ``role`` column here.** doc 03
    §5, §7.1, §7.2 and ADR-011 all place ``role`` on ``memberships``, because a role
    is held *per organization* — the same person can be a ``teacher`` in one org and
    a ``coaching_admin`` in another. The three-value CHECK from ADR-011 therefore
    lives in ``app/models/membership.py``.
    """

    __tablename__ = "users"

    # citext, per doc 03 §5 — case-insensitive comparison in the database rather
    # than a lower() call every caller has to remember. Requires the `citext`
    # extension, which doc 03 §9 step 1 enables (migration 0001).
    email: Mapped[str] = mapped_column(CITEXT, nullable=False)
    # Nullable: null for SSO-only accounts (doc 03 §5).
    password_hash: Mapped[Optional[str]] = mapped_column(Text, nullable=True)
    full_name: Mapped[str] = mapped_column(Text, nullable=False)
    status: Mapped[str] = mapped_column(
        Text,
        nullable=False,
        server_default=text("'active'"),
        index=True,
    )

    # doc 03 §7.1 and ADR-012 §1 both say: column `full_name`, "ORM attribute
    # `fullName`". The column name is settled and implemented above. The camelCase
    # attribute is a holdover from the Prisma prototype that ADR-012 was written
    # against; ADR-006 has since replaced Prisma with SQLAlchemy, where PEP 8 makes
    # `full_name` the natural attribute and `fullName` the odd one out.
    #
    # Rather than pick a side silently, both work: `full_name` is canonical for
    # application code, and this synonym makes the documented `fullName` a real,
    # queryable attribute (`select(User).where(User.fullName == ...)`) at zero
    # schema cost. Flagged in the P1-T3 hand-off notes; camelCase properly belongs
    # at the Pydantic serialization boundary (P1-T6), not in the ORM.
    fullName = synonym("full_name")

    memberships: Mapped[list["Membership"]] = relationship(
        "Membership",
        back_populates="user",
        cascade="save-update, merge",
    )
    # Added in P1-T4, all DDL-neutral. Three distinct kinds of link:
    #
    #   program_teachers — assignment (doc 03 §3, programs ↔ users). The bridge row is
    #       CASCADEd away with the user, because an assignment to a deleted person
    #       means nothing.
    #   authored_*       — authorship on *mutable* tables. Those FKs are ON DELETE SET
    #       NULL: the content outlives its author, which is exactly why ADR-012 §2 added
    #       `users.status` instead of expecting accounts to be deleted. The one exception
    #       is `authored_papers`, whose FK is RESTRICT because doc 03 §5 makes
    #       `generated_papers.created_by` required — SET NULL is not available on a NOT
    #       NULL column.
    #   attribution      — `usage.user_id`, `audit_logs.actor_id`,
    #       `approvals.reviewer_id`, `prompt_versions.author`. All RESTRICT, because
    #       those tables are append-only and SET NULL would mean UPDATEing a row that is
    #       not allowed to change. Left unmapped from this side on purpose: doc 03 §6
    #       draws no `users ||--o{` edge to any of them, and a convenience collection
    #       over an audit trail invites exactly the kind of ORM-level cascade ADR-004
    #       forbids.
    #
    # The collections below are therefore precisely doc 03 §6's six `users ||--o{ X`
    # edges, no more.
    program_teachers: Mapped[list["ProgramTeacher"]] = relationship(
        "ProgramTeacher",
        back_populates="user",
        cascade="save-update, merge",
    )
    authored_questions: Mapped[list["QuestionBank"]] = relationship(
        "QuestionBank",
        back_populates="author",
        cascade="save-update, merge",
    )
    authored_templates: Mapped[list["Template"]] = relationship(
        "Template",
        back_populates="author",
        cascade="save-update, merge",
    )
    authored_papers: Mapped[list["GeneratedPaper"]] = relationship(
        "GeneratedPaper",
        back_populates="author",
        cascade="save-update, merge",
    )
    notifications: Mapped[list["Notification"]] = relationship(
        "Notification",
        back_populates="user",
        cascade="save-update, merge",
    )

    __table_args__ = (
        check_in("status", USER_STATUSES, name="status"),
        row_version_positive(),
        # doc 03 §5 asks for "email unique globally"; ADR-004 requires the
        # uniqueness to be partial so that a soft-deleted account does not hold its
        # address hostage forever. Two soft-deleted rows may share an address; at
        # most one live row may hold it.
        Index(
            "uq_users_email",
            "email",
            unique=True,
            postgresql_where=text("deleted_at IS NULL"),
        ),
    )

    def __repr__(self) -> str:  # pragma: no cover - debugging aid
        # Deliberately no email and no password_hash: __repr__ output ends up in
        # logs and tracebacks.
        return f"<User id={self.id!r} status={self.status!r}>"


__all__ = ["USER_STATUSES", "User"]
