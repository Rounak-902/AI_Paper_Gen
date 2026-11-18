"""P6-T2 durable export jobs, artifact lineage, and export metering.

Revision ID: 0037
Revises: 0036
Create Date: 2026-09-26
"""

from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "0037"
down_revision: Union[str, Sequence[str], None] = "0036"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.create_table(
        "export_artifacts",
        sa.Column(
            "id",
            postgresql.UUID(as_uuid=True),
            server_default=sa.text("gen_random_uuid()"),
            nullable=False,
        ),
        sa.Column("org_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("paper_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("paper_version_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("template_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("requested_by", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("format", sa.Text(), nullable=False),
        sa.Column("status", sa.Text(), server_default=sa.text("'queued'"), nullable=False),
        sa.Column("object_key", sa.Text(), nullable=True),
        sa.Column("media_type", sa.Text(), nullable=True),
        sa.Column("byte_size", sa.BigInteger(), nullable=True),
        sa.Column("sha256", sa.Text(), nullable=True),
        sa.Column("error", sa.Text(), nullable=True),
        sa.Column("started_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("completed_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
        sa.CheckConstraint("format IN ('pdf','docx')", name=op.f("ck_export_artifacts_format")),
        sa.CheckConstraint(
            "status IN ('queued','processing','done','failed')",
            name=op.f("ck_export_artifacts_status"),
        ),
        sa.CheckConstraint(
            "byte_size IS NULL OR byte_size > 0",
            name=op.f("ck_export_artifacts_byte_size_positive"),
        ),
        sa.CheckConstraint(
            "(status = 'done' AND object_key IS NOT NULL AND media_type IS NOT NULL "
            "AND byte_size IS NOT NULL AND sha256 IS NOT NULL AND completed_at IS NOT NULL) "
            "OR status <> 'done'",
            name=op.f("ck_export_artifacts_done_has_artifact"),
        ),
        sa.ForeignKeyConstraint(["org_id"], ["organizations.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["paper_id"], ["generated_papers.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["paper_version_id"], ["paper_versions.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["template_id"], ["templates.id"], ondelete="RESTRICT"),
        sa.ForeignKeyConstraint(["requested_by"], ["users.id"], ondelete="RESTRICT"),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_export_artifacts")),
    )
    op.create_index(
        "ix_export_artifacts_org_status", "export_artifacts", ["org_id", "status"]
    )
    op.create_index(
        "ix_export_artifacts_paper_version",
        "export_artifacts",
        ["paper_version_id", "created_at"],
    )
    op.execute(sa.text("ALTER TABLE public.export_artifacts ENABLE ROW LEVEL SECURITY"))
    op.execute(sa.text("ALTER TABLE public.export_artifacts FORCE ROW LEVEL SECURITY"))
    op.execute(
        sa.text(
            "CREATE POLICY tenant_isolation_policy ON public.export_artifacts "
            "FOR ALL TO app_user "
            "USING (org_id = current_setting('app.org_id')::uuid) "
            "WITH CHECK (org_id = current_setting('app.org_id')::uuid)"
        )
    )
    op.execute(
        sa.text(
            "GRANT SELECT, INSERT, UPDATE, DELETE ON TABLE public.export_artifacts TO app_user"
        )
    )

    op.drop_constraint(op.f("ck_usage_metric"), "usage", type_="check")
    op.create_check_constraint(
        op.f("ck_usage_metric"),
        "usage",
        "metric IN ('papers_generated','ai_tokens','ocr_pages','exports_generated')",
    )


def downgrade() -> None:
    op.drop_constraint(op.f("ck_usage_metric"), "usage", type_="check")
    op.create_check_constraint(
        op.f("ck_usage_metric"),
        "usage",
        "metric IN ('papers_generated','ai_tokens','ocr_pages')",
    )
    op.drop_index("ix_export_artifacts_paper_version", table_name="export_artifacts")
    op.drop_index("ix_export_artifacts_org_status", table_name="export_artifacts")
    op.drop_table("export_artifacts")
