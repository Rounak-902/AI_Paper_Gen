"""create curriculum tables (doc 03 §9 step 3)

Revision ID: 0003
Revises: 0002
Create Date: 2026-08-17

doc 03 §9 step 3: "Create boards, classes, subjects, chapters, learning_outcomes."
Five tables, no more — ``curriculum_versions`` is step 4 and arrives in revision 0004.

Every literal in this file is frozen on purpose. It would be tidier to import
``CURRICULUM_VERSION_STATUSES`` or the mixin definitions from ``app.models``, and it
would be wrong: a migration is a snapshot of the schema at a point in history, and
importing a live constant means editing a model silently rewrites what an
already-applied revision claims to have done. Alembic's own autogenerate inlines
these for the same reason.

Deliberate deviations from a naive reading of doc 03 §5
------------------------------------------------------

* **The four audit columns are added to all five tables.** doc 03 §5 lists only
  ``id``, the parent FK and the payload columns for these tables — no ``created_at``,
  ``updated_at``, ``deleted_at`` or ``row_version``. That is read as abbreviation
  rather than decision, on three grounds: ADR-004 states every business entity
  carries ``deleted_at``; ``curriculum_versions``, in this same doc 03 §4 table
  group, does list all four; and doc 03 §5's own cascade rule for
  ``learning_outcomes`` reads "restrict deletion **or soft delete** with the parent
  chapter", which presupposes a ``deleted_at`` its column list never declares.
  ``IMPLEMENTATION_PLAN.md`` §7 settles the tie: where a phase document and an ADR
  disagree, the ADR wins. Reported in the P1-T4 hand-off notes as the single largest
  deviation in this task.

* **Uniqueness is therefore partial.** Consequence of the above, and ADR-004's own
  recorded one: "a soft-deleted row still occupies its unique key, so 'delete and
  recreate with the same name' fails unless the constraint is partial". So
  ``unique(board_id, name)``, ``unique(class_id, name)``, ``unique(subject_id, seq)``
  and ``code unique`` all ship as unique *indexes* with ``WHERE deleted_at IS NULL``.

* **``subjects.curriculum_version_id`` and ``chapters.curriculum_version_id`` have no
  FK yet.** Both are documented in doc 03 §5 as nullable references to
  ``curriculum_versions.id``, and ``curriculum_versions`` is §9 step 4 — it does not
  exist while this revision runs. The columns and their indexes ship now; revision
  0004 adds the two constraints, which is exactly what step 4's "and **wire version
  relations**" asks for. Same expand-migrate-contract split (doc 03 §8) that 0002
  used for ``organizations.subscription_id``, closed one revision later instead of
  one task later.

* **``boards.code`` gets one index, not two.** doc 03 §5 lists "code unique" under
  Constraints and "code" again under Indexes. In PostgreSQL those are one object: a
  unique index on ``code`` *is* an index on ``code``. A second non-unique index on
  the same single column would be maintained on every write and chosen by the planner
  never.

Not done here, on purpose
-------------------------
No ``CHECK (seq > 0)`` on ``chapters.seq``. doc 03 states no such invariant for it
anywhere, and unlike ``row_version`` there is no statement elsewhere in the document
set to generalize from. Listed as an open item rather than invented.
"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

# revision identifiers, used by Alembic.
revision: str = "0003"
down_revision: Union[str, Sequence[str], None] = "0002"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None

_SOFT_DELETE_PREDICATE = "deleted_at IS NULL"


def upgrade() -> None:
    """Upgrade schema."""

    # ------------------------------------------------------------------
    # boards — global board reference data (doc 03 §5)
    # ------------------------------------------------------------------
    op.create_table(
        "boards",
        sa.Column(
            "id",
            postgresql.UUID(as_uuid=True),
            server_default=sa.text("gen_random_uuid()"),
            nullable=False,
        ),
        sa.Column("code", sa.Text(), nullable=False),
        sa.Column("name", sa.Text(), nullable=False),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.Column(
            "updated_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.Column("deleted_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("row_version", sa.Integer(), server_default=sa.text("1"), nullable=False),
        sa.CheckConstraint("row_version > 0", name=op.f("ck_boards_row_version_positive")),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_boards")),
    )
    # doc 03 §5 "code unique" and "Indexes: code" — one partial unique index serves
    # both; see the module docstring.
    op.create_index(
        "uq_boards_code",
        "boards",
        ["code"],
        unique=True,
        postgresql_where=sa.text(_SOFT_DELETE_PREDICATE),
    )

    # ------------------------------------------------------------------
    # classes — board-specific grade definition (doc 03 §5)
    # ------------------------------------------------------------------
    op.create_table(
        "classes",
        sa.Column(
            "id",
            postgresql.UUID(as_uuid=True),
            server_default=sa.text("gen_random_uuid()"),
            nullable=False,
        ),
        sa.Column("board_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("name", sa.Text(), nullable=False),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.Column(
            "updated_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.Column("deleted_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("row_version", sa.Integer(), server_default=sa.text("1"), nullable=False),
        sa.CheckConstraint("row_version > 0", name=op.f("ck_classes_row_version_positive")),
        # doc 03 §5 boards: "restrict deletion when curriculum records exist".
        # Curriculum is global reference data with no tenant owner, so no offboarding
        # path should ever take a board and its grades with it.
        sa.ForeignKeyConstraint(
            ["board_id"],
            ["boards.id"],
            name=op.f("fk_classes_board_id_boards"),
            ondelete="RESTRICT",
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_classes")),
    )
    op.create_index(op.f("ix_classes_board_id"), "classes", ["board_id"])
    op.create_index(
        "uq_classes_board_id_name",
        "classes",
        ["board_id", "name"],
        unique=True,
        postgresql_where=sa.text(_SOFT_DELETE_PREDICATE),
    )

    # ------------------------------------------------------------------
    # subjects — subject definition (doc 03 §5)
    #
    # curriculum_version_id ships here as a bare column; revision 0004 adds the
    # REFERENCES clause once curriculum_versions exists.
    # ------------------------------------------------------------------
    op.create_table(
        "subjects",
        sa.Column(
            "id",
            postgresql.UUID(as_uuid=True),
            server_default=sa.text("gen_random_uuid()"),
            nullable=False,
        ),
        sa.Column("class_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("curriculum_version_id", postgresql.UUID(as_uuid=True), nullable=True),
        sa.Column("name", sa.Text(), nullable=False),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.Column(
            "updated_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.Column("deleted_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("row_version", sa.Integer(), server_default=sa.text("1"), nullable=False),
        sa.CheckConstraint("row_version > 0", name=op.f("ck_subjects_row_version_positive")),
        sa.ForeignKeyConstraint(
            ["class_id"],
            ["classes.id"],
            name=op.f("fk_subjects_class_id_classes"),
            ondelete="RESTRICT",
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_subjects")),
    )
    op.create_index(op.f("ix_subjects_class_id"), "subjects", ["class_id"])
    # The index does not need the target table to exist — only the FK does.
    op.create_index(
        op.f("ix_subjects_curriculum_version_id"),
        "subjects",
        ["curriculum_version_id"],
    )
    op.create_index(
        "uq_subjects_class_id_name",
        "subjects",
        ["class_id", "name"],
        unique=True,
        postgresql_where=sa.text(_SOFT_DELETE_PREDICATE),
    )

    # ------------------------------------------------------------------
    # chapters — syllabus node and retrieval anchor (doc 03 §5)
    # ------------------------------------------------------------------
    op.create_table(
        "chapters",
        sa.Column(
            "id",
            postgresql.UUID(as_uuid=True),
            server_default=sa.text("gen_random_uuid()"),
            nullable=False,
        ),
        sa.Column("subject_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("curriculum_version_id", postgresql.UUID(as_uuid=True), nullable=True),
        sa.Column("seq", sa.Integer(), nullable=False),
        sa.Column("title", sa.Text(), nullable=False),
        sa.Column("derived_from_chapter_id", postgresql.UUID(as_uuid=True), nullable=True),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.Column(
            "updated_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.Column("deleted_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("row_version", sa.Integer(), server_default=sa.text("1"), nullable=False),
        sa.CheckConstraint("row_version > 0", name=op.f("ck_chapters_row_version_positive")),
        # doc 03 §3 "Self-referencing: chapters.derived_from_chapter_id". doc 03 §5
        # does not name an action for it; RESTRICT is chosen because this column *is*
        # the provenance record, and SET NULL would keep the derived chapter while
        # erasing where it came from. Flagged.
        sa.ForeignKeyConstraint(
            ["derived_from_chapter_id"],
            ["chapters.id"],
            name=op.f("fk_chapters_derived_from_chapter_id_chapters"),
            ondelete="RESTRICT",
        ),
        sa.ForeignKeyConstraint(
            ["subject_id"],
            ["subjects.id"],
            name=op.f("fk_chapters_subject_id_subjects"),
            ondelete="RESTRICT",
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_chapters")),
    )
    op.create_index(
        op.f("ix_chapters_curriculum_version_id"),
        "chapters",
        ["curriculum_version_id"],
    )
    # doc 03 §5: "composite(curriculum_version_id, subject_id, seq)". Not redundant
    # with uq_chapters_subject_id_seq below: this one leads with the version, so
    # "chapter N of this subject in this syllabus year" needs no filter step.
    op.create_index(
        op.f("ix_chapters_curriculum_version_id_subject_id_seq"),
        "chapters",
        ["curriculum_version_id", "subject_id", "seq"],
    )
    op.create_index(
        op.f("ix_chapters_derived_from_chapter_id"),
        "chapters",
        ["derived_from_chapter_id"],
    )
    op.create_index(op.f("ix_chapters_subject_id"), "chapters", ["subject_id"])
    op.create_index(
        "uq_chapters_subject_id_seq",
        "chapters",
        ["subject_id", "seq"],
        unique=True,
        postgresql_where=sa.text(_SOFT_DELETE_PREDICATE),
    )

    # ------------------------------------------------------------------
    # learning_outcomes — chapter-level objectives (doc 03 §5)
    # ------------------------------------------------------------------
    op.create_table(
        "learning_outcomes",
        sa.Column(
            "id",
            postgresql.UUID(as_uuid=True),
            server_default=sa.text("gen_random_uuid()"),
            nullable=False,
        ),
        sa.Column("chapter_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("code", sa.Text(), nullable=True),
        sa.Column("description", sa.Text(), nullable=False),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.Column(
            "updated_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.Column("deleted_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("row_version", sa.Integer(), server_default=sa.text("1"), nullable=False),
        sa.CheckConstraint(
            "row_version > 0",
            name=op.f("ck_learning_outcomes_row_version_positive"),
        ),
        # doc 03 §5 chapters: "restrict deletion if outcomes, questions, or textbook
        # assets still reference the chapter".
        sa.ForeignKeyConstraint(
            ["chapter_id"],
            ["chapters.id"],
            name=op.f("fk_learning_outcomes_chapter_id_chapters"),
            ondelete="RESTRICT",
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_learning_outcomes")),
    )
    op.create_index(
        op.f("ix_learning_outcomes_chapter_id"),
        "learning_outcomes",
        ["chapter_id"],
    )
    # doc 03 §5 hedges — "**optional** unique(chapter_id, code) if codes are
    # consistently populated". Created: PostgreSQL's default NULLS DISTINCT is exactly
    # what makes the hedge unnecessary, since rows with a NULL code never collide, so
    # the constraint binds only where codes are populated and costs nothing where they
    # are not.
    op.create_index(
        "uq_learning_outcomes_chapter_id_code",
        "learning_outcomes",
        ["chapter_id", "code"],
        unique=True,
        postgresql_where=sa.text(_SOFT_DELETE_PREDICATE),
    )


def downgrade() -> None:
    """Downgrade schema.

    Fully reversible: all five tables are created from nothing here, so dropping them
    restores revision 0002's state exactly. Dropped in reverse dependency order so no
    foreign key blocks a drop. The explicit ``drop_index`` calls are redundant —
    ``DROP TABLE`` takes its indexes with it — but they mirror what autogenerate emits
    and make the reversal auditable line by line.
    """

    op.drop_index("uq_learning_outcomes_chapter_id_code", table_name="learning_outcomes")
    op.drop_index(op.f("ix_learning_outcomes_chapter_id"), table_name="learning_outcomes")
    op.drop_table("learning_outcomes")

    op.drop_index("uq_chapters_subject_id_seq", table_name="chapters")
    op.drop_index(op.f("ix_chapters_subject_id"), table_name="chapters")
    op.drop_index(op.f("ix_chapters_derived_from_chapter_id"), table_name="chapters")
    op.drop_index(
        op.f("ix_chapters_curriculum_version_id_subject_id_seq"),
        table_name="chapters",
    )
    op.drop_index(op.f("ix_chapters_curriculum_version_id"), table_name="chapters")
    op.drop_table("chapters")

    op.drop_index("uq_subjects_class_id_name", table_name="subjects")
    op.drop_index(op.f("ix_subjects_curriculum_version_id"), table_name="subjects")
    op.drop_index(op.f("ix_subjects_class_id"), table_name="subjects")
    op.drop_table("subjects")

    op.drop_index("uq_classes_board_id_name", table_name="classes")
    op.drop_index(op.f("ix_classes_board_id"), table_name="classes")
    op.drop_table("classes")

    op.drop_index("uq_boards_code", table_name="boards")
    op.drop_table("boards")
