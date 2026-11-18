"""create generated_papers (doc 03 §9 step 10)

Revision ID: 0010
Revises: 0009
Create Date: 2026-08-17

doc 03 §9 step 10: "Create generated_papers." The paper aggregate — its blueprint
snapshot, its provenance, and its workflow state. Five foreign keys, more than any other
table in the schema, because a paper is the point where tenancy, authorship, programs,
curriculum versions and templates all meet.

This revision opens P1-T4 round 2 (§9 steps 10-19). Every literal is frozen on purpose —
see revision 0003's docstring.

The ON DELETE actions, and why they are not all the same
--------------------------------------------------------
doc 03 §5 names an action for none of the five. Inferred as follows and reported rather
than presented as fact:

* ``org_id`` → **CASCADE**. The tenant-offboarding path, consistent with every other
  ``org_id`` in the schema.
* ``created_by`` → **RESTRICT**. doc 03 §5 makes this column *required*, which removes
  SET NULL from the options — you cannot null a NOT NULL column. So this is the one
  authorship FK in the schema that is not SET NULL, and the consequence is worth saying
  out loud: a user who has ever created a paper cannot be hard-deleted, only disabled
  (``users.status``, ADR-012 §2). The alternative reading — that ``created_by`` should
  have been nullable like ``question_bank.created_by`` and ``templates.created_by`` — is
  reported as an inconsistency in doc 03 rather than silently applied.
* ``program_id``, ``curriculum_version_id``, ``template_id`` → **SET NULL**. All three
  are provenance pointers into data that outlives nothing: ``blueprint`` is a *snapshot*
  (doc 03 §5's own word) and ``paper_versions.content`` holds the rendered result, so a
  paper stays complete and reproducible after its program is retired or its template
  archived.

  SET NULL is also the choice that keeps tenant offboarding working. ``programs`` is
  itself ``ON DELETE CASCADE`` from ``organizations``, so a RESTRICT on ``program_id``
  would create a race between two referential-integrity triggers on the same
  ``DELETE FROM organizations``: PostgreSQL fires RI triggers as AFTER-row triggers in
  constraint-OID order, and if the RESTRICT check runs before the CASCADE has removed
  the papers, the delete aborts. That failure depends on the order two constraints
  happened to be created in, which is not a property anyone should have to reason about
  during an offboarding.

What is *not* here
------------------
* **No ``paper_questions`` bridge table.** doc 03 defines none, and ADR-003 explains
  why: the selection is a rendered snapshot inside ``paper_versions.content``, so a
  paper printed last term still resolves to the text that was printed even after every
  question in it has been forked.
* **No BR-6 enforcement.** Separation of duties needs ``approvals.reviewer_id <>
  generated_papers.created_by``, a comparison across two rows in two tables. A CHECK
  constraint cannot see another table, and doc 03 specifies no trigger. This revision
  supplies the two columns the rule is evaluated over; enforcement is P1-T5's. Reported.
* **No partitioning**, though the Blueprint §8 DDL ends this table with
  ``PARTITION BY RANGE (created_at) -- monthly partitions``. doc 03 §5 says nothing
  about partitioning ``generated_papers`` at all — it mentions time partitions only for
  ``usage`` and ``audit_logs`` — and no interval, retention or partition-creation owner
  is specified anywhere. Plain table; the contradiction is reported.
* **No unique constraint.** doc 03 §5 lists none, so two papers may share a title. That
  is correct rather than lax: the same unit test generated for two batches is the normal
  case.
"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

# revision identifiers, used by Alembic.
revision: str = "0010"
down_revision: Union[str, Sequence[str], None] = "0009"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    """Upgrade schema."""

    op.create_table(
        "generated_papers",
        sa.Column(
            "id",
            postgresql.UUID(as_uuid=True),
            server_default=sa.text("gen_random_uuid()"),
            nullable=False,
        ),
        sa.Column("org_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("created_by", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("program_id", postgresql.UUID(as_uuid=True), nullable=True),
        sa.Column("blueprint", postgresql.JSONB(astext_type=sa.Text()), nullable=False),
        sa.Column("curriculum_version_id", postgresql.UUID(as_uuid=True), nullable=True),
        sa.Column("template_id", postgresql.UUID(as_uuid=True), nullable=True),
        sa.Column("title", sa.Text(), nullable=False),
        # numeric(5,1) per doc 03 §5 — one digit wider than `question_bank.marks`
        # (numeric(4,1)), because a paper totals many questions. Exact, not float: the
        # marks have to sum to the blueprint total without drift.
        sa.Column("total_marks", sa.Numeric(precision=5, scale=1), nullable=True),
        sa.Column("duration_min", sa.Integer(), nullable=True),
        sa.Column("status", sa.Text(), server_default=sa.text("'draft'"), nullable=False),
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
        # doc 03 §5 lists the five values in lifecycle order and §7.3 repeats them, so
        # the CHECK is written in that order too.
        sa.CheckConstraint(
            "status IN ('draft', 'submitted', 'review', 'approved', 'rejected')",
            name=op.f("ck_generated_papers_status"),
        ),
        sa.CheckConstraint(
            "row_version > 0",
            name=op.f("ck_generated_papers_row_version_positive"),
        ),
        sa.ForeignKeyConstraint(
            ["created_by"],
            ["users.id"],
            name=op.f("fk_generated_papers_created_by_users"),
            ondelete="RESTRICT",
        ),
        sa.ForeignKeyConstraint(
            ["curriculum_version_id"],
            ["curriculum_versions.id"],
            name=op.f("fk_generated_papers_curriculum_version_id_curriculum_versions"),
            ondelete="SET NULL",
        ),
        sa.ForeignKeyConstraint(
            ["org_id"],
            ["organizations.id"],
            name=op.f("fk_generated_papers_org_id_organizations"),
            ondelete="CASCADE",
        ),
        sa.ForeignKeyConstraint(
            ["program_id"],
            ["programs.id"],
            name=op.f("fk_generated_papers_program_id_programs"),
            ondelete="SET NULL",
        ),
        sa.ForeignKeyConstraint(
            ["template_id"],
            ["templates.id"],
            name=op.f("fk_generated_papers_template_id_templates"),
            ondelete="SET NULL",
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_generated_papers")),
    )
    # doc 03 §5 indexes: org_id, status, created_at, program_id, and (created_by,
    # created_at) as one composite rather than two singles — "this teacher's papers,
    # newest first" is the query the Papers list runs.
    op.create_index(op.f("ix_generated_papers_created_at"), "generated_papers", ["created_at"])
    op.create_index(
        op.f("ix_generated_papers_created_by_created_at"),
        "generated_papers",
        ["created_by", "created_at"],
    )
    op.create_index(op.f("ix_generated_papers_org_id"), "generated_papers", ["org_id"])
    op.create_index(op.f("ix_generated_papers_program_id"), "generated_papers", ["program_id"])
    op.create_index(op.f("ix_generated_papers_status"), "generated_papers", ["status"])


def downgrade() -> None:
    """Downgrade schema.

    Fully reversible: one table created from nothing, so dropping it restores revision
    0009's state exactly. The five foreign keys go with the table — no separate
    ``drop_constraint`` is needed, and issuing one would fail on the second run.
    """

    op.drop_index(op.f("ix_generated_papers_status"), table_name="generated_papers")
    op.drop_index(op.f("ix_generated_papers_program_id"), table_name="generated_papers")
    op.drop_index(op.f("ix_generated_papers_org_id"), table_name="generated_papers")
    op.drop_index(
        op.f("ix_generated_papers_created_by_created_at"),
        table_name="generated_papers",
    )
    op.drop_index(op.f("ix_generated_papers_created_at"), table_name="generated_papers")
    op.drop_table("generated_papers")
