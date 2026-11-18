"""complete the templates lifecycle and org-default invariants

Revision ID: 0036
Revises: 0035
Create Date: 2026-09-25

The templates table itself deliberately remains revision 0009, immediately before
generated_papers as required by doc 03 section 9. This additive migration supplies
the lifecycle/default metadata needed by the P6-T1 module.
"""

from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "0036"
down_revision: Union[str, Sequence[str], None] = "0035"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.add_column(
        "templates",
        sa.Column("is_default", sa.Boolean(), server_default=sa.text("false"), nullable=False),
    )
    op.add_column(
        "templates",
        sa.Column("source_template_id", postgresql.UUID(as_uuid=True), nullable=True),
    )
    op.create_foreign_key(
        op.f("fk_templates_source_template_id_templates"),
        "templates",
        "templates",
        ["source_template_id"],
        ["id"],
        ondelete="SET NULL",
    )
    op.create_index(
        "ix_templates_source_template_id", "templates", ["source_template_id"]
    )

    # Existing rows predate org defaults. Pick one active row per org deterministically
    # so an upgraded tenant with templates does not lose the downstream fallback.
    op.execute(
        sa.text(
            "WITH ranked AS ("
            " SELECT id, row_number() OVER (PARTITION BY org_id ORDER BY updated_at DESC, id) AS rn"
            " FROM templates"
            " WHERE org_id IS NOT NULL AND status = 'active' AND deleted_at IS NULL"
            ") UPDATE templates SET is_default = true"
            " FROM ranked WHERE templates.id = ranked.id AND ranked.rn = 1"
        )
    )
    op.create_index(
        "uq_templates_one_default_per_org",
        "templates",
        ["org_id"],
        unique=True,
        postgresql_where=sa.text(
            "is_default IS TRUE AND deleted_at IS NULL AND org_id IS NOT NULL"
        ),
    )
    op.create_check_constraint(
        "ck_templates_default_must_be_active",
        "templates",
        "is_default IS FALSE OR status = 'active'",
    )
    op.alter_column(
        "templates",
        "status",
        existing_type=sa.Text(),
        server_default=sa.text("'draft'"),
        existing_nullable=False,
    )


def downgrade() -> None:
    op.alter_column(
        "templates",
        "status",
        existing_type=sa.Text(),
        server_default=sa.text("'active'"),
        existing_nullable=False,
    )
    op.drop_constraint("ck_templates_default_must_be_active", "templates", type_="check")
    op.drop_index("uq_templates_one_default_per_org", table_name="templates")
    op.drop_index("ix_templates_source_template_id", table_name="templates")
    op.drop_constraint(
        op.f("fk_templates_source_template_id_templates"),
        "templates",
        type_="foreignkey",
    )
    op.drop_column("templates", "source_template_id")
    op.drop_column("templates", "is_default")
