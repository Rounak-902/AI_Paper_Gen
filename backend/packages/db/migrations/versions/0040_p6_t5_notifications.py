"""P6-T5 notification inbox, delivery state, and digest buckets.

Revision ID: 0040
Revises: 0039
Create Date: 2026-09-27
"""

from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "0040"
down_revision: Union[str, Sequence[str], None] = "0039"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def _tenant_table(table: str) -> None:
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
    op.execute(sa.text(f"GRANT SELECT, INSERT, UPDATE ON TABLE public.{table} TO app_user"))
    op.execute(sa.text(f"REVOKE DELETE ON TABLE public.{table} FROM app_user"))


def upgrade() -> None:
    op.add_column(
        "notifications",
        sa.Column("attempt_count", sa.Integer(), server_default=sa.text("0"), nullable=False),
    )
    op.add_column("notifications", sa.Column("last_error", sa.Text(), nullable=True))
    op.add_column(
        "notifications", sa.Column("sent_at", sa.DateTime(timezone=True), nullable=True)
    )

    op.create_table(
        "notification_event_receipts",
        sa.Column(
            "id",
            postgresql.UUID(as_uuid=True),
            server_default=sa.text("gen_random_uuid()"),
            nullable=False,
        ),
        sa.Column("org_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("source", sa.Text(), nullable=False),
        sa.Column("source_event_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("event_type", sa.Text(), nullable=False),
        sa.Column(
            "payload",
            postgresql.JSONB(astext_type=sa.Text()),
            server_default=sa.text("'{}'::jsonb"),
            nullable=False,
        ),
        sa.Column("status", sa.Text(), server_default=sa.text("'pending'"), nullable=False),
        sa.Column(
            "available_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.Column("locked_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("completed_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("attempt_count", sa.Integer(), server_default=sa.text("0"), nullable=False),
        sa.Column("last_error", sa.Text(), nullable=True),
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
            "source IN ('approval_request_events','domain_event')",
            name=op.f("ck_notification_event_receipts_source"),
        ),
        sa.CheckConstraint(
            "status IN ('pending','processing','completed','ignored','failed')",
            name=op.f("ck_notification_event_receipts_status"),
        ),
        sa.ForeignKeyConstraint(
            ["org_id"],
            ["organizations.id"],
            name=op.f("fk_notification_event_receipts_org_id_organizations"),
            ondelete="CASCADE",
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_notification_event_receipts")),
        sa.UniqueConstraint(
            "source",
            "source_event_id",
            name=op.f("uq_notification_event_receipts_source_event"),
        ),
    )
    op.create_index(
        "ix_notification_event_receipts_status_available",
        "notification_event_receipts",
        ["status", "available_at"],
    )
    op.create_index(
        "ix_notification_event_receipts_org_created",
        "notification_event_receipts",
        ["org_id", "created_at"],
    )

    op.create_table(
        "notification_digests",
        sa.Column(
            "id",
            postgresql.UUID(as_uuid=True),
            server_default=sa.text("gen_random_uuid()"),
            nullable=False,
        ),
        sa.Column("org_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("user_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("code", sa.Text(), nullable=False),
        sa.Column("aggregation_key", sa.Text(), nullable=False),
        sa.Column("window_start", sa.DateTime(timezone=True), nullable=False),
        sa.Column("window_end", sa.DateTime(timezone=True), nullable=False),
        sa.Column("event_count", sa.Integer(), server_default=sa.text("0"), nullable=False),
        sa.Column("status", sa.Text(), server_default=sa.text("'open'"), nullable=False),
        sa.Column("delivered_at", sa.DateTime(timezone=True), nullable=True),
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
            "code IN ('NT-1','NT-2','NT-3','NT-4','NT-5','NT-6','NT-7','NT-8','NT-9','NT-10','NT-11','NT-12','NT-13','NT-14','NT-15')",
            name=op.f("ck_notification_digests_code"),
        ),
        sa.CheckConstraint(
            "status IN ('open','delivered')",
            name=op.f("ck_notification_digests_status"),
        ),
        sa.ForeignKeyConstraint(
            ["org_id"],
            ["organizations.id"],
            name=op.f("fk_notification_digests_org_id_organizations"),
            ondelete="CASCADE",
        ),
        sa.ForeignKeyConstraint(
            ["user_id"],
            ["users.id"],
            name=op.f("fk_notification_digests_user_id_users"),
            ondelete="CASCADE",
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_notification_digests")),
        sa.UniqueConstraint(
            "user_id",
            "code",
            "aggregation_key",
            "window_start",
            name=op.f("uq_notification_digests_bucket"),
        ),
    )
    op.create_index(
        "ix_notification_digests_due",
        "notification_digests",
        ["status", "window_end"],
    )
    op.create_index(
        "ix_notification_digests_org_created",
        "notification_digests",
        ["org_id", "created_at"],
    )

    _tenant_table("notification_event_receipts")
    _tenant_table("notification_digests")
    op.execute(sa.text("GRANT UPDATE ON TABLE public.notifications TO app_user"))

    # Deployment must not send a burst for historical approval decisions. Mark the
    # pre-existing catalogue-mapped transitions as an explicit consumed baseline.
    op.execute(
        sa.text(
            "INSERT INTO public.notification_event_receipts "
            "(org_id,source,source_event_id,event_type,status,completed_at) "
            "SELECT org_id,'approval_request_events',id,"
            "CASE action WHEN 'submitted' THEN 'paper.submitted' "
            "WHEN 'approved' THEN 'paper.approved' ELSE 'paper.rejected' END,"
            "'ignored',now() FROM public.approval_request_events "
            "WHERE action IN ('submitted','approved','rejected') ON CONFLICT DO NOTHING"
        )
    )

    # Narrow cross-tenant subscriber boundary. The worker receives only notification
    # receipt/org IDs; all entity reads and writes then occur in a tenant-scoped session.
    op.execute(
        sa.text(
            "CREATE FUNCTION public.claim_approval_notification_events(p_limit integer DEFAULT 100, p_max_attempts integer DEFAULT 4) "
            "RETURNS TABLE(receipt_id uuid, org_id uuid) LANGUAGE plpgsql SECURITY DEFINER "
            "SET search_path = pg_catalog, public AS $$ BEGIN "
            "INSERT INTO public.notification_event_receipts "
            "(org_id,source,source_event_id,event_type,status) "
            "SELECT e.org_id,'approval_request_events',e.id,"
            "CASE e.action WHEN 'submitted' THEN 'paper.submitted' "
            "WHEN 'approved' THEN 'paper.approved' ELSE 'paper.rejected' END,'pending' "
            "FROM public.approval_request_events e "
            "WHERE e.action IN ('submitted','approved','rejected') "
            "ON CONFLICT (source,source_event_id) DO NOTHING; "
            "RETURN QUERY WITH candidates AS ("
            "SELECT r.id FROM public.notification_event_receipts r "
            "WHERE r.source='approval_request_events' AND "
            "((r.status IN ('pending','failed') AND r.available_at <= clock_timestamp()) OR "
            "(r.status='processing' AND r.locked_at < clock_timestamp() - interval '2 minutes')) "
            "AND r.attempt_count < greatest(1,p_max_attempts) "
            "ORDER BY r.created_at,r.id FOR UPDATE SKIP LOCKED LIMIT greatest(1,least(p_limit,500))"
            "), leased AS (UPDATE public.notification_event_receipts r "
            "SET status='processing',locked_at=clock_timestamp(),attempt_count=r.attempt_count+1,updated_at=clock_timestamp() "
            "FROM candidates c WHERE r.id=c.id RETURNING r.id,r.org_id) "
            "SELECT leased.id,leased.org_id FROM leased; END $$"
        )
    )
    op.execute(sa.text("REVOKE ALL ON FUNCTION public.claim_approval_notification_events(integer,integer) FROM PUBLIC"))
    op.execute(sa.text("GRANT EXECUTE ON FUNCTION public.claim_approval_notification_events(integer,integer) TO app_user"))
    op.execute(
        sa.text(
            "CREATE FUNCTION public.pending_notification_work(p_limit integer DEFAULT 100, p_digest_threshold integer DEFAULT 10) "
            "RETURNS TABLE(work_kind text, work_id uuid, org_id uuid) LANGUAGE sql SECURITY DEFINER "
            "SET search_path = pg_catalog, public AS $$ "
            "(SELECT 'digest'::text,d.id,d.org_id FROM public.notification_digests d "
            "WHERE d.status='open' AND d.window_end <= clock_timestamp() AND d.event_count >= greatest(1,p_digest_threshold) "
            "ORDER BY d.window_end,d.id LIMIT greatest(1,least(p_limit,500))) UNION ALL "
            "(SELECT 'email'::text,n.id,n.org_id FROM public.notifications n "
            "WHERE n.channel='email' AND n.status='pending' "
            "ORDER BY n.created_at,n.id LIMIT greatest(1,least(p_limit,500))) $$"
        )
    )
    op.execute(sa.text("REVOKE ALL ON FUNCTION public.pending_notification_work(integer,integer) FROM PUBLIC"))
    op.execute(sa.text("GRANT EXECUTE ON FUNCTION public.pending_notification_work(integer,integer) TO app_user"))


def downgrade() -> None:
    op.execute(
        sa.text(
            "DROP FUNCTION IF EXISTS public.pending_notification_work(integer,integer)"
        )
    )
    op.execute(
        sa.text(
            "DROP FUNCTION IF EXISTS public.claim_approval_notification_events(integer,integer)"
        )
    )
    # Compatibility for local databases that briefly ran the development form of
    # this revision before the bounded-attempt parameter was added.
    op.execute(
        sa.text(
            "DROP FUNCTION IF EXISTS public.claim_approval_notification_events(integer)"
        )
    )
    op.drop_index("ix_notification_digests_org_created", table_name="notification_digests")
    op.drop_index("ix_notification_digests_due", table_name="notification_digests")
    op.drop_table("notification_digests")
    op.drop_index(
        "ix_notification_event_receipts_org_created",
        table_name="notification_event_receipts",
    )
    op.drop_index(
        "ix_notification_event_receipts_status_available",
        table_name="notification_event_receipts",
    )
    op.drop_table("notification_event_receipts")
    op.drop_column("notifications", "sent_at")
    op.drop_column("notifications", "last_error")
    op.drop_column("notifications", "attempt_count")
