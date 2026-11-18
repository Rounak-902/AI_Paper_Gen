"""Durable export jobs and immutable artifact metadata (P6-T2)."""

from __future__ import annotations

import datetime as dt
import uuid
from typing import Optional

from sqlalchemy import BigInteger, CheckConstraint, DateTime, ForeignKey, Index, Text, func, text
from sqlalchemy.dialects.postgresql import UUID
from sqlalchemy.orm import Mapped, mapped_column

from app.db.base import Base, TimestampMixin, UUIDPrimaryKeyMixin


EXPORT_FORMATS = ("pdf", "docx")
EXPORT_ARTIFACT_TYPES = ("paper", "answer_key")
EXPORT_STATUSES = ("queued", "processing", "done", "failed")


class ExportArtifact(UUIDPrimaryKeyMixin, TimestampMixin, Base):
    """One request to render one immutable paper version with one template row.

    The object key is durable; signed URLs are generated on demand and are never
    persisted. Keeping ``format`` on the row makes this the shared job/history
    model for the P6-T3 DOCX renderer as well as the PDF renderer delivered here.
    ``artifact_type`` is orthogonal to file format so paper and answer-key jobs
    retain independent lifecycle, checksum, history, and download records.
    """

    __tablename__ = "export_artifacts"

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
        ForeignKey("paper_versions.id", ondelete="CASCADE"),
        nullable=False,
    )
    template_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("templates.id", ondelete="RESTRICT"),
        nullable=False,
    )
    requested_by: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("users.id", ondelete="RESTRICT"),
        nullable=False,
    )
    format: Mapped[str] = mapped_column(Text, nullable=False)
    artifact_type: Mapped[str] = mapped_column(
        Text, nullable=False, default="paper", server_default=text("'paper'")
    )
    status: Mapped[str] = mapped_column(
        Text, nullable=False, server_default=text("'queued'")
    )
    object_key: Mapped[Optional[str]] = mapped_column(Text, nullable=True)
    media_type: Mapped[Optional[str]] = mapped_column(Text, nullable=True)
    byte_size: Mapped[Optional[int]] = mapped_column(BigInteger, nullable=True)
    sha256: Mapped[Optional[str]] = mapped_column(Text, nullable=True)
    error: Mapped[Optional[str]] = mapped_column(Text, nullable=True)
    started_at: Mapped[Optional[dt.datetime]] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
    completed_at: Mapped[Optional[dt.datetime]] = mapped_column(
        DateTime(timezone=True), nullable=True
    )

    __table_args__ = (
        CheckConstraint("format IN ('pdf','docx')", name="format"),
        CheckConstraint(
            "artifact_type IN ('paper','answer_key')", name="artifact_type"
        ),
        CheckConstraint(
            "status IN ('queued','processing','done','failed')", name="status"
        ),
        CheckConstraint("byte_size IS NULL OR byte_size > 0", name="byte_size_positive"),
        CheckConstraint(
            "(status = 'done' AND object_key IS NOT NULL AND media_type IS NOT NULL "
            "AND byte_size IS NOT NULL AND sha256 IS NOT NULL AND completed_at IS NOT NULL) "
            "OR status <> 'done'",
            name="done_has_artifact",
        ),
        Index("ix_export_artifacts_org_status", "org_id", "status"),
        Index("ix_export_artifacts_paper_version", "paper_version_id", "created_at"),
        Index(
            "ix_export_artifacts_version_artifact_type",
            "paper_version_id",
            "artifact_type",
            "created_at",
        ),
    )


__all__ = [
    "EXPORT_ARTIFACT_TYPES",
    "EXPORT_FORMATS",
    "EXPORT_STATUSES",
    "ExportArtifact",
]
