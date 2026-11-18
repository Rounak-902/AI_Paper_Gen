"""Run question imports as durable background jobs.

Revision ID: 0042
Revises: 0041
Create Date: 2026-10-02
"""

from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "0042"
down_revision: Union[str, Sequence[str], None] = "0041"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.create_table(
        "question_import_jobs",
        sa.Column(
            "id",
            postgresql.UUID(as_uuid=True),
            server_default=sa.text("gen_random_uuid()"),
            nullable=False,
        ),
        sa.Column("org_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("requested_by", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("requester_role", sa.Text(), nullable=False),
        sa.Column("filename", sa.Text(), nullable=False),
        sa.Column("content_type", sa.Text(), nullable=False),
        sa.Column("file_bytes", sa.LargeBinary(), nullable=True),
        sa.Column("column_mapping", postgresql.JSONB(), nullable=False),
        sa.Column("request_ip", sa.Text(), nullable=True),
        sa.Column("status", sa.Text(), server_default=sa.text("'queued'"), nullable=False),
        sa.Column("total_rows", sa.Integer(), nullable=False),
        sa.Column("processed_rows", sa.Integer(), server_default=sa.text("0"), nullable=False),
        sa.Column("created_count", sa.Integer(), server_default=sa.text("0"), nullable=False),
        sa.Column(
            "created_with_warnings_count",
            sa.Integer(),
            server_default=sa.text("0"),
            nullable=False,
        ),
        sa.Column("error_count", sa.Integer(), server_default=sa.text("0"), nullable=False),
        sa.Column("duplicate_count", sa.Integer(), server_default=sa.text("0"), nullable=False),
        sa.Column(
            "row_results",
            postgresql.JSONB(),
            server_default=sa.text("'[]'::jsonb"),
            nullable=False,
        ),
        sa.Column(
            "error_report_available",
            sa.Boolean(),
            server_default=sa.text("false"),
            nullable=False,
        ),
        sa.Column("error", sa.Text(), nullable=True),
        sa.Column("started_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("completed_at", sa.DateTime(timezone=True), nullable=True),
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
        sa.CheckConstraint(
            "status IN ('queued','processing','completed','failed')",
            name=op.f("ck_question_import_jobs_status"),
        ),
        sa.CheckConstraint(
            "total_rows >= 0",
            name=op.f("ck_question_import_jobs_total_rows_nonnegative"),
        ),
        sa.CheckConstraint(
            "processed_rows >= 0 AND processed_rows <= total_rows",
            name=op.f("ck_question_import_jobs_processed_rows_in_range"),
        ),
        sa.CheckConstraint(
            "created_count >= 0 AND created_with_warnings_count >= 0 "
            "AND error_count >= 0 AND duplicate_count >= 0",
            name=op.f("ck_question_import_jobs_counts_nonnegative"),
        ),
        sa.ForeignKeyConstraint(
            ["org_id"], ["organizations.id"], ondelete="CASCADE"
        ),
        sa.ForeignKeyConstraint(
            ["requested_by"], ["users.id"], ondelete="RESTRICT"
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_question_import_jobs")),
    )
    op.create_index(
        "ix_question_import_jobs_org_status",
        "question_import_jobs",
        ["org_id", "status"],
    )
    op.create_index(
        "ix_question_import_jobs_org_requested_by_created",
        "question_import_jobs",
        ["org_id", "requested_by", "created_at"],
    )
    op.execute(sa.text("ALTER TABLE public.question_import_jobs ENABLE ROW LEVEL SECURITY"))
    op.execute(sa.text("ALTER TABLE public.question_import_jobs FORCE ROW LEVEL SECURITY"))
    op.execute(
        sa.text(
            "CREATE POLICY tenant_isolation_policy ON public.question_import_jobs "
            "FOR ALL TO app_user "
            "USING (org_id = current_setting('app.org_id')::uuid) "
            "WITH CHECK (org_id = current_setting('app.org_id')::uuid)"
        )
    )
    op.execute(
        sa.text(
            "GRANT SELECT, INSERT, UPDATE, DELETE ON TABLE public.question_import_jobs TO app_user"
        )
    )


def downgrade() -> None:
    op.drop_index(
        "ix_question_import_jobs_org_requested_by_created",
        table_name="question_import_jobs",
    )
    op.drop_index(
        "ix_question_import_jobs_org_status", table_name="question_import_jobs"
    )
    op.drop_table("question_import_jobs")
