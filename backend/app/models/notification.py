"""``notifications`` (doc 03 §5, §9 step 17).

The durable delivery and read-state record behind the NT-1 … NT-15 catalogue in
``01_Product_Requirements.md`` §17. doc 03's own section header notes that this table
"closes a gap" — ``02_System_Architecture.md`` and ``07_Backend_Architecture.md`` both
assign the Notifications module a log table that had no definition until doc 03 gave it
one.

Worth stating plainly: this table is **not** in the P1-T4 task brief, which lists
subscriptions, usage, audit_logs, analytics_daily and prompt_versions for step 9 of its
own numbering and never mentions notifications. It is built because doc 03 §9 makes it
step 17 of the migration order and the brief names doc 03 as the source of truth over
anything discussed in conversation. Reported rather than assumed.

The dedup rule, and the convention it forces
--------------------------------------------
doc 03 §5 specifies ``unique(user_id, dedup_key)`` to enforce "duplicate notifications
are suppressed". Implemented exactly as written — and it has a consequence the document
does not draw out.

Three catalogue entries (NT-13's email, NT-14 and NT-15's "In-app, email") deliver the
*same event* over *two channels*. Two channels means two rows, since ``channel`` is a
scalar column. But two rows for one event and one recipient share a ``user_id``, so they
must not share a ``dedup_key`` — the unique constraint would reject the second delivery
outright, and the failure mode is a silently missing email rather than an error anyone
sees.

So ``dedup_key`` has to incorporate the channel: ``NT-14:membership:<id>:email``, not
``NT-14:membership:<id>``. That is a string-format convention the database cannot
enforce, and it is the one thing to get right when the Notifications module is built.
The alternative — ``unique(user_id, channel, dedup_key)`` — would make the schema
enforce it directly, and is *not* implemented here because doc 03 states the two-column
form explicitly. Flagged for the P1-T5 hand-off.
"""

from __future__ import annotations

import datetime as _dt
import uuid as _uuid
from typing import TYPE_CHECKING, Optional

from sqlalchemy import (
    DateTime,
    ForeignKey,
    Index,
    Integer,
    Text,
    UniqueConstraint,
    func,
    text,
)
from sqlalchemy.dialects.postgresql import JSONB, UUID
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.db.base import Base, TimestampMixin, UUIDPrimaryKeyMixin, check_in

if TYPE_CHECKING:  # pragma: no cover - typing only
    from app.models.organization import Organization
    from app.models.user import User

#: The notification catalogue of ``01_Product_Requirements.md`` §17, which runs NT-1 to
#: NT-15 with no gaps. Written out one by one rather than generated from ``range(1, 16)``
#: so that the vocabulary is greppable and extending the catalogue shows up as a visible
#: diff in both the model and the CHECK constraint — the same frozen-literal discipline
#: used for every other controlled vocabulary in this package (ADR-011).
NOTIFICATION_CODES: tuple[str, ...] = (
    "NT-1",
    "NT-2",
    "NT-3",
    "NT-4",
    "NT-5",
    "NT-6",
    "NT-7",
    "NT-8",
    "NT-9",
    "NT-10",
    "NT-11",
    "NT-12",
    "NT-13",
    "NT-14",
    "NT-15",
)

#: doc 03 §5: constrained to ``in_app`` / ``email``, with ``sms`` and ``push``
#: "reserved, not implemented". Reserved values are deliberately **not** in the CHECK:
#: a constraint that admits a channel with no delivery path would let rows accumulate
#: in ``pending`` forever with nothing to send them, and "the database accepted it"
#: is the worst possible reason to believe a notification went out.
NOTIFICATION_CHANNELS: tuple[str, ...] = ("in_app", "email")

#: doc 03 §5: ``pending`` / ``sent`` / ``failed``. Note that this tracks *delivery*,
#: while ``read_at`` tracks receipt — an in-app notification is ``sent`` the moment it
#: is queryable, and stays unread until the recipient opens it.
NOTIFICATION_STATUSES: tuple[str, ...] = ("pending", "sent", "failed")

NOTIFICATION_EVENT_SOURCES: tuple[str, ...] = (
    "approval_request_events",
    "domain_event",
)
NOTIFICATION_EVENT_STATUSES: tuple[str, ...] = (
    "pending",
    "processing",
    "completed",
    "ignored",
    "failed",
)
NOTIFICATION_DIGEST_STATUSES: tuple[str, ...] = ("open", "delivered")


class Notification(UUIDPrimaryKeyMixin, TimestampMixin, Base):
    """One delivery of one catalogue event to one recipient.

    Both foreign keys are CASCADE, which is the only place in this schema where doc 03
    §5 states the rule outright rather than leaving it to be inferred: "org and user
    deletion cascade only in hard-delete maintenance scenarios". Easy to justify — a
    notification is addressed *to* a person, so it has no meaning once that person is
    gone, and unlike ``approvals`` it is not evidence of anything.

    No ``deleted_at`` and no ``row_version``, per doc 03 §5. Dismissing a notification
    is ``read_at``, not a tombstone; and the only mutation a row ever receives is a
    status transition or a read stamp, neither of which two writers race over in a way
    optimistic locking would help.
    """

    __tablename__ = "notifications"

    org_id: Mapped[_uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("organizations.id", ondelete="CASCADE"),
        nullable=False,
    )
    # Required, unlike every other user reference in the schema: a notification with no
    # recipient is not a notification. This is also why there is no "broadcast" row —
    # announcing something to a whole organization means one row per member, which is
    # what makes per-recipient read state possible at all.
    user_id: Mapped[_uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("users.id", ondelete="CASCADE"),
        nullable=False,
    )
    code: Mapped[str] = mapped_column(Text, nullable=False)
    channel: Mapped[str] = mapped_column(Text, nullable=False)
    # Rendered text, not a template reference. Deliberate and per doc 03 §5: a
    # notification says what was true when it fired, so re-rendering it later against
    # current data would rewrite history ("Paper X was rejected" must not become
    # "Paper Y was approved" because the paper moved on).
    title: Mapped[str] = mapped_column(Text, nullable=False)
    body: Mapped[Optional[str]] = mapped_column(Text, nullable=True)
    # The S-15 deep link. Stored as written by the producer; nullable because a purely
    # informational notice has nowhere to go.
    link: Mapped[Optional[str]] = mapped_column(Text, nullable=True)
    # Polymorphic subject, same shape and same no-FK reasoning as `audit_logs`.
    entity: Mapped[Optional[str]] = mapped_column(Text, nullable=True)
    entity_id: Mapped[Optional[_uuid.UUID]] = mapped_column(
        UUID(as_uuid=True),
        nullable=True,
    )
    # Required, and the reason this table can be written from a retrying Celery task
    # without double-delivering: the producer computes a deterministic key from the
    # event, and a redelivery collides instead of arriving twice. See the module
    # docstring for the channel-in-the-key rule.
    dedup_key: Mapped[str] = mapped_column(Text, nullable=False)
    status: Mapped[str] = mapped_column(
        Text,
        nullable=False,
        server_default=text("'pending'"),
    )
    attempt_count: Mapped[int] = mapped_column(
        Integer, nullable=False, server_default=text("0")
    )
    last_error: Mapped[Optional[str]] = mapped_column(Text, nullable=True)
    sent_at: Mapped[Optional[_dt.datetime]] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
    read_at: Mapped[Optional[_dt.datetime]] = mapped_column(
        DateTime(timezone=True),
        nullable=True,
    )

    organization: Mapped["Organization"] = relationship(
        "Organization",
        back_populates="notifications",
    )
    user: Mapped["User"] = relationship("User", back_populates="notifications")

    __table_args__ = (
        check_in("code", NOTIFICATION_CODES, name="code"),
        check_in("channel", NOTIFICATION_CHANNELS, name="channel"),
        check_in("status", NOTIFICATION_STATUSES, name="status"),
        # A plain UNIQUE constraint, not a partial index: this table has no
        # `deleted_at`, so ADR-004's `WHERE deleted_at IS NULL` predicate has nothing to
        # attach to. Same situation as `paper_versions`.
        UniqueConstraint("user_id", "dedup_key", name="uq_notifications_user_id_dedup_key"),
        # doc 03 §5's four indexes. The first two are the panel and admin feeds.
        Index("ix_notifications_user_id_created_at", "user_id", "created_at"),
        Index("ix_notifications_org_id_created_at", "org_id", "created_at"),
        # The unread-count index. doc 03 names both columns and the predicate; keeping
        # `read_at` as the second key column is literal-minded rather than useful, since
        # under `read_at IS NULL` every entry holds the same NULL and the column can
        # neither narrow nor order anything. Implemented as specified and noted: the
        # index earns its keep through the *predicate*, which keeps it proportional to
        # the unread backlog instead of the full history.
        Index(
            "ix_notifications_user_id_read_at",
            "user_id",
            "read_at",
            postgresql_where=text("read_at IS NULL"),
        ),
        # Not redundant with the unique constraint above, whose leading column is
        # `user_id`: this one answers "has this event already been delivered to
        # anyone", which is how a fan-out producer avoids re-queueing a whole
        # organization's worth of notifications after a partial failure.
        Index("ix_notifications_dedup_key", "dedup_key"),
    )

    def __repr__(self) -> str:  # pragma: no cover - debugging aid
        return (
            f"<Notification id={self.id!r} user_id={self.user_id!r} "
            f"code={self.code!r} channel={self.channel!r} status={self.status!r}>"
        )


class NotificationEventReceipt(UUIDPrimaryKeyMixin, TimestampMixin, Base):
    """Notifications-owned inbox receipt for an at-least-once domain event.

    The unique source/event pair is the durable dedup boundary. Workers additionally
    lock this row before fan-out: the unique key prevents duplicate records, while the
    row lock prevents two concurrently executing Celery deliveries from performing the
    same side effects (R-18).
    """

    __tablename__ = "notification_event_receipts"

    org_id: Mapped[_uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("organizations.id", ondelete="CASCADE"),
        nullable=False,
    )
    source: Mapped[str] = mapped_column(Text, nullable=False)
    source_event_id: Mapped[_uuid.UUID] = mapped_column(UUID(as_uuid=True), nullable=False)
    event_type: Mapped[str] = mapped_column(Text, nullable=False)
    payload: Mapped[dict] = mapped_column(
        JSONB, nullable=False, server_default=text("'{}'::jsonb")
    )
    status: Mapped[str] = mapped_column(
        Text, nullable=False, server_default=text("'pending'")
    )
    available_at: Mapped[_dt.datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )
    locked_at: Mapped[Optional[_dt.datetime]] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
    completed_at: Mapped[Optional[_dt.datetime]] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
    attempt_count: Mapped[int] = mapped_column(
        Integer, nullable=False, server_default=text("0")
    )
    last_error: Mapped[Optional[str]] = mapped_column(Text, nullable=True)

    __table_args__ = (
        check_in("source", NOTIFICATION_EVENT_SOURCES, name="source"),
        check_in("status", NOTIFICATION_EVENT_STATUSES, name="status"),
        UniqueConstraint(
            "source", "source_event_id", name="uq_notification_event_receipts_source_event"
        ),
        Index(
            "ix_notification_event_receipts_status_available",
            "status",
            "available_at",
        ),
        Index(
            "ix_notification_event_receipts_org_created",
            "org_id",
            "created_at",
        ),
    )


class NotificationDigest(UUIDPrimaryKeyMixin, TimestampMixin, Base):
    """One five-minute aggregation bucket for bulk catalogue notifications."""

    __tablename__ = "notification_digests"

    org_id: Mapped[_uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("organizations.id", ondelete="CASCADE"),
        nullable=False,
    )
    user_id: Mapped[_uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("users.id", ondelete="CASCADE"), nullable=False
    )
    code: Mapped[str] = mapped_column(Text, nullable=False)
    aggregation_key: Mapped[str] = mapped_column(Text, nullable=False)
    window_start: Mapped[_dt.datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    window_end: Mapped[_dt.datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    event_count: Mapped[int] = mapped_column(
        Integer, nullable=False, server_default=text("0")
    )
    status: Mapped[str] = mapped_column(
        Text, nullable=False, server_default=text("'open'")
    )
    delivered_at: Mapped[Optional[_dt.datetime]] = mapped_column(
        DateTime(timezone=True), nullable=True
    )

    __table_args__ = (
        check_in("code", NOTIFICATION_CODES, name="code"),
        check_in("status", NOTIFICATION_DIGEST_STATUSES, name="status"),
        UniqueConstraint(
            "user_id",
            "code",
            "aggregation_key",
            "window_start",
            name="uq_notification_digests_bucket",
        ),
        Index(
            "ix_notification_digests_due",
            "status",
            "window_end",
        ),
        Index(
            "ix_notification_digests_org_created", "org_id", "created_at"
        ),
    )


__all__ = [
    "NOTIFICATION_CHANNELS",
    "NOTIFICATION_CODES",
    "NOTIFICATION_DIGEST_STATUSES",
    "NOTIFICATION_EVENT_SOURCES",
    "NOTIFICATION_EVENT_STATUSES",
    "NOTIFICATION_STATUSES",
    "Notification",
    "NotificationDigest",
    "NotificationEventReceipt",
]
