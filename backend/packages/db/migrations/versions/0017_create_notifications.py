"""create notifications (doc 03 §9 step 17)

Revision ID: 0017
Revises: 0016
Create Date: 2026-08-17

doc 03 §9 step 17: "Create notifications." The durable delivery and read-state record
behind the NT-1 … NT-15 catalogue in ``01_Product_Requirements.md`` §17.

Worth stating plainly: **this table is not in the P1-T4 task brief.** The brief's own
item 9 lists subscriptions, usage, audit_logs, analytics_daily and prompt_versions, and
never mentions notifications. It is built because doc 03 §9 makes it step 17 of the
migration order and the brief names doc 03 as the source of truth over anything discussed
in conversation. Reported rather than assumed — doc 03's own section header says this
table "closes a gap": ``02_System_Architecture.md`` and ``07_Backend_Architecture.md``
both assign the Notifications module a log table that had no definition until doc 03 gave
it one.

Every literal is frozen on purpose — see revision 0003's docstring.

Notes on shape
--------------
* **Both foreign keys are CASCADE, and this is the only place doc 03 §5 states a cascade
  rule for both sides outright** ("org and user deletion cascade only in hard-delete
  maintenance scenarios") rather than leaving it to be inferred. Easy to justify: a
  notification is addressed *to* a person, so it has no meaning once that person is gone,
  and unlike ``approvals`` it is not evidence of anything.
* **``user_id`` is required**, unlike every other user reference in the schema — a
  notification with no recipient is not a notification. This is also why there is no
  "broadcast" row: announcing something to a whole organization means one row per member,
  which is what makes per-recipient read state possible at all.
* **``title`` and ``body`` hold rendered text, not a template reference**, per doc 03 §5.
  Deliberate: a notification says what was true when it fired, so re-rendering it later
  against current data would rewrite history — "Paper X was rejected" must not become
  "Paper X was approved" because the paper moved on.
* **``entity`` / ``entity_id`` are a polymorphic subject with no FK**, same shape and same
  reasoning as ``audit_logs`` in revision 0014.
* **``status`` tracks delivery; ``read_at`` tracks receipt.** An in-app notification is
  ``sent`` the moment it is queryable and stays unread until the recipient opens it.
* **No ``deleted_at``, no ``row_version``**, per doc 03 §5. Dismissing a notification is
  ``read_at``, not a tombstone, and the only mutations a row receives are a status
  transition and a read stamp — neither of which two writers race over in a way
  optimistic locking would help. As on ``paper_versions``, the absence of ``deleted_at``
  is why ``uq_notifications_user_id_dedup_key`` is a plain UNIQUE rather than a partial
  index: there is no predicate to attach.
* **``channel`` admits ``in_app`` and ``email`` only.** doc 03 §5 calls ``sms`` and
  ``push`` "reserved, not implemented", and reserved values are deliberately kept out of
  the CHECK: a constraint that admits a channel with no delivery path would let rows
  accumulate in ``pending`` forever with nothing to send them, and "the database accepted
  it" is the worst possible reason to believe a notification went out. Same posture
  revision 0002 took toward the reserved ``hod`` / ``reviewer`` / ``viewer`` roles.
* **``ix_notifications_user_id_read_at`` keeps ``read_at`` as its second key column**,
  which doc 03 §5 specifies and which is literal-minded rather than useful: under
  ``WHERE read_at IS NULL`` every entry holds the same NULL, so the column can neither
  narrow nor order anything. Created as specified, and noted — the index earns its keep
  through the *predicate*, which keeps it proportional to the unread backlog instead of
  the full history.
* **``ix_notifications_dedup_key`` is not redundant** with the unique constraint, whose
  leading column is ``user_id``: this one answers "has this event already been delivered
  to anyone", which is how a fan-out producer avoids re-queueing a whole organization's
  worth of notifications after a partial failure.

Open item, reported not resolved
--------------------------------
doc 03 §5 specifies ``unique(user_id, dedup_key)`` to enforce "duplicate notifications are
suppressed". Implemented exactly as written — and it has a consequence the document does
not draw out.

Three catalogue entries (NT-13's email, and NT-14 and NT-15's "In-app, email") deliver the
*same event* over *two channels*. Two channels means two rows, because ``channel`` is a
scalar column. But two rows for one event and one recipient share a ``user_id``, so they
must not share a ``dedup_key`` — this constraint would reject the second delivery
outright, and the failure mode is a silently missing email rather than an error anyone
sees.

So ``dedup_key`` has to incorporate the channel: ``NT-14:membership:<id>:email``, not
``NT-14:membership:<id>``. That is a string-format convention the database cannot enforce,
and it is the one thing to get right when the Notifications module is built. The
alternative — ``unique(user_id, channel, dedup_key)`` — would make the schema enforce it
directly, and is **not** implemented here because doc 03 states the two-column form
explicitly. Flagged for the P1-T5 hand-off.
"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

# revision identifiers, used by Alembic.
revision: str = "0017"
down_revision: Union[str, Sequence[str], None] = "0016"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None

_UNREAD_PREDICATE = "read_at IS NULL"


def upgrade() -> None:
    """Upgrade schema."""

    op.create_table(
        "notifications",
        sa.Column(
            "id",
            postgresql.UUID(as_uuid=True),
            server_default=sa.text("gen_random_uuid()"),
            nullable=False,
        ),
        sa.Column("org_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("user_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("code", sa.Text(), nullable=False),
        sa.Column("channel", sa.Text(), nullable=False),
        sa.Column("title", sa.Text(), nullable=False),
        sa.Column("body", sa.Text(), nullable=True),
        sa.Column("link", sa.Text(), nullable=True),
        sa.Column("entity", sa.Text(), nullable=True),
        sa.Column("entity_id", postgresql.UUID(as_uuid=True), nullable=True),
        sa.Column("dedup_key", sa.Text(), nullable=False),
        sa.Column("status", sa.Text(), server_default=sa.text("'pending'"), nullable=False),
        sa.Column("read_at", sa.DateTime(timezone=True), nullable=True),
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
        # The NT-1 … NT-15 catalogue of `01_Product_Requirements.md` §17, which runs with
        # no gaps. Written out one by one rather than generated, so that extending the
        # catalogue shows up as a visible diff in both the model and this constraint.
        sa.CheckConstraint(
            "code IN ('NT-1', 'NT-2', 'NT-3', 'NT-4', 'NT-5', 'NT-6', 'NT-7', 'NT-8', "
            "'NT-9', 'NT-10', 'NT-11', 'NT-12', 'NT-13', 'NT-14', 'NT-15')",
            name=op.f("ck_notifications_code"),
        ),
        sa.CheckConstraint(
            "channel IN ('in_app', 'email')",
            name=op.f("ck_notifications_channel"),
        ),
        sa.CheckConstraint(
            "status IN ('pending', 'sent', 'failed')",
            name=op.f("ck_notifications_status"),
        ),
        sa.ForeignKeyConstraint(
            ["org_id"],
            ["organizations.id"],
            name=op.f("fk_notifications_org_id_organizations"),
            ondelete="CASCADE",
        ),
        sa.ForeignKeyConstraint(
            ["user_id"],
            ["users.id"],
            name=op.f("fk_notifications_user_id_users"),
            ondelete="CASCADE",
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_notifications")),
        # A plain UNIQUE, not a partial index — this table has no `deleted_at`. See the
        # module docstring for the channel-in-the-key consequence.
        sa.UniqueConstraint(
            "user_id",
            "dedup_key",
            name=op.f("uq_notifications_user_id_dedup_key"),
        ),
    )
    # doc 03 §5's four indexes. The first two are the notification panel and admin feeds.
    op.create_index(op.f("ix_notifications_dedup_key"), "notifications", ["dedup_key"])
    op.create_index(
        op.f("ix_notifications_org_id_created_at"),
        "notifications",
        ["org_id", "created_at"],
    )
    op.create_index(
        op.f("ix_notifications_user_id_created_at"),
        "notifications",
        ["user_id", "created_at"],
    )
    # The unread-count index: partial, per doc 03 §5.
    op.create_index(
        op.f("ix_notifications_user_id_read_at"),
        "notifications",
        ["user_id", "read_at"],
        postgresql_where=sa.text(_UNREAD_PREDICATE),
    )


def downgrade() -> None:
    """Downgrade schema.

    Fully reversible: one table created from nothing, so dropping it restores revision
    0016's state exactly.
    """

    op.drop_index(op.f("ix_notifications_user_id_read_at"), table_name="notifications")
    op.drop_index(op.f("ix_notifications_user_id_created_at"), table_name="notifications")
    op.drop_index(op.f("ix_notifications_org_id_created_at"), table_name="notifications")
    op.drop_index(op.f("ix_notifications_dedup_key"), table_name="notifications")
    op.drop_table("notifications")
