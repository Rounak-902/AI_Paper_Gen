"""``templates`` — reusable paper layout and branding (doc 03 §5, §9 step 9).

First member of doc 03 §4's **Papers** group. A template is the presentation half of
a paper: header, school logo, section headings, marks column, instruction block. The
content half is ``generated_papers`` and ``paper_versions`` (§9 steps 10-11), which
are not part of this migration.

``layout`` is JSONB and NOT NULL. doc 03 §8 lists "layout" among the columns that are
deliberately JSONB, and §3's normalization rule — "do not store mutable canonical
content only inside JSON blobs" — is satisfied because nothing canonical lives here:
a layout is presentation, and losing it costs a re-render, not a question.
"""

from __future__ import annotations

import uuid as _uuid
from typing import TYPE_CHECKING, Any, Optional

from sqlalchemy import Boolean, CheckConstraint, ForeignKey, Index, Text, text
from sqlalchemy.dialects.postgresql import JSONB, UUID
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.db.base import (
    Base,
    RowVersionMixin,
    SoftDeleteMixin,
    TimestampMixin,
    UUIDPrimaryKeyMixin,
    check_in,
    row_version_positive,
)

if TYPE_CHECKING:  # pragma: no cover - typing only
    from app.models.organization import Organization
    from app.models.paper import GeneratedPaper
    from app.models.user import User

#: Doc 03 section 5 enumerates these three values. P6-T1 changes the historical
#: server default from ``active`` to ``draft`` because the now-implemented lifecycle
#: has explicit preview and activation steps; publishing an unreviewed create would
#: bypass both. Migration 0036 records that deliberate evolution.
TEMPLATE_STATUSES: tuple[str, ...] = ("draft", "active", "archived")


class Template(
    UUIDPrimaryKeyMixin,
    TimestampMixin,
    SoftDeleteMixin,
    RowVersionMixin,
    Base,
):
    """A reusable paper layout owned by an organization, or global if ``org_id`` is NULL."""

    __tablename__ = "templates"

    org_id: Mapped[Optional[_uuid.UUID]] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("organizations.id", ondelete="CASCADE"),
        nullable=True,
        index=True,
    )
    name: Mapped[str] = mapped_column(Text, nullable=False)
    layout: Mapped[dict[str, Any]] = mapped_column(JSONB, nullable=False)
    # SET NULL rather than CASCADE, for the same reason as `question_bank.created_by`:
    # a template outlives the person who built it. doc 03 §5 does not specify the
    # action; inferred and flagged.
    created_by: Mapped[Optional[_uuid.UUID]] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("users.id", ondelete="SET NULL"),
        nullable=True,
    )
    status: Mapped[str] = mapped_column(
        Text,
        nullable=False,
        server_default=text("'draft'"),
        index=True,
    )
    is_default: Mapped[bool] = mapped_column(
        Boolean,
        nullable=False,
        server_default=text("false"),
    )
    source_template_id: Mapped[Optional[_uuid.UUID]] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("templates.id", ondelete="SET NULL"),
        nullable=True,
    )

    organization: Mapped[Optional["Organization"]] = relationship(
        "Organization",
        back_populates="templates",
    )
    author: Mapped[Optional["User"]] = relationship(
        "User",
        back_populates="authored_templates",
    )
    # doc 03 §6: `templates ||--o{ generated_papers : renders`. Added with migration
    # 0010. The FK on the other side is ON DELETE SET NULL — a paper keeps its rendered
    # output after the template it was laid out with is retired, because
    # `paper_versions.pdf_key` already points at the finished artifact.
    generated_papers: Mapped[list["GeneratedPaper"]] = relationship(
        "GeneratedPaper",
        back_populates="template",
        cascade="save-update, merge",
    )

    __table_args__ = (
        check_in("status", TEMPLATE_STATUSES, name="status"),
        CheckConstraint(
            "is_default IS FALSE OR status = 'active'",
            name="ck_templates_default_must_be_active",
        ),
        row_version_positive(),
        # doc 03 §5: "name unique **within owner scope**". The owner is `org_id`, so
        # the constraint is (org_id, name) — partial per ADR-004 so a deleted template
        # name can be reused.
        #
        # AMBIGUITY, flagged rather than resolved: `org_id` is nullable and PostgreSQL
        # is NULLS DISTINCT, so "owner scope" for platform-global templates
        # (org_id IS NULL) is not actually a scope this index can close — two global
        # templates may share a name. That is the same shape as the `programs` and
        # `textbooks` gaps and wants one product answer covering all three, not three
        # separate guesses.
        Index(
            "uq_templates_org_id_name",
            "org_id",
            "name",
            unique=True,
            postgresql_where=text("deleted_at IS NULL"),
        ),
        # doc 03 §5 lists created_at among the indexes; it comes from TimestampMixin.
        Index("ix_templates_created_at", "created_at"),
        Index("ix_templates_source_template_id", "source_template_id"),
        Index(
            "uq_templates_one_default_per_org",
            "org_id",
            unique=True,
            postgresql_where=text(
                "is_default IS TRUE AND deleted_at IS NULL AND org_id IS NOT NULL"
            ),
        ),
    )

    def __repr__(self) -> str:  # pragma: no cover - debugging aid
        return (
            f"<Template id={self.id!r} org_id={self.org_id!r} "
            f"name={self.name!r} status={self.status!r}>"
        )


__all__ = ["TEMPLATE_STATUSES", "Template"]
