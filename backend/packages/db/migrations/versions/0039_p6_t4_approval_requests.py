"""P6-T4 version-bound approval requests and append-only transition history.

Revision ID: 0039
Revises: 0038
Create Date: 2026-09-27
"""

from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "0039"
down_revision: Union[str, Sequence[str], None] = "0038"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def _tenant_table(table: str, *, update: bool) -> None:
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
    privileges = "SELECT, INSERT, UPDATE" if update else "SELECT, INSERT"
    op.execute(sa.text(f"GRANT {privileges} ON TABLE public.{table} TO app_user"))
    op.execute(sa.text(f"REVOKE DELETE ON TABLE public.{table} FROM app_user"))
    if not update:
        op.execute(sa.text(f"REVOKE UPDATE ON TABLE public.{table} FROM app_user"))


def upgrade() -> None:
    op.create_table(
        "approval_requests",
        sa.Column("id", postgresql.UUID(as_uuid=True), server_default=sa.text("gen_random_uuid()"), nullable=False),
        sa.Column("org_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("paper_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("paper_version_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("paper_export_artifact_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("answer_key_export_artifact_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("submitted_by", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("review_started_by", postgresql.UUID(as_uuid=True), nullable=True),
        sa.Column("approver_id", postgresql.UUID(as_uuid=True), nullable=True),
        sa.Column("status", sa.Text(), server_default=sa.text("'submitted'"), nullable=False),
        sa.Column("separation_of_duties_required", sa.Boolean(), server_default=sa.text("true"), nullable=False),
        sa.Column("coverage_report_snapshot", postgresql.JSONB(astext_type=sa.Text()), nullable=False),
        sa.Column("coverage_report_sha256", sa.Text(), nullable=False),
        sa.Column("submitted_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
        sa.Column("review_started_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("decided_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("decision_comment", sa.Text(), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
        sa.Column("deleted_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("row_version", sa.Integer(), server_default=sa.text("1"), nullable=False),
        sa.CheckConstraint("status IN ('submitted','review','approved','rejected')", name=op.f("ck_approval_requests_status")),
        sa.CheckConstraint("row_version > 0", name=op.f("ck_approval_requests_row_version_positive")),
        sa.CheckConstraint("char_length(coverage_report_sha256) = 64", name=op.f("ck_approval_requests_coverage_report_sha256_length")),
        sa.CheckConstraint("NOT separation_of_duties_required OR review_started_by IS NULL OR review_started_by <> submitted_by", name=op.f("ck_approval_requests_reviewer_differs_from_submitter")),
        sa.CheckConstraint("NOT separation_of_duties_required OR approver_id IS NULL OR approver_id <> submitted_by", name=op.f("ck_approval_requests_approver_differs_from_submitter")),
        sa.CheckConstraint(
            "(status = 'submitted' AND review_started_by IS NULL AND review_started_at IS NULL AND approver_id IS NULL AND decided_at IS NULL) OR "
            "(status = 'review' AND review_started_by IS NOT NULL AND review_started_at IS NOT NULL AND approver_id IS NULL AND decided_at IS NULL) OR "
            "(status IN ('approved','rejected') AND review_started_by IS NOT NULL AND review_started_at IS NOT NULL AND approver_id IS NOT NULL AND decided_at IS NOT NULL)",
            name=op.f("ck_approval_requests_state_fields"),
        ),
        sa.CheckConstraint("status <> 'rejected' OR nullif(btrim(decision_comment), '') IS NOT NULL", name=op.f("ck_approval_requests_rejection_comment_required")),
        sa.ForeignKeyConstraint(["org_id"], ["organizations.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["paper_id"], ["generated_papers.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["paper_version_id"], ["paper_versions.id"], ondelete="RESTRICT"),
        sa.ForeignKeyConstraint(["paper_export_artifact_id"], ["export_artifacts.id"], ondelete="RESTRICT"),
        sa.ForeignKeyConstraint(["answer_key_export_artifact_id"], ["export_artifacts.id"], ondelete="RESTRICT"),
        sa.ForeignKeyConstraint(["submitted_by"], ["users.id"], ondelete="RESTRICT"),
        sa.ForeignKeyConstraint(["review_started_by"], ["users.id"], ondelete="RESTRICT"),
        sa.ForeignKeyConstraint(["approver_id"], ["users.id"], ondelete="RESTRICT"),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_approval_requests")),
        sa.UniqueConstraint("paper_version_id", name="uq_approval_requests_paper_version_id"),
        sa.UniqueConstraint("id", "paper_version_id", name="uq_approval_requests_id_version"),
    )
    op.create_index("ix_approval_requests_org_status", "approval_requests", ["org_id", "status", "submitted_at"])
    op.create_index("ix_approval_requests_paper_created", "approval_requests", ["paper_id", "created_at"])
    op.create_index(
        "uq_approval_requests_one_pending_per_paper",
        "approval_requests",
        ["paper_id"],
        unique=True,
        postgresql_where=sa.text("status IN ('submitted','review') AND deleted_at IS NULL"),
    )

    op.create_table(
        "approval_request_events",
        sa.Column("id", postgresql.UUID(as_uuid=True), server_default=sa.text("gen_random_uuid()"), nullable=False),
        sa.Column("org_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("approval_request_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("paper_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("paper_version_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("actor_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("action", sa.Text(), nullable=False),
        sa.Column("from_status", sa.Text(), nullable=True),
        sa.Column("to_status", sa.Text(), nullable=False),
        sa.Column("comment", sa.Text(), nullable=True),
        sa.Column("occurred_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
        sa.CheckConstraint("action IN ('submitted','review_started','approved','rejected','approval_withdrawn','question_commented')", name=op.f("ck_approval_request_events_action")),
        sa.ForeignKeyConstraint(["org_id"], ["organizations.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["approval_request_id"], ["approval_requests.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["paper_id"], ["generated_papers.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["paper_version_id"], ["paper_versions.id"], ondelete="RESTRICT"),
        sa.ForeignKeyConstraint(["actor_id"], ["users.id"], ondelete="RESTRICT"),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_approval_request_events")),
    )
    op.create_index("ix_approval_request_events_request_time", "approval_request_events", ["approval_request_id", "occurred_at"])

    op.create_table(
        "approval_comments",
        sa.Column("id", postgresql.UUID(as_uuid=True), server_default=sa.text("gen_random_uuid()"), nullable=False),
        sa.Column("org_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("approval_request_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("paper_version_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("created_by", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("slot_id", sa.Text(), nullable=True),
        sa.Column("body", sa.Text(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
        sa.CheckConstraint("nullif(btrim(body), '') IS NOT NULL", name=op.f("ck_approval_comments_body_not_blank")),
        sa.ForeignKeyConstraint(["org_id"], ["organizations.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["approval_request_id", "paper_version_id"], ["approval_requests.id", "approval_requests.paper_version_id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["created_by"], ["users.id"], ondelete="RESTRICT"),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_approval_comments")),
    )
    op.create_index("ix_approval_comments_request_time", "approval_comments", ["approval_request_id", "created_at"])

    op.execute(
        sa.text(
            "CREATE FUNCTION public.validate_approval_request_insert() RETURNS trigger "
            "LANGUAGE plpgsql AS $$ DECLARE paper_author uuid; org_policy boolean; "
            "version_assembly jsonb; paper_artifact record; key_artifact record; BEGIN "
            "SELECT gp.created_by, COALESCE((o.settings->>'approvalSeparationOfDutiesRequired')::boolean, true) "
            "INTO paper_author, org_policy FROM public.generated_papers gp JOIN public.organizations o ON o.id=gp.org_id "
            "WHERE gp.id=NEW.paper_id AND gp.org_id=NEW.org_id AND gp.deleted_at IS NULL; "
            "IF paper_author IS NULL THEN RAISE EXCEPTION 'approval paper not found in organization' USING ERRCODE='23514'; END IF; "
            "IF paper_author <> NEW.submitted_by THEN RAISE EXCEPTION 'only the paper author may submit it' USING ERRCODE='23514'; END IF; "
            "IF NEW.separation_of_duties_required IS DISTINCT FROM org_policy THEN RAISE EXCEPTION 'approval policy snapshot does not match organization policy' USING ERRCODE='23514'; END IF; "
            "SELECT content->'assembly' INTO version_assembly FROM public.paper_versions WHERE id=NEW.paper_version_id AND paper_id=NEW.paper_id; "
            "IF version_assembly IS NULL OR version_assembly IS DISTINCT FROM NEW.coverage_report_snapshot THEN RAISE EXCEPTION 'coverage report snapshot does not match paper version' USING ERRCODE='23514'; END IF; "
            "SELECT paper_id,paper_version_id,artifact_type,status INTO paper_artifact FROM public.export_artifacts WHERE id=NEW.paper_export_artifact_id AND org_id=NEW.org_id; "
            "SELECT paper_id,paper_version_id,artifact_type,status INTO key_artifact FROM public.export_artifacts WHERE id=NEW.answer_key_export_artifact_id AND org_id=NEW.org_id; "
            "IF paper_artifact.paper_id IS DISTINCT FROM NEW.paper_id OR paper_artifact.paper_version_id IS DISTINCT FROM NEW.paper_version_id OR paper_artifact.artifact_type IS DISTINCT FROM 'paper' OR paper_artifact.status IS DISTINCT FROM 'done' THEN RAISE EXCEPTION 'paper export must be a completed artifact for the submitted version' USING ERRCODE='23514'; END IF; "
            "IF key_artifact.paper_id IS DISTINCT FROM NEW.paper_id OR key_artifact.paper_version_id IS DISTINCT FROM NEW.paper_version_id OR key_artifact.artifact_type IS DISTINCT FROM 'answer_key' OR key_artifact.status IS DISTINCT FROM 'done' THEN RAISE EXCEPTION 'answer-key export must be a completed artifact for the submitted version' USING ERRCODE='23514'; END IF; "
            "RETURN NEW; END $$"
        )
    )
    op.execute(sa.text("CREATE TRIGGER trg_approval_request_insert_guard BEFORE INSERT ON public.approval_requests FOR EACH ROW EXECUTE FUNCTION public.validate_approval_request_insert()"))
    op.execute(
        sa.text(
            "CREATE FUNCTION public.enforce_approval_request_transition() RETURNS trigger "
            "LANGUAGE plpgsql AS $$ BEGIN "
            "IF NEW.org_id IS DISTINCT FROM OLD.org_id OR NEW.paper_id IS DISTINCT FROM OLD.paper_id OR NEW.paper_version_id IS DISTINCT FROM OLD.paper_version_id OR NEW.paper_export_artifact_id IS DISTINCT FROM OLD.paper_export_artifact_id OR NEW.answer_key_export_artifact_id IS DISTINCT FROM OLD.answer_key_export_artifact_id OR NEW.submitted_by IS DISTINCT FROM OLD.submitted_by OR NEW.separation_of_duties_required IS DISTINCT FROM OLD.separation_of_duties_required OR NEW.coverage_report_snapshot IS DISTINCT FROM OLD.coverage_report_snapshot OR NEW.coverage_report_sha256 IS DISTINCT FROM OLD.coverage_report_sha256 OR NEW.submitted_at IS DISTINCT FROM OLD.submitted_at OR NEW.created_at IS DISTINCT FROM OLD.created_at THEN RAISE EXCEPTION 'approval request submission facts are immutable' USING ERRCODE='23514'; END IF; "
            "IF NEW.status IS DISTINCT FROM OLD.status THEN "
            "IF NOT ((OLD.status='submitted' AND NEW.status='review') OR (OLD.status='review' AND NEW.status IN ('approved','rejected'))) THEN RAISE EXCEPTION 'invalid approval request transition: % -> %', OLD.status, NEW.status USING ERRCODE='23514'; END IF; "
            "ELSIF NEW.review_started_by IS DISTINCT FROM OLD.review_started_by OR NEW.review_started_at IS DISTINCT FROM OLD.review_started_at OR NEW.approver_id IS DISTINCT FROM OLD.approver_id OR NEW.decided_at IS DISTINCT FROM OLD.decided_at OR NEW.decision_comment IS DISTINCT FROM OLD.decision_comment THEN RAISE EXCEPTION 'approval decision fields may change only with a state transition' USING ERRCODE='23514'; END IF; "
            "IF NEW.row_version <> OLD.row_version + 1 THEN RAISE EXCEPTION 'approval request row_version must increment exactly once' USING ERRCODE='23514'; END IF; "
            "NEW.updated_at := now(); RETURN NEW; END $$"
        )
    )
    op.execute(sa.text("CREATE TRIGGER trg_approval_request_transition BEFORE UPDATE ON public.approval_requests FOR EACH ROW EXECUTE FUNCTION public.enforce_approval_request_transition()"))
    op.execute(
        sa.text(
            "CREATE FUNCTION public.sync_paper_approval_status() RETURNS trigger LANGUAGE plpgsql AS $$ BEGIN "
            "UPDATE public.generated_papers SET status=NEW.status, updated_at=now(), row_version=row_version+1 WHERE id=NEW.paper_id AND org_id=NEW.org_id; RETURN NEW; END $$"
        )
    )
    op.execute(sa.text("CREATE TRIGGER trg_sync_paper_approval_status AFTER INSERT OR UPDATE OF status ON public.approval_requests FOR EACH ROW EXECUTE FUNCTION public.sync_paper_approval_status()"))

    _tenant_table("approval_requests", update=True)
    _tenant_table("approval_request_events", update=False)
    _tenant_table("approval_comments", update=False)


def downgrade() -> None:
    op.execute(sa.text("DROP FUNCTION IF EXISTS public.sync_paper_approval_status() CASCADE"))
    op.execute(sa.text("DROP FUNCTION IF EXISTS public.enforce_approval_request_transition() CASCADE"))
    op.execute(sa.text("DROP FUNCTION IF EXISTS public.validate_approval_request_insert() CASCADE"))
    op.drop_table("approval_comments")
    op.drop_table("approval_request_events")
    op.drop_table("approval_requests")
