"""create templates (doc 03 §9 step 9)

Revision ID: 0009
Revises: 0008
Create Date: 2026-08-17

doc 03 §9 step 9: "Create templates." One table, and the first member of doc 03 §4's
**Papers** group. A template is the presentation half of a paper — header, logo,
section headings, marks column, instruction block. The content half
(``generated_papers``, ``paper_versions``, ``approvals``) is §9 steps 10-11 and
arrives in later revisions.

This is the head of P1-T4 round 1: revisions 0003-0009 cover doc 03 §9 steps 3-9, and
``alembic check`` at this point must come back clean, because ``Base.metadata`` after
``app/models/__init__.py`` contains exactly the thirteen tables these revisions create
plus the four from 0002.

Every literal is frozen on purpose — see revision 0003's docstring.

Notes on shape
--------------
* **``status`` defaults to ``'active'``, not ``'draft'``.** doc 03 §5 says so outright
  ("Default values: status active"), and it is the opposite of the choice made for
  ``question_bank.status`` in revision 0006 — deliberately, because the risk is
  reversed. An unreviewed *question* must not be publishable; an unlabelled *layout*
  is just a layout, and forcing every template through a draft state nobody advances
  would make the common case need two writes.
* **``layout`` is JSONB NOT NULL.** doc 03 §8 lists it among the deliberately-JSONB
  columns, and §3's "do not store mutable canonical content only inside JSON blobs"
  rule is satisfied: nothing canonical lives here, so losing a layout costs a
  re-render rather than a question.
* **``created_by`` is SET NULL**, matching ``question_bank.created_by`` — a template
  outlives the person who built it. doc 03 §5 names no action for this FK; inferred
  and reported.

Open item, reported not resolved
--------------------------------
doc 03 §5 asks for "name unique **within owner scope**". The owner is ``org_id``, so
the index is ``(org_id, name)`` — partial on ``deleted_at IS NULL`` per ADR-004 so a
deleted template's name can be reused. But ``org_id`` is nullable and PostgreSQL
unique indexes are NULLS DISTINCT, so "owner scope" is precisely the thing this index
*cannot* close for platform-global templates: two rows with ``org_id IS NULL`` may
share a name. Third instance of the same gap (``programs`` in 0005, ``textbooks`` in
0008), and it wants one product answer rather than three separate guesses — either
``NULLS NOT DISTINCT`` on PG15+, or a second partial unique index
``WHERE org_id IS NULL AND deleted_at IS NULL``.
"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

# revision identifiers, used by Alembic.
revision: str = "0009"
down_revision: Union[str, Sequence[str], None] = "0008"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None

_SOFT_DELETE_PREDICATE = "deleted_at IS NULL"


def upgrade() -> None:
    """Upgrade schema."""

    op.create_table(
        "templates",
        sa.Column(
            "id",
            postgresql.UUID(as_uuid=True),
            server_default=sa.text("gen_random_uuid()"),
            nullable=False,
        ),
        sa.Column("org_id", postgresql.UUID(as_uuid=True), nullable=True),
        sa.Column("name", sa.Text(), nullable=False),
        sa.Column("layout", postgresql.JSONB(astext_type=sa.Text()), nullable=False),
        sa.Column("created_by", postgresql.UUID(as_uuid=True), nullable=True),
        sa.Column("status", sa.Text(), server_default=sa.text("'active'"), nullable=False),
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
        # Written lifecycle-first (draft → active → archived) rather than in doc 03
        # §5's "active/draft/archived" listing order, so the CHECK reads as the state
        # machine it is. Same three values.
        sa.CheckConstraint(
            "status IN ('draft', 'active', 'archived')",
            name=op.f("ck_templates_status"),
        ),
        sa.CheckConstraint("row_version > 0", name=op.f("ck_templates_row_version_positive")),
        sa.ForeignKeyConstraint(
            ["created_by"],
            ["users.id"],
            name=op.f("fk_templates_created_by_users"),
            ondelete="SET NULL",
        ),
        # doc 03 §5: "org deletion cascades org-owned templates".
        sa.ForeignKeyConstraint(
            ["org_id"],
            ["organizations.id"],
            name=op.f("fk_templates_org_id_organizations"),
            ondelete="CASCADE",
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_templates")),
    )
    # doc 03 §5 indexes: org_id, status, created_at.
    op.create_index(op.f("ix_templates_created_at"), "templates", ["created_at"])
    op.create_index(op.f("ix_templates_org_id"), "templates", ["org_id"])
    op.create_index(op.f("ix_templates_status"), "templates", ["status"])
    # See the module docstring for the NULLS DISTINCT gap this leaves open.
    op.create_index(
        "uq_templates_org_id_name",
        "templates",
        ["org_id", "name"],
        unique=True,
        postgresql_where=sa.text(_SOFT_DELETE_PREDICATE),
    )


def downgrade() -> None:
    """Downgrade schema.

    Fully reversible: one table created from nothing, so dropping it restores
    revision 0008's state exactly.
    """

    op.drop_index("uq_templates_org_id_name", table_name="templates")
    op.drop_index(op.f("ix_templates_status"), table_name="templates")
    op.drop_index(op.f("ix_templates_org_id"), table_name="templates")
    op.drop_index(op.f("ix_templates_created_at"), table_name="templates")
    op.drop_table("templates")
