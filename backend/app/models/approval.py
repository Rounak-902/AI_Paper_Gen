"""P6-T4 approval requests, transitions, and question-anchored comments."""

from __future__ import annotations

import datetime as dt
import uuid
from typing import Any, Optional

from sqlalchemy import (
    Boolean,
    CheckConstraint,
    DateTime,
    ForeignKey,
    ForeignKeyConstraint,
    Index,
    Text,
    UniqueConstraint,
    func,
    text,
)
from sqlalchemy.dialects.postgresql import JSONB, UUID
from sqlalchemy.orm import Mapped, mapped_column

from app.db.base import (
    Base,
    RowVersionMixin,
    SoftDeleteMixin,
    TimestampMixin,
    UUIDPrimaryKeyMixin,
    check_in,
    row_version_positive,
)


APPROVAL_REQUEST_STATUSES = ("submitted", "review", "approved", "rejected")
APPROVAL_EVENT_ACTIONS = (
    "submitted",
    "review_started",
    "approved",
    "rejected",
    "approval_withdrawn",
    "question_commented",
)


class ApprovalRequest(
    UUIDPrimaryKeyMixin,
    TimestampMixin,
    SoftDeleteMixin,
    RowVersionMixin,
    Base,
):
    """One review round for exactly one immutable paper version and artifact pair.

    Approved and rejected rows are terminal. A later round is a new row, preserving
    ADR-004's audit history instead of reopening and rewriting a prior decision.
    """

    __tablename__ = "approval_requests"

    org_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("organizations.id", ondelete="CASCADE"),
        nullable=False,
    )
    paper_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("generated_papers.id", ondelete="CASCADE"),
        nullable=False,
    )
    paper_version_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("paper_versions.id", ondelete="RESTRICT"),
        nullable=False,
    )
    paper_export_artifact_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("export_artifacts.id", ondelete="RESTRICT"),
        nullable=False,
    )
    answer_key_export_artifact_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("export_artifacts.id", ondelete="RESTRICT"),
        nullable=False,
    )
    submitted_by: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("users.id", ondelete="RESTRICT"),
        nullable=False,
    )
    review_started_by: Mapped[Optional[uuid.UUID]] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("users.id", ondelete="RESTRICT"),
        nullable=True,
    )
    approver_id: Mapped[Optional[uuid.UUID]] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("users.id", ondelete="RESTRICT"),
        nullable=True,
    )
    status: Mapped[str] = mapped_column(
        Text, nullable=False, server_default=text("'submitted'")
    )
    separation_of_duties_required: Mapped[bool] = mapped_column(
        Boolean, nullable=False, server_default=text("true")
    )
    coverage_report_snapshot: Mapped[dict[str, Any]] = mapped_column(
        JSONB, nullable=False
    )
    coverage_report_sha256: Mapped[str] = mapped_column(Text, nullable=False)
    submitted_at: Mapped[dt.datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )
    review_started_at: Mapped[Optional[dt.datetime]] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
    decided_at: Mapped[Optional[dt.datetime]] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
    decision_comment: Mapped[Optional[str]] = mapped_column(Text, nullable=True)

    __table_args__ = (
        check_in("status", APPROVAL_REQUEST_STATUSES, name="status"),
        row_version_positive(),
        CheckConstraint(
            "char_length(coverage_report_sha256) = 64",
            name="coverage_report_sha256_length",
        ),
        CheckConstraint(
            "NOT separation_of_duties_required OR review_started_by IS NULL "
            "OR review_started_by <> submitted_by",
            name="reviewer_differs_from_submitter",
        ),
        CheckConstraint(
            "NOT separation_of_duties_required OR approver_id IS NULL "
            "OR approver_id <> submitted_by",
            name="approver_differs_from_submitter",
        ),
        CheckConstraint(
            "(status = 'submitted' AND review_started_by IS NULL "
            "AND review_started_at IS NULL AND approver_id IS NULL AND decided_at IS NULL) "
            "OR (status = 'review' AND review_started_by IS NOT NULL "
            "AND review_started_at IS NOT NULL AND approver_id IS NULL AND decided_at IS NULL) "
            "OR (status IN ('approved','rejected') AND review_started_by IS NOT NULL "
            "AND review_started_at IS NOT NULL AND approver_id IS NOT NULL "
            "AND decided_at IS NOT NULL)",
            name="state_fields",
        ),
        CheckConstraint(
            "status <> 'rejected' OR nullif(btrim(decision_comment), '') IS NOT NULL",
            name="rejection_comment_required",
        ),
        UniqueConstraint(
            "paper_version_id", name="uq_approval_requests_paper_version_id"
        ),
        UniqueConstraint(
            "id", "paper_version_id", name="uq_approval_requests_id_version"
        ),
        Index("ix_approval_requests_org_status", "org_id", "status", "submitted_at"),
        Index("ix_approval_requests_paper_created", "paper_id", "created_at"),
        Index(
            "uq_approval_requests_one_pending_per_paper",
            "paper_id",
            unique=True,
            postgresql_where=text(
                "status IN ('submitted','review') AND deleted_at IS NULL"
            ),
        ),
    )


class ApprovalRequestEvent(UUIDPrimaryKeyMixin, Base):
    """Append-only transition event; P6-T5 can consume this durable boundary."""

    __tablename__ = "approval_request_events"

    org_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("organizations.id", ondelete="CASCADE"),
        nullable=False,
    )
    approval_request_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("approval_requests.id", ondelete="CASCADE"),
        nullable=False,
    )
    paper_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("generated_papers.id", ondelete="CASCADE"),
        nullable=False,
    )
    paper_version_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("paper_versions.id", ondelete="RESTRICT"),
        nullable=False,
    )
    actor_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("users.id", ondelete="RESTRICT"), nullable=False
    )
    action: Mapped[str] = mapped_column(Text, nullable=False)
    from_status: Mapped[Optional[str]] = mapped_column(Text, nullable=True)
    to_status: Mapped[str] = mapped_column(Text, nullable=False)
    comment: Mapped[Optional[str]] = mapped_column(Text, nullable=True)
    occurred_at: Mapped[dt.datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )

    __table_args__ = (
        check_in("action", APPROVAL_EVENT_ACTIONS, name="action"),
        Index(
            "ix_approval_request_events_request_time",
            "approval_request_id",
            "occurred_at",
        ),
        Index(
            "uq_approval_request_events_one_withdrawal",
            "approval_request_id",
            unique=True,
            postgresql_where=text("action = 'approval_withdrawn'"),
        ),
    )


class ApprovalComment(UUIDPrimaryKeyMixin, Base):
    """Append-only reviewer feedback, optionally anchored to a stable slot id."""

    __tablename__ = "approval_comments"

    org_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("organizations.id", ondelete="CASCADE"),
        nullable=False,
    )
    approval_request_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), nullable=False
    )
    paper_version_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), nullable=False
    )
    created_by: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("users.id", ondelete="RESTRICT"), nullable=False
    )
    slot_id: Mapped[Optional[str]] = mapped_column(Text, nullable=True)
    body: Mapped[str] = mapped_column(Text, nullable=False)
    created_at: Mapped[dt.datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )

    __table_args__ = (
        ForeignKeyConstraint(
            ["approval_request_id", "paper_version_id"],
            ["approval_requests.id", "approval_requests.paper_version_id"],
            ondelete="CASCADE",
        ),
        CheckConstraint("nullif(btrim(body), '') IS NOT NULL", name="body_not_blank"),
        Index(
            "ix_approval_comments_request_time",
            "approval_request_id",
            "created_at",
        ),
    )


__all__ = [
    "APPROVAL_EVENT_ACTIONS",
    "APPROVAL_REQUEST_STATUSES",
    "ApprovalComment",
    "ApprovalRequest",
    "ApprovalRequestEvent",
]
