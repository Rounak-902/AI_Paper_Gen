"""P4-T5 durable assembly checkpoints and transactional Celery outbox.

Revision ID: 0034
Revises: 0033
Create Date: 2026-09-20
"""

from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "0034"
down_revision: Union[str, Sequence[str], None] = "0033"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def _uuid() -> sa.Column:
    return sa.Column(
        "id",
        postgresql.UUID(as_uuid=True),
        server_default=sa.text("gen_random_uuid()"),
        nullable=False,
    )


def upgrade() -> None:
    op.create_table(
        "paper_assembly_runs",
        _uuid(),
        sa.Column("org_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("paper_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("label", sa.Text(), nullable=False),
        sa.Column("status", sa.Text(), server_default=sa.text("'planned'"), nullable=False),
        sa.Column("solver_snapshot", postgresql.JSONB(astext_type=sa.Text()), nullable=False),
        sa.Column("residual_solve_count", sa.Integer(), server_default=sa.text("0"), nullable=False),
        sa.Column("paper_version_id", postgresql.UUID(as_uuid=True), nullable=True),
        sa.Column("last_error", sa.Text(), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
        sa.CheckConstraint(
            "status IN ('planned','running','finalizing','completed','failed')",
            name=op.f("ck_paper_assembly_runs_status"),
        ),
        sa.CheckConstraint(
            "residual_solve_count >= 0",
            name=op.f("ck_paper_assembly_runs_residual_solve_count_nonnegative"),
        ),
        sa.ForeignKeyConstraint(["org_id"], ["organizations.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["paper_id"], ["generated_papers.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["paper_version_id"], ["paper_versions.id"], ondelete="SET NULL"),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_paper_assembly_runs")),
        sa.UniqueConstraint("paper_id", "label", name="uq_paper_assembly_runs_paper_label"),
    )
    op.create_index("ix_paper_assembly_runs_org_status", "paper_assembly_runs", ["org_id", "status"])

    op.create_table(
        "paper_assembly_slots",
        _uuid(),
        sa.Column("org_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("run_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("paper_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("slot_id", sa.Text(), nullable=False),
        sa.Column("section_id", sa.Text(), nullable=False),
        sa.Column("position", sa.Integer(), nullable=False),
        sa.Column("status", sa.Text(), server_default=sa.text("'pending'"), nullable=False),
        sa.Column("idempotency_key", sa.Text(), nullable=False),
        sa.Column("solver_question_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("question_id", postgresql.UUID(as_uuid=True), nullable=True),
        sa.Column("attempt_count", sa.Integer(), server_default=sa.text("0"), nullable=False),
        sa.Column("snapshot", postgresql.JSONB(astext_type=sa.Text()), nullable=True),
        sa.Column("answer", postgresql.JSONB(astext_type=sa.Text()), nullable=True),
        sa.Column("trace", postgresql.JSONB(astext_type=sa.Text()), nullable=True),
        sa.Column("last_error", sa.Text(), nullable=True),
        sa.Column("completed_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
        sa.CheckConstraint("status IN ('pending','running','completed','failed')", name=op.f("ck_paper_assembly_slots_status")),
        sa.CheckConstraint("position > 0", name=op.f("ck_paper_assembly_slots_position_positive")),
        sa.CheckConstraint("attempt_count >= 0", name=op.f("ck_paper_assembly_slots_attempt_count_nonnegative")),
        sa.ForeignKeyConstraint(["org_id"], ["organizations.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["run_id"], ["paper_assembly_runs.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["paper_id"], ["generated_papers.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["question_id"], ["question_bank.id"], ondelete="RESTRICT"),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_paper_assembly_slots")),
        sa.UniqueConstraint("run_id", "slot_id", name="uq_paper_assembly_slots_run_slot"),
        sa.UniqueConstraint("idempotency_key", name="uq_paper_assembly_slots_idempotency_key"),
    )
    op.create_index("ix_paper_assembly_slots_run_status", "paper_assembly_slots", ["run_id", "status"])
    op.create_index("ix_paper_assembly_slots_paper", "paper_assembly_slots", ["paper_id"])

    op.create_table(
        "task_outbox",
        _uuid(),
        sa.Column("org_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("task_name", sa.Text(), nullable=False),
        sa.Column("idempotency_key", sa.Text(), nullable=False),
        sa.Column("payload", postgresql.JSONB(astext_type=sa.Text()), nullable=False),
        sa.Column("status", sa.Text(), server_default=sa.text("'pending'"), nullable=False),
        sa.Column("publish_attempts", sa.Integer(), server_default=sa.text("0"), nullable=False),
        sa.Column("available_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
        sa.Column("published_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("last_error", sa.Text(), nullable=True),
        sa.CheckConstraint("status IN ('pending','published')", name=op.f("ck_task_outbox_status")),
        sa.CheckConstraint("publish_attempts >= 0", name=op.f("ck_task_outbox_publish_attempts_nonnegative")),
        sa.ForeignKeyConstraint(["org_id"], ["organizations.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_task_outbox")),
        sa.UniqueConstraint("idempotency_key", name="uq_task_outbox_idempotency_key"),
    )
    op.create_index("ix_task_outbox_pending", "task_outbox", ["status", "available_at"])

    for table in ("paper_assembly_runs", "paper_assembly_slots"):
        op.execute(sa.text(f"ALTER TABLE public.{table} ENABLE ROW LEVEL SECURITY"))
        op.execute(sa.text(f"ALTER TABLE public.{table} FORCE ROW LEVEL SECURITY"))
        op.execute(
            sa.text(
                f"CREATE POLICY tenant_isolation_policy ON public.{table} "
                "FOR ALL TO app_user "
                "USING (org_id = current_setting('app.org_id')::uuid) "
                "WITH CHECK (org_id = current_setting('app.org_id')::uuid)"
            )
        )

    # task_outbox is operational infrastructure, intentionally cross-tenant so a
    # single reconciler can recover publications. It contains task coordinates,
    # not paper/question content.
    for table in ("paper_assembly_runs", "paper_assembly_slots", "task_outbox"):
        op.execute(sa.text(f"GRANT SELECT, INSERT, UPDATE, DELETE ON public.{table} TO app_user"))


def downgrade() -> None:
    op.drop_index("ix_task_outbox_pending", table_name="task_outbox")
    op.drop_table("task_outbox")
    op.drop_index("ix_paper_assembly_slots_paper", table_name="paper_assembly_slots")
    op.drop_index("ix_paper_assembly_slots_run_status", table_name="paper_assembly_slots")
    op.drop_table("paper_assembly_slots")
    op.drop_index("ix_paper_assembly_runs_org_status", table_name="paper_assembly_runs")
    op.drop_table("paper_assembly_runs")
