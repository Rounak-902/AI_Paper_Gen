"""Mutable Paper Builder buffers, deliberately separate from immutable versions."""

from __future__ import annotations

import uuid
from typing import Any, Optional

from sqlalchemy import CheckConstraint, ForeignKey, Index, Integer, text
from sqlalchemy.dialects.postgresql import JSONB, UUID
from sqlalchemy.orm import Mapped, mapped_column

from app.db.base import Base, TimestampMixin, UUIDPrimaryKeyMixin


class PaperEditorDraft(UUIDPrimaryKeyMixin, TimestampMixin, Base):
    """One owner-scoped, compare-and-swap editor snapshot.

    This row is a mutable crash-recovery buffer. It is not a generated artifact and
    must never be read as, copied into, or counted as a :class:`PaperVersion`.
    """

    __tablename__ = "paper_editor_drafts"

    org_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("organizations.id", ondelete="CASCADE"),
        nullable=False,
    )
    owner_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("users.id", ondelete="RESTRICT"),
        nullable=False,
    )
    paper_id: Mapped[Optional[uuid.UUID]] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("generated_papers.id", ondelete="CASCADE"),
        nullable=True,
    )
    document: Mapped[dict[str, Any]] = mapped_column(JSONB, nullable=False)
    revision: Mapped[int] = mapped_column(
        Integer, nullable=False, server_default=text("1")
    )

    __table_args__ = (
        CheckConstraint("revision > 0", name="revision_positive"),
        Index("ix_paper_editor_drafts_org_owner_updated", "org_id", "owner_id", "updated_at"),
        Index("ix_paper_editor_drafts_paper_id", "paper_id"),
    )


__all__ = ["PaperEditorDraft"]
