"""Durable P4-T5 paper-assembly checkpoints and transactional task outbox."""

from __future__ import annotations

import datetime as dt
import uuid
from typing import Any, Optional

from sqlalchemy import (
    CheckConstraint,
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
from sqlalchemy.orm import Mapped, mapped_column

from app.db.base import Base, TimestampMixin, UUIDPrimaryKeyMixin


ASSEMBLY_RUN_STATUSES = ("planned", "running", "finalizing", "completed", "failed")
ASSEMBLY_SLOT_STATUSES = ("pending", "running", "completed", "failed")
OUTBOX_STATUSES = ("pending", "published")


class PaperAssemblyRun(UUIDPrimaryKeyMixin, TimestampMixin, Base):
    """One resumable attempt to produce a named immutable paper version."""

    __tablename__ = "paper_assembly_runs"

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
    label: Mapped[str] = mapped_column(Text, nullable=False)
    status: Mapped[str] = mapped_column(
        Text, nullable=False, server_default=text("'planned'")
    )
    solver_snapshot: Mapped[dict[str, Any]] = mapped_column(JSONB, nullable=False)
    residual_solve_count: Mapped[int] = mapped_column(
        Integer, nullable=False, server_default=text("0")
    )
    paper_version_id: Mapped[Optional[uuid.UUID]] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("paper_versions.id", ondelete="SET NULL"),
        nullable=True,
    )
    last_error: Mapped[Optional[str]] = mapped_column(Text, nullable=True)

    __table_args__ = (
        UniqueConstraint("paper_id", "label", name="uq_paper_assembly_runs_paper_label"),
        CheckConstraint(
            "status IN ('planned','running','finalizing','completed','failed')",
            name="status",
        ),
        CheckConstraint("residual_solve_count >= 0", name="residual_solve_count_nonnegative"),
        Index("ix_paper_assembly_runs_org_status", "org_id", "status"),
    )


class PaperAssemblySlot(UUIDPrimaryKeyMixin, TimestampMixin, Base):
    """Committed result and retry state for one blueprint slot."""

    __tablename__ = "paper_assembly_slots"

    org_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("organizations.id", ondelete="CASCADE"),
        nullable=False,
    )
    run_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("paper_assembly_runs.id", ondelete="CASCADE"),
        nullable=False,
    )
    paper_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("generated_papers.id", ondelete="CASCADE"),
        nullable=False,
    )
    slot_id: Mapped[str] = mapped_column(Text, nullable=False)
    section_id: Mapped[str] = mapped_column(Text, nullable=False)
    position: Mapped[int] = mapped_column(Integer, nullable=False)
    status: Mapped[str] = mapped_column(
        Text, nullable=False, server_default=text("'pending'")
    )
    idempotency_key: Mapped[str] = mapped_column(Text, nullable=False)
    solver_question_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), nullable=False)
    question_id: Mapped[Optional[uuid.UUID]] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("question_bank.id", ondelete="RESTRICT"),
        nullable=True,
    )
    attempt_count: Mapped[int] = mapped_column(
        Integer, nullable=False, server_default=text("0")
    )
    snapshot: Mapped[Optional[dict[str, Any]]] = mapped_column(JSONB, nullable=True)
    answer: Mapped[Optional[dict[str, Any]]] = mapped_column(JSONB, nullable=True)
    trace: Mapped[Optional[dict[str, Any]]] = mapped_column(JSONB, nullable=True)
    last_error: Mapped[Optional[str]] = mapped_column(Text, nullable=True)
    completed_at: Mapped[Optional[dt.datetime]] = mapped_column(
        DateTime(timezone=True), nullable=True
    )

    __table_args__ = (
        UniqueConstraint("run_id", "slot_id", name="uq_paper_assembly_slots_run_slot"),
        UniqueConstraint("idempotency_key", name="uq_paper_assembly_slots_idempotency_key"),
        CheckConstraint(
            "status IN ('pending','running','completed','failed')", name="status"
        ),
        CheckConstraint("position > 0", name="position_positive"),
        CheckConstraint("attempt_count >= 0", name="attempt_count_nonnegative"),
        Index("ix_paper_assembly_slots_run_status", "run_id", "status"),
        Index("ix_paper_assembly_slots_paper", "paper_id"),
    )


class TaskOutbox(UUIDPrimaryKeyMixin, Base):
    """Committed Celery publication intent, reconciled until publication succeeds."""

    __tablename__ = "task_outbox"

    org_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("organizations.id", ondelete="CASCADE"),
        nullable=False,
    )
    task_name: Mapped[str] = mapped_column(Text, nullable=False)
    idempotency_key: Mapped[str] = mapped_column(Text, nullable=False)
    payload: Mapped[dict[str, Any]] = mapped_column(JSONB, nullable=False)
    status: Mapped[str] = mapped_column(
        Text, nullable=False, server_default=text("'pending'")
    )
    publish_attempts: Mapped[int] = mapped_column(
        Integer, nullable=False, server_default=text("0")
    )
    available_at: Mapped[dt.datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )
    created_at: Mapped[dt.datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )
    published_at: Mapped[Optional[dt.datetime]] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
    last_error: Mapped[Optional[str]] = mapped_column(Text, nullable=True)

    __table_args__ = (
        UniqueConstraint("idempotency_key", name="uq_task_outbox_idempotency_key"),
        CheckConstraint("status IN ('pending','published')", name="status"),
        CheckConstraint("publish_attempts >= 0", name="publish_attempts_nonnegative"),
        Index("ix_task_outbox_pending", "status", "available_at"),
    )


__all__ = [
    "ASSEMBLY_RUN_STATUSES",
    "ASSEMBLY_SLOT_STATUSES",
    "OUTBOX_STATUSES",
    "PaperAssemblyRun",
    "PaperAssemblySlot",
    "TaskOutbox",
]
