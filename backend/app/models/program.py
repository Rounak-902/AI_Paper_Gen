"""The **Programs** table group of doc 03 §4: institute-owned batches and their bridges.

``programs`` is the first tenant-owned table outside identity: it carries ``org_id``
and is therefore in scope for the RLS policies P1-T5 adds. ``program_subjects`` and
``program_teachers`` are the two many-to-many bridges named in doc 03 §3.

Why the bridges have no audit columns
-------------------------------------
``programs`` gets ``created_at``/``updated_at``/``deleted_at``/``row_version``
because doc 03 §5 lists all four. The two bridge tables get none, which is also what
doc 03 §5 lists — and here that omission is load-bearing rather than abbreviated.
Both bridges use a **composite primary key** over their two FK columns (doc 03 §5,
"Primary key: composite(...)"), and a primary key cannot be made partial. Adding
``deleted_at`` to a composite-PK bridge would trip exactly the failure mode ADR-004
warns about — "a soft-deleted row still occupies its unique key, so delete and
recreate with the same name fails" — except with no partial-index escape hatch, so
un-assigning a teacher from a program would permanently prevent re-assigning them.

That is the principle used throughout P1-T4: **soft delete applies where uniqueness
can be made partial.** It can for every surrogate-keyed table in this schema; it
cannot for these two, so they take hard deletes. Removing a row from a bridge
destroys no history — the program and the teacher both survive it.
"""

from __future__ import annotations

import uuid as _uuid
from typing import TYPE_CHECKING, Optional

from sqlalchemy import ForeignKey, Index, PrimaryKeyConstraint, Text, text
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
    from app.models.curriculum import Subject
    from app.models.organization import Organization
    from app.models.paper import GeneratedPaper
    from app.models.user import User

#: doc 03 §5 says only "status constrained to active lifecycle values" with a default
#: of ``active``, and §7.3 does not list this column at all — so the vocabulary is
#: invented here, the same way ``ORG_STATUSES`` was during P1-T3, and flagged in the
#: P1-T4 hand-off notes.
#:
#: Two values, not three: ``deleted_at`` already means "this program is gone", so the
#: only other state a batch needs is "it ran, it is over, keep it for its papers".
#: Because this is a named CHECK on a text column and never a native ENUM (ADR-011),
#: widening it later is a one-line migration.
PROGRAM_STATUSES: tuple[str, ...] = ("active", "archived")

_SOFT_DELETE_PREDICATE = "deleted_at IS NULL"


class Program(
    UUIDPrimaryKeyMixin,
    TimestampMixin,
    SoftDeleteMixin,
    RowVersionMixin,
    Base,
):
    """An institute-owned batch or program (doc 03 §5, §9 step 5)."""

    __tablename__ = "programs"

    # CASCADE per doc 03 §5, "org deletion cascades programs" — the hard-delete
    # tenant-offboarding path only; ADR-004 forbids hard deletes from app code.
    org_id: Mapped[_uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("organizations.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )
    # SET NULL per doc 03 §5, "deleting a branch should set branch_id to null":
    # losing a branch promotes the program to org-wide rather than deleting it.
    branch_id: Mapped[Optional[_uuid.UUID]] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("branches.id", ondelete="SET NULL"),
        nullable=True,
        index=True,
    )
    name: Mapped[str] = mapped_column(Text, nullable=False)
    exam_target: Mapped[Optional[str]] = mapped_column(Text, nullable=True, index=True)
    academic_year: Mapped[Optional[str]] = mapped_column(Text, nullable=True)
    status: Mapped[str] = mapped_column(
        Text,
        nullable=False,
        server_default=text("'active'"),
        index=True,
    )

    organization: Mapped["Organization"] = relationship(
        "Organization",
        back_populates="programs",
    )
    branch: Mapped[Optional["Branch"]] = relationship(
        "Branch",
        back_populates="programs",
    )
    program_subjects: Mapped[list["ProgramSubject"]] = relationship(
        "ProgramSubject",
        back_populates="program",
        # The database CASCADEs these (doc 03 §5, "program delete cascades"), and the
        # ORM matches it here — unlike everywhere else in this schema — because a
        # bridge row carries no history worth preserving and has no `deleted_at` to
        # tombstone into.
        cascade="all, delete-orphan",
    )
    program_teachers: Mapped[list["ProgramTeacher"]] = relationship(
        "ProgramTeacher",
        back_populates="program",
        cascade="all, delete-orphan",
    )
    # doc 03 §6: `programs ||--o{ generated_papers : for`. Added with migration 0010.
    # Note the cascade difference from the two bridge collections above: this one is
    # `save-update, merge`, because a paper is not a bridge row — it has its own
    # lifecycle, its own soft delete and its own approval trail, and deleting a program
    # must not reach into it. The database FK is ON DELETE SET NULL to match.
    generated_papers: Mapped[list["GeneratedPaper"]] = relationship(
        "GeneratedPaper",
        back_populates="program",
        cascade="save-update, merge",
    )

    __table_args__ = (
        check_in("status", PROGRAM_STATUSES, name="status"),
        row_version_positive(),
        # doc 03 §5: "unique(org_id, branch_id, name)".
        #
        # KNOWN GAP, flagged in the P1-T4 hand-off notes rather than silently fixed:
        # `branch_id` is nullable and PostgreSQL indexes are NULLS DISTINCT by
        # default, so this constraint does **not** stop two org-wide programs
        # (branch_id IS NULL) sharing a name. doc 03 does not address the null case.
        # The fix is one line — `postgresql_nulls_not_distinct=True` (PG15+), or a
        # second partial index on (org_id, name) WHERE branch_id IS NULL — but which
        # one is correct depends on whether an org-wide and a branch-scoped program
        # may share a name, which is a product question, not a schema one. Written
        # literally as specified; adding the stricter form later is additive, whereas
        # removing it after duplicates exist is not.
        Index(
            "uq_programs_org_id_branch_id_name",
            "org_id",
            "branch_id",
            "name",
            unique=True,
            postgresql_where=text(_SOFT_DELETE_PREDICATE),
        ),
    )

    def __repr__(self) -> str:  # pragma: no cover - debugging aid
        return (
            f"<Program id={self.id!r} org_id={self.org_id!r} name={self.name!r} "
            f"status={self.status!r}>"
        )


class ProgramSubject(Base):
    """``programs ↔ subjects`` bridge (doc 03 §3, §5).

    No surrogate id, no audit columns — see the module docstring.
    """

    __tablename__ = "program_subjects"

    program_id: Mapped[_uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("programs.id", ondelete="CASCADE"),
        nullable=False,
    )
    # RESTRICT: curriculum is shared reference data, and a subject that some batch is
    # still teaching must not vanish underneath it. doc 03 §5 states the cascade for
    # this table only as "program delete cascades", so the subject side is inferred
    # from the RESTRICT posture the curriculum group takes throughout.
    subject_id: Mapped[_uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("subjects.id", ondelete="RESTRICT"),
        nullable=False,
        index=True,
    )

    program: Mapped["Program"] = relationship(
        "Program",
        back_populates="program_subjects",
    )
    subject: Mapped["Subject"] = relationship(
        "Subject",
        back_populates="program_subjects",
    )

    __table_args__ = (
        # Named explicitly rather than left to `primary_key=True` on both columns, so
        # the constraint name comes from the naming convention (`pk_program_subjects`)
        # and the downgrade can drop it by name.
        PrimaryKeyConstraint("program_id", "subject_id", name="pk_program_subjects"),
    )

    def __repr__(self) -> str:  # pragma: no cover - debugging aid
        return (
            f"<ProgramSubject program_id={self.program_id!r} "
            f"subject_id={self.subject_id!r}>"
        )


class ProgramTeacher(Base):
    """``programs ↔ users`` bridge — which teachers work a batch (doc 03 §3, §5).

    Note what this table is *not*: it is not an authorization record. Authority comes
    from ``memberships.role``; this narrows a teacher's assignment (doc 03 §7.2,
    "one organization, optionally narrowed to assigned programs and subjects").
    """

    __tablename__ = "program_teachers"

    program_id: Mapped[_uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("programs.id", ondelete="CASCADE"),
        nullable=False,
    )
    # CASCADE: a hard-deleted user leaves no assignment behind. This mirrors
    # `memberships.user_id`, which doc 03 §5 CASCADEs for the same reason.
    user_id: Mapped[_uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("users.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )

    program: Mapped["Program"] = relationship(
        "Program",
        back_populates="program_teachers",
    )
    user: Mapped["User"] = relationship("User", back_populates="program_teachers")

    __table_args__ = (
        PrimaryKeyConstraint("program_id", "user_id", name="pk_program_teachers"),
    )

    def __repr__(self) -> str:  # pragma: no cover - debugging aid
        return (
            f"<ProgramTeacher program_id={self.program_id!r} user_id={self.user_id!r}>"
        )


__all__ = [
    "PROGRAM_STATUSES",
    "Program",
    "ProgramSubject",
    "ProgramTeacher",
]
