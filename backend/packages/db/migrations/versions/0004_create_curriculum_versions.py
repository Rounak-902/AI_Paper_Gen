"""create curriculum_versions and wire version relations (doc 03 §9 step 4)

Revision ID: 0004
Revises: 0003
Create Date: 2026-08-17

doc 03 §9 step 4: "Create curriculum_versions **and wire version relations**." Both
halves are in this revision — the table, then the two foreign keys that revision 0003
could not add because their target did not exist yet.

That split is the contract step of expand-migrate-contract (doc 03 §8):

    0003  ALTER-free:  subjects.curriculum_version_id  UUID NULL   (+ index)
                       chapters.curriculum_version_id  UUID NULL   (+ index)
    0004  contract:    both columns gain REFERENCES curriculum_versions(id)

Deploying 0003 alone leaves a working application; deploying 0004 tightens it. The
same pattern 0002 used for ``organizations.subscription_id``, except that one is
closed by a later *task* while this one closes one revision later.

Every literal is frozen on purpose — see revision 0003's docstring for why a
migration never imports a live model constant.

Notes on shape
--------------
* ``status`` is the one curriculum vocabulary doc 03 enumerates outright (§5:
  "constrained to draft/active/superseded"), so unlike ``programs.status`` nothing is
  invented here.
* ``unique(board_id, academic_year)`` ships partial on ``deleted_at IS NULL``,
  per ADR-004 — one active syllabus version per board per year, with a deleted one
  free to be recreated.
* This table is the reason the whole group exists: a syllabus revision must not
  rewrite history. A paper generated against 2024-25 keeps resolving to the chapters
  that existed then, because ``chapters`` rows are version-scoped rather than edited
  in place. Nothing is deleted; ``status`` moves draft → active → superseded.
"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

# revision identifiers, used by Alembic.
revision: str = "0004"
down_revision: Union[str, Sequence[str], None] = "0003"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None

_SOFT_DELETE_PREDICATE = "deleted_at IS NULL"


def upgrade() -> None:
    """Upgrade schema."""

    # ------------------------------------------------------------------
    # curriculum_versions — syllabus version boundary (doc 03 §5)
    # ------------------------------------------------------------------
    op.create_table(
        "curriculum_versions",
        sa.Column(
            "id",
            postgresql.UUID(as_uuid=True),
            server_default=sa.text("gen_random_uuid()"),
            nullable=False,
        ),
        sa.Column("board_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("academic_year", sa.Text(), nullable=False),
        sa.Column("label", sa.Text(), nullable=True),
        sa.Column("status", sa.Text(), server_default=sa.text("'draft'"), nullable=False),
        sa.Column("effective_from", sa.Date(), nullable=True),
        sa.Column("source_ref", sa.Text(), nullable=True),
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
        # doc 03 §5: "status constrained to draft/active/superseded". A CHECK on a text
        # column, never a native ENUM (ADR-011).
        sa.CheckConstraint(
            "status IN ('draft', 'active', 'superseded')",
            name=op.f("ck_curriculum_versions_status"),
        ),
        sa.CheckConstraint(
            "row_version > 0",
            name=op.f("ck_curriculum_versions_row_version_positive"),
        ),
        # doc 03 §5: "restrict deletion when dependent subjects or chapters exist" is
        # this table's own rule; boards' rule ("restrict deletion when curriculum
        # records exist") is what governs this FK.
        sa.ForeignKeyConstraint(
            ["board_id"],
            ["boards.id"],
            name=op.f("fk_curriculum_versions_board_id_boards"),
            ondelete="RESTRICT",
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_curriculum_versions")),
    )
    # doc 03 §5 indexes: board_id, status, effective_from.
    op.create_index(
        op.f("ix_curriculum_versions_board_id"),
        "curriculum_versions",
        ["board_id"],
    )
    op.create_index(
        op.f("ix_curriculum_versions_effective_from"),
        "curriculum_versions",
        ["effective_from"],
    )
    op.create_index(
        op.f("ix_curriculum_versions_status"),
        "curriculum_versions",
        ["status"],
    )
    op.create_index(
        "uq_curriculum_versions_board_id_academic_year",
        "curriculum_versions",
        ["board_id", "academic_year"],
        unique=True,
        postgresql_where=sa.text(_SOFT_DELETE_PREDICATE),
    )

    # ------------------------------------------------------------------
    # "wire version relations" — the deferred halves of revision 0003
    #
    # RESTRICT on both: a syllabus version that subjects or chapters still point at
    # must not be removable (doc 03 §5 curriculum_versions, "restrict deletion when
    # dependent subjects or chapters exist"). The columns stay nullable, so a subject
    # that predates any versioned syllabus is still representable.
    #
    # No backfill is needed. Both columns were created NULL in 0003 and NULL satisfies
    # a foreign key trivially, so ADD CONSTRAINT validates instantly against any
    # existing rows instead of scanning them.
    # ------------------------------------------------------------------
    op.create_foreign_key(
        op.f("fk_subjects_curriculum_version_id_curriculum_versions"),
        "subjects",
        "curriculum_versions",
        ["curriculum_version_id"],
        ["id"],
        ondelete="RESTRICT",
    )
    op.create_foreign_key(
        op.f("fk_chapters_curriculum_version_id_curriculum_versions"),
        "chapters",
        "curriculum_versions",
        ["curriculum_version_id"],
        ["id"],
        ondelete="RESTRICT",
    )


def downgrade() -> None:
    """Downgrade schema.

    Reverses in the mirror order: drop the two wired constraints first, because
    ``curriculum_versions`` cannot be dropped while anything references it, then drop
    the table. The two columns themselves survive — they belong to revision 0003 and
    ``downgrade -1`` from here must land exactly on 0003's state, not past it.
    """

    op.drop_constraint(
        op.f("fk_chapters_curriculum_version_id_curriculum_versions"),
        "chapters",
        type_="foreignkey",
    )
    op.drop_constraint(
        op.f("fk_subjects_curriculum_version_id_curriculum_versions"),
        "subjects",
        type_="foreignkey",
    )

    op.drop_index(
        "uq_curriculum_versions_board_id_academic_year",
        table_name="curriculum_versions",
    )
    op.drop_index(op.f("ix_curriculum_versions_status"), table_name="curriculum_versions")
    op.drop_index(
        op.f("ix_curriculum_versions_effective_from"),
        table_name="curriculum_versions",
    )
    op.drop_index(op.f("ix_curriculum_versions_board_id"), table_name="curriculum_versions")
    op.drop_table("curriculum_versions")
