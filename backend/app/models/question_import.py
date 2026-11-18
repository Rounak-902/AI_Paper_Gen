"""Durable background jobs for question-bank imports."""

from __future__ import annotations

import datetime as dt
import uuid
from typing import Any, Optional

from sqlalchemy import (
    Boolean,
    CheckConstraint,
    DateTime,
    ForeignKey,
    Index,
    Integer,
    LargeBinary,
    Text,
    text,
)
from sqlalchemy.dialects.postgresql import JSONB, UUID
from sqlalchemy.orm import Mapped, mapped_column

from app.db.base import Base, TimestampMixin, UUIDPrimaryKeyMixin


QUESTION_IMPORT_STATUSES = ("queued", "processing", "completed", "failed")


class QuestionImportJob(UUIDPrimaryKeyMixin, TimestampMixin, Base):
    """A durable import request whose file is discarded after processing."""

    __tablename__ = "question_import_jobs"

    org_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("organizations.id", ondelete="CASCADE"),
        nullable=False,
    )
    requested_by: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("users.id", ondelete="RESTRICT"),
        nullable=False,
    )
    requester_role: Mapped[str] = mapped_column(Text, nullable=False)
    filename: Mapped[str] = mapped_column(Text, nullable=False)
    content_type: Mapped[str] = mapped_column(Text, nullable=False)
    file_bytes: Mapped[Optional[bytes]] = mapped_column(LargeBinary, nullable=True)
    column_mapping: Mapped[list[dict[str, str]]] = mapped_column(JSONB, nullable=False)
    request_ip: Mapped[Optional[str]] = mapped_column(Text, nullable=True)
    status: Mapped[str] = mapped_column(
        Text, nullable=False, server_default=text("'queued'")
    )
    total_rows: Mapped[int] = mapped_column(Integer, nullable=False)
    processed_rows: Mapped[int] = mapped_column(
        Integer, nullable=False, server_default=text("0")
    )
    created_count: Mapped[int] = mapped_column(
        Integer, nullable=False, server_default=text("0")
    )
    created_with_warnings_count: Mapped[int] = mapped_column(
        Integer, nullable=False, server_default=text("0")
    )
    error_count: Mapped[int] = mapped_column(
        Integer, nullable=False, server_default=text("0")
    )
    duplicate_count: Mapped[int] = mapped_column(
        Integer, nullable=False, server_default=text("0")
    )
    row_results: Mapped[list[dict[str, Any]]] = mapped_column(
        JSONB, nullable=False, default=list, server_default=text("'[]'::jsonb")
    )
    error_report_available: Mapped[bool] = mapped_column(
        Boolean, nullable=False, server_default=text("false")
    )
    error: Mapped[Optional[str]] = mapped_column(Text, nullable=True)
    started_at: Mapped[Optional[dt.datetime]] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
    completed_at: Mapped[Optional[dt.datetime]] = mapped_column(
        DateTime(timezone=True), nullable=True
    )

    __table_args__ = (
        CheckConstraint(
            "status IN ('queued','processing','completed','failed')", name="status"
        ),
        CheckConstraint("total_rows >= 0", name="total_rows_nonnegative"),
        CheckConstraint(
            "processed_rows >= 0 AND processed_rows <= total_rows",
            name="processed_rows_in_range",
        ),
        CheckConstraint(
            "created_count >= 0 AND created_with_warnings_count >= 0 "
            "AND error_count >= 0 AND duplicate_count >= 0",
            name="counts_nonnegative",
        ),
        Index("ix_question_import_jobs_org_status", "org_id", "status"),
        Index(
            "ix_question_import_jobs_org_requested_by_created",
            "org_id",
            "requested_by",
            "created_at",
        ),
    )


__all__ = ["QUESTION_IMPORT_STATUSES", "QuestionImportJob"]
