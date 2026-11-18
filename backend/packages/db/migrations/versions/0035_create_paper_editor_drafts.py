"""create mutable Paper Builder draft buffers

Revision ID: 0035
Revises: 0034
Create Date: 2026-09-25

The table is intentionally not ``paper_versions``. Generated versions remain immutable
and worker-owned; this table stores whole editor snapshots for crash recovery and uses a
small integer revision only as an optimistic-concurrency token.
"""

from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "0035"
down_revision: Union[str, Sequence[str], None] = "0034"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.create_table(
        "paper_editor_drafts",
        sa.Column(
            "id",
            postgresql.UUID(as_uuid=True),
            server_default=sa.text("gen_random_uuid()"),
            nullable=False,
        ),
        sa.Column("org_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("owner_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("paper_id", postgresql.UUID(as_uuid=True), nullable=True),
        sa.Column("document", postgresql.JSONB(astext_type=sa.Text()), nullable=False),
        sa.Column("revision", sa.Integer(), server_default=sa.text("1"), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
        sa.CheckConstraint("revision > 0", name=op.f("ck_paper_editor_drafts_revision_positive")),
        sa.ForeignKeyConstraint(["org_id"], ["organizations.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["owner_id"], ["users.id"], ondelete="RESTRICT"),
        sa.ForeignKeyConstraint(["paper_id"], ["generated_papers.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_paper_editor_drafts")),
    )
    op.create_index(
        "ix_paper_editor_drafts_org_owner_updated",
        "paper_editor_drafts",
        ["org_id", "owner_id", "updated_at"],
    )
    op.create_index(
        "ix_paper_editor_drafts_paper_id",
        "paper_editor_drafts",
        ["paper_id"],
    )
    op.execute(sa.text("ALTER TABLE public.paper_editor_drafts ENABLE ROW LEVEL SECURITY"))
    op.execute(sa.text("ALTER TABLE public.paper_editor_drafts FORCE ROW LEVEL SECURITY"))
    op.execute(
        sa.text(
            "CREATE POLICY tenant_isolation_policy ON public.paper_editor_drafts "
            "FOR ALL TO app_user "
            "USING (org_id = current_setting('app.org_id')::uuid) "
            "WITH CHECK (org_id = current_setting('app.org_id')::uuid)"
        )
    )
    op.execute(
        sa.text(
            "GRANT SELECT, INSERT, UPDATE, DELETE ON public.paper_editor_drafts TO app_user"
        )
    )


def downgrade() -> None:
    op.drop_index("ix_paper_editor_drafts_paper_id", table_name="paper_editor_drafts")
    op.drop_index("ix_paper_editor_drafts_org_owner_updated", table_name="paper_editor_drafts")
    op.drop_table("paper_editor_drafts")
