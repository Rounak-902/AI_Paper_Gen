"""The **Curriculum** table group of doc 03 §4: the syllabus spine.

``boards`` → ``classes`` → ``subjects`` → ``chapters`` → ``learning_outcomes``,
with ``curriculum_versions`` cutting across ``subjects`` and ``chapters`` as the
version boundary (doc 03 §3, "curriculum_versions → subjects / → chapters").

Why one module for six tables
-----------------------------
P1-T3 used one module per table, which worked because the four tenancy tables form
a simple chain. These six are mutually referential — ``chapters`` self-references,
and both ``subjects`` and ``chapters`` point at ``curriculum_versions`` while
``curriculum_versions`` points back at ``boards`` — so splitting them buys six
``TYPE_CHECKING`` blocks and a harder-to-follow read. doc 03 §4 already names this
exact set as one table group, and it maps 1:1 onto migrations 0003 and 0004. This is
a code-organization choice only; it changes no DDL.

Two deliberate deviations from doc 03 §5, both flagged in the P1-T4 hand-off notes
-----------------------------------------------------------------------------------
1. **Audit columns.** doc 03 §5 lists ``id``/``name``-style columns *only* for
   ``boards``, ``classes``, ``subjects``, ``chapters`` and ``learning_outcomes`` —
   no ``created_at``, ``updated_at``, ``deleted_at`` or ``row_version``. That reads
   as an abbreviation rather than a decision, because (a) ADR-004 states every
   business entity carries ``deleted_at``, (b) ``curriculum_versions`` in this very
   table group *does* list all four, and (c) doc 03 §5's own cascade rule for
   ``learning_outcomes`` is "restrict deletion **or soft delete** with the parent
   chapter", which presupposes a ``deleted_at`` the column list never declares.
   The four columns are therefore applied uniformly here. ADR-004 wins over §5's
   prose per ``IMPLEMENTATION_PLAN.md`` §7.
2. **Uniqueness is partial.** Consequence of (1), and ADR-004's own recorded one: a
   soft-deleted row still occupies its unique key, so every uniqueness rule below is
   a partial unique index ``WHERE deleted_at IS NULL`` rather than a plain UNIQUE.

Not created here (doc 03 §9 step 18, migration 0018): the GIN, trigram and generated
``tsvector`` search artifacts. Nothing in this group needs them.
"""

from __future__ import annotations

import datetime as _dt
import uuid as _uuid
from typing import TYPE_CHECKING, Optional

from sqlalchemy import CheckConstraint, Date, ForeignKey, Index, Integer, Text, text
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
    from app.models.paper import GeneratedPaper
    from app.models.program import ProgramSubject
    from app.models.question import QuestionBank
    from app.models.textbook import Textbook, TextbookAsset

#: doc 03 §5 ``curriculum_versions``: "status constrained to draft/active/superseded".
#: The only curriculum vocabulary doc 03 enumerates outright.
CURRICULUM_VERSION_STATUSES: tuple[str, ...] = ("draft", "active", "superseded")

#: ``deleted_at IS NULL`` — the predicate on every partial unique index in this
#: module. Declared once so the model and migration 0003 cannot drift on the exact
#: text, which is what PostgreSQL stores and what the verify script asserts on.
_SOFT_DELETE_PREDICATE = "deleted_at IS NULL"


class Board(
    UUIDPrimaryKeyMixin,
    TimestampMixin,
    SoftDeleteMixin,
    RowVersionMixin,
    Base,
):
    """Global board reference data — CBSE, ICSE, a state board (doc 03 §5)."""

    __tablename__ = "boards"

    code: Mapped[str] = mapped_column(Text, nullable=False)
    name: Mapped[str] = mapped_column(Text, nullable=False)

    classes: Mapped[list["Class"]] = relationship(
        "Class",
        back_populates="board",
        cascade="save-update, merge",
    )
    curriculum_versions: Mapped[list["CurriculumVersion"]] = relationship(
        "CurriculumVersion",
        back_populates="board",
        cascade="save-update, merge",
    )

    __table_args__ = (
        row_version_positive(),
        # doc 03 §5 lists "code unique" under Constraints *and* "code" again under
        # Indexes. Those are one object, not two: a unique index on `code` is an
        # index on `code`. A second, non-unique index on the same single column
        # would be dead weight PostgreSQL still has to maintain on every write.
        Index(
            "uq_boards_code",
            "code",
            unique=True,
            postgresql_where=text(_SOFT_DELETE_PREDICATE),
        ),
    )

    def __repr__(self) -> str:  # pragma: no cover - debugging aid
        return f"<Board id={self.id!r} code={self.code!r}>"


class Class(
    UUIDPrimaryKeyMixin,
    TimestampMixin,
    SoftDeleteMixin,
    RowVersionMixin,
    Base,
):
    """A board-specific grade level (doc 03 §5, model name ``Class`` per §8)."""

    __tablename__ = "classes"

    # RESTRICT, from doc 03 §5 boards: "restrict deletion when curriculum records
    # exist". Curriculum is global reference data with no tenant owner, so there is
    # no offboarding path that should ever take a board and its grades with it.
    board_id: Mapped[_uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("boards.id", ondelete="RESTRICT"),
        nullable=False,
        index=True,
    )
    name: Mapped[str] = mapped_column(Text, nullable=False)

    board: Mapped["Board"] = relationship("Board", back_populates="classes")
    subjects: Mapped[list["Subject"]] = relationship(
        "Subject",
        back_populates="school_class",
        cascade="save-update, merge",
    )

    __table_args__ = (
        row_version_positive(),
        Index(
            "uq_classes_board_id_name",
            "board_id",
            "name",
            unique=True,
            postgresql_where=text(_SOFT_DELETE_PREDICATE),
        ),
    )

    def __repr__(self) -> str:  # pragma: no cover - debugging aid
        return f"<Class id={self.id!r} board_id={self.board_id!r} name={self.name!r}>"


class Subject(
    UUIDPrimaryKeyMixin,
    TimestampMixin,
    SoftDeleteMixin,
    RowVersionMixin,
    Base,
):
    """A subject within one grade of one board (doc 03 §5)."""

    __tablename__ = "subjects"

    class_id: Mapped[_uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("classes.id", ondelete="RESTRICT"),
        nullable=False,
        index=True,
    )
    # Nullable per doc 03 §5: a subject may predate any versioned syllabus.
    #
    # The FK is real in the final schema but arrives one revision late. `subjects`
    # is doc 03 §9 step 3 and `curriculum_versions` is step 4, so migration 0003
    # creates this column bare and 0004 adds the REFERENCES clause ("wire version
    # relations"). Same expand-migrate-contract split 0002 used for
    # `organizations.subscription_id` — the difference is that this one is closed
    # inside P1-T4 rather than handed to a later task, so the constraint *is*
    # declared here and `alembic check` at head sees it.
    curriculum_version_id: Mapped[Optional[_uuid.UUID]] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("curriculum_versions.id", ondelete="RESTRICT"),
        nullable=True,
        index=True,
    )
    name: Mapped[str] = mapped_column(Text, nullable=False)

    school_class: Mapped["Class"] = relationship("Class", back_populates="subjects")
    curriculum_version: Mapped[Optional["CurriculumVersion"]] = relationship(
        "CurriculumVersion",
        back_populates="subjects",
    )
    chapters: Mapped[list["Chapter"]] = relationship(
        "Chapter",
        back_populates="subject",
        cascade="save-update, merge",
    )
    program_subjects: Mapped[list["ProgramSubject"]] = relationship(
        "ProgramSubject",
        back_populates="subject",
        cascade="save-update, merge",
    )
    textbooks: Mapped[list["Textbook"]] = relationship(
        "Textbook",
        back_populates="subject",
        cascade="save-update, merge",
    )

    __table_args__ = (
        row_version_positive(),
        # A subject is a row inside one syllabus snapshot (the design source calls
        # this "subject-syllabus granularity").  The original (class_id, name)
        # uniqueness made a second annual snapshot of "Mathematics" impossible.
        # Keep legacy/unversioned rows unique in their own partial index, because a
        # nullable curriculum_version_id in one composite index would otherwise use
        # PostgreSQL's NULLS DISTINCT semantics and admit duplicates.
        Index(
            "uq_subjects_class_id_curriculum_version_id_name",
            "class_id",
            "curriculum_version_id",
            "name",
            unique=True,
            postgresql_where=text(
                f"{_SOFT_DELETE_PREDICATE} AND curriculum_version_id IS NOT NULL"
            ),
        ),
        Index(
            "uq_subjects_class_id_name_unversioned",
            "class_id",
            "name",
            unique=True,
            postgresql_where=text(
                f"{_SOFT_DELETE_PREDICATE} AND curriculum_version_id IS NULL"
            ),
        ),
    )

    def __repr__(self) -> str:  # pragma: no cover - debugging aid
        return f"<Subject id={self.id!r} class_id={self.class_id!r} name={self.name!r}>"


class Chapter(
    UUIDPrimaryKeyMixin,
    TimestampMixin,
    SoftDeleteMixin,
    RowVersionMixin,
    Base,
):
    """Syllabus node and **retrieval anchor** (doc 03 §5).

    This is the table the whole generation pipeline filters on, which is why doc 03
    asks for the composite ``(curriculum_version_id, subject_id, seq)`` index below
    on top of the individual ones.
    """

    __tablename__ = "chapters"

    subject_id: Mapped[_uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("subjects.id", ondelete="RESTRICT"),
        nullable=False,
        index=True,
    )
    # See the note on Subject.curriculum_version_id — column in 0003, FK in 0004.
    curriculum_version_id: Mapped[Optional[_uuid.UUID]] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("curriculum_versions.id", ondelete="RESTRICT"),
        nullable=True,
        index=True,
    )
    # doc 03 §5 gives no positivity constraint for `seq`, and unlike `row_version`
    # there is no statement of the invariant anywhere else in the document set to
    # generalize from. Left unconstrained rather than invented; flagged in the
    # P1-T4 hand-off notes as an open item.
    seq: Mapped[int] = mapped_column(Integer, nullable=False)
    title: Mapped[str] = mapped_column(Text, nullable=False)
    # doc 03 §3 "Self-referencing". RESTRICT rather than SET NULL: this column is
    # the record of where a derived chapter came from, and silently blanking it on a
    # hard delete would erase the provenance while leaving the row that depends on
    # it. doc 03 §5 does not specify the action — flagged.
    derived_from_chapter_id: Mapped[Optional[_uuid.UUID]] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("chapters.id", ondelete="RESTRICT"),
        nullable=True,
        index=True,
    )

    subject: Mapped["Subject"] = relationship("Subject", back_populates="chapters")
    curriculum_version: Mapped[Optional["CurriculumVersion"]] = relationship(
        "CurriculumVersion",
        back_populates="chapters",
    )
    derived_from: Mapped[Optional["Chapter"]] = relationship(
        "Chapter",
        # remote_side names the "one" end of a self-referential relationship;
        # without it SQLAlchemy cannot tell which side is the parent (doc 03 §8,
        # "keep question lineage as self-referential relationships (remote_side)" —
        # the same mechanism applies here).
        remote_side=lambda: [Chapter.id],
        back_populates="derived_chapters",
    )
    derived_chapters: Mapped[list["Chapter"]] = relationship(
        "Chapter",
        back_populates="derived_from",
        cascade="save-update, merge",
    )
    learning_outcomes: Mapped[list["LearningOutcome"]] = relationship(
        "LearningOutcome",
        back_populates="chapter",
        cascade="save-update, merge",
    )
    textbook_assets: Mapped[list["TextbookAsset"]] = relationship(
        "TextbookAsset",
        back_populates="chapter",
        cascade="save-update, merge",
    )
    questions: Mapped[list["QuestionBank"]] = relationship(
        "QuestionBank",
        back_populates="chapter",
        cascade="save-update, merge",
    )

    __table_args__ = (
        row_version_positive(),
        CheckConstraint(
            "derived_from_chapter_id IS NULL OR derived_from_chapter_id <> id",
            name="derived_from_not_self",
        ),
        Index(
            "uq_chapters_subject_id_seq",
            "subject_id",
            "seq",
            unique=True,
            postgresql_where=text(_SOFT_DELETE_PREDICATE),
        ),
        # doc 03 §5: "composite(curriculum_version_id, subject_id, seq)". Not
        # redundant with the unique index above: this one leads with the version, so
        # it serves "give me chapter N of this subject *in this syllabus year*"
        # without a filter step on the version.
        Index(
            "ix_chapters_curriculum_version_id_subject_id_seq",
            "curriculum_version_id",
            "subject_id",
            "seq",
        ),
    )

    def __repr__(self) -> str:  # pragma: no cover - debugging aid
        return f"<Chapter id={self.id!r} subject_id={self.subject_id!r} seq={self.seq!r}>"


class LearningOutcome(
    UUIDPrimaryKeyMixin,
    TimestampMixin,
    SoftDeleteMixin,
    RowVersionMixin,
    Base,
):
    """Chapter-level learning objective (doc 03 §5)."""

    __tablename__ = "learning_outcomes"

    chapter_id: Mapped[_uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("chapters.id", ondelete="RESTRICT"),
        nullable=False,
        index=True,
    )
    code: Mapped[Optional[str]] = mapped_column(Text, nullable=True)
    description: Mapped[str] = mapped_column(Text, nullable=False)

    chapter: Mapped["Chapter"] = relationship(
        "Chapter",
        back_populates="learning_outcomes",
    )

    __table_args__ = (
        row_version_positive(),
        # doc 03 §5 hedges: "**optional** unique(chapter_id, code) if codes are
        # consistently populated". It is created, and PostgreSQL's default
        # NULLS DISTINCT is exactly what makes the hedge unnecessary: rows with a
        # NULL `code` never collide with each other, so the constraint binds only
        # where codes *are* populated and costs nothing where they are not.
        Index(
            "uq_learning_outcomes_chapter_id_code",
            "chapter_id",
            "code",
            unique=True,
            postgresql_where=text(_SOFT_DELETE_PREDICATE),
        ),
    )

    def __repr__(self) -> str:  # pragma: no cover - debugging aid
        return (
            f"<LearningOutcome id={self.id!r} chapter_id={self.chapter_id!r} "
            f"code={self.code!r}>"
        )


class CurriculumVersion(
    UUIDPrimaryKeyMixin,
    TimestampMixin,
    SoftDeleteMixin,
    RowVersionMixin,
    Base,
):
    """Syllabus version boundary (doc 03 §5, §9 step 4).

    The point of this table is that a syllabus revision must not silently rewrite
    history: papers generated against the 2024-25 syllabus keep pointing at the
    chapters that existed then. ``status`` moves ``draft`` → ``active`` →
    ``superseded``; nothing is deleted.
    """

    __tablename__ = "curriculum_versions"

    board_id: Mapped[_uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("boards.id", ondelete="RESTRICT"),
        nullable=False,
        index=True,
    )
    academic_year: Mapped[str] = mapped_column(Text, nullable=False)
    label: Mapped[Optional[str]] = mapped_column(Text, nullable=True)
    status: Mapped[str] = mapped_column(
        Text,
        nullable=False,
        server_default=text("'draft'"),
        index=True,
    )
    effective_from: Mapped[Optional[_dt.date]] = mapped_column(
        Date,
        nullable=True,
        index=True,
    )
    source_ref: Mapped[Optional[str]] = mapped_column(Text, nullable=True)

    board: Mapped["Board"] = relationship(
        "Board",
        back_populates="curriculum_versions",
    )
    subjects: Mapped[list["Subject"]] = relationship(
        "Subject",
        back_populates="curriculum_version",
        cascade="save-update, merge",
    )
    chapters: Mapped[list["Chapter"]] = relationship(
        "Chapter",
        back_populates="curriculum_version",
        cascade="save-update, merge",
    )
    # doc 03 §6: `curriculum_versions ||--o{ generated_papers : built_against`. Added
    # with migration 0010. This is the edge that makes "which papers were built against
    # the 2025-26 syllabus" answerable after that syllabus has been superseded — the
    # reason `curriculum_versions` exists at all (ADR-002).
    generated_papers: Mapped[list["GeneratedPaper"]] = relationship(
        "GeneratedPaper",
        back_populates="curriculum_version",
        cascade="save-update, merge",
    )

    __table_args__ = (
        check_in("status", CURRICULUM_VERSION_STATUSES, name="status"),
        row_version_positive(),
        Index(
            "uq_curriculum_versions_board_id_academic_year",
            "board_id",
            "academic_year",
            unique=True,
            postgresql_where=text(_SOFT_DELETE_PREDICATE),
        ),
        # There is one canonical published syllabus per board. Programs may
        # continue pinning superseded versions explicitly, but an unqualified
        # active lookup must never return two competing snapshots.
        Index(
            "uq_curriculum_versions_one_active_per_board",
            "board_id",
            unique=True,
            postgresql_where=text("status = 'active' AND deleted_at IS NULL"),
        ),
    )

    def __repr__(self) -> str:  # pragma: no cover - debugging aid
        return (
            f"<CurriculumVersion id={self.id!r} board_id={self.board_id!r} "
            f"academic_year={self.academic_year!r} status={self.status!r}>"
        )


# Re-exported for the migrations and tests that need the exact predicate text.
__all__ = [
    "CURRICULUM_VERSION_STATUSES",
    "Board",
    "Chapter",
    "Class",
    "CurriculumVersion",
    "LearningOutcome",
    "Subject",
]
