"""The **Textbooks** table group of doc 03 §4 (§5, §9 step 8).

``textbooks`` is the container for an uploaded source document; ``textbook_assets``
holds what the ingestion pipeline extracted from it — paragraphs, exercise sets,
diagrams, tables. Assets are the raw material the question extractor reads, and
``chapter_id`` is what anchors them to the syllabus.

Both tables are schema-only here. The ingestion pipeline itself (OCR, parsing,
asset classification) is `05_Textbook_Ingestion.md` and a later phase; nothing in
this module knows how the status column advances.
"""

from __future__ import annotations

import uuid as _uuid
from typing import TYPE_CHECKING, Any, Optional

from sqlalchemy import ForeignKey, Index, Integer, Text, text
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
    from app.models.curriculum import Chapter, Subject
    from app.models.organization import Organization

#: doc 03 §7.3, which pins these as "persisted values only (see
#: 05_Textbook_Ingestion.md §15)" — the qualifier matters: the pipeline has more
#: internal states than these five, and only these five are ever written to the row.
#: doc 03 §5's looser phrasing ("uploaded/ocr/parsed/embedded/failed **style**
#: lifecycle values") is superseded by §7.3's exact list.
#:
#: ``embedded`` is worth noting: it survives in the vocabulary even though ADR-007
#: defers embeddings entirely, so a textbook cannot legitimately reach that state at
#: MVP. It is kept because §7.3 lists it and the value costs nothing; flagged as an
#: observation rather than removed.
TEXTBOOK_STATUSES: tuple[str, ...] = (
    "uploaded",
    "ocr",
    "parsed",
    "embedded",
    "failed",
)

#: doc 03 §7.3 — the seven asset kinds. §5's "paragraph/exercise/question/diagram/
#: table/activity/formula style values" enumerates the same seven.
TEXTBOOK_ASSET_KINDS: tuple[str, ...] = (
    "paragraph",
    "exercise",
    "question",
    "diagram",
    "table",
    "activity",
    "formula",
)

_SOFT_DELETE_PREDICATE = "deleted_at IS NULL"


class Textbook(
    UUIDPrimaryKeyMixin,
    TimestampMixin,
    SoftDeleteMixin,
    RowVersionMixin,
    Base,
):
    """An uploaded source document (doc 03 §5)."""

    __tablename__ = "textbooks"

    # Nullable per doc 03 §5: a NULL org_id is a platform-global textbook, the same
    # convention `question_bank.scope = 'global'` uses. CASCADE for the hard-delete
    # offboarding path only ("org deletion cascades org-owned textbooks").
    org_id: Mapped[Optional[_uuid.UUID]] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("organizations.id", ondelete="CASCADE"),
        nullable=True,
        index=True,
    )
    subject_id: Mapped[Optional[_uuid.UUID]] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("subjects.id", ondelete="RESTRICT"),
        nullable=True,
        index=True,
    )
    # The object-store key for the uploaded file. Note the naming inconsistency with
    # `textbook_assets.s3_key` below — same concept, two names, both straight from
    # doc 03 §5. Neither is renamed here; flagged in the hand-off notes.
    file_key: Mapped[str] = mapped_column(Text, nullable=False)
    status: Mapped[str] = mapped_column(
        Text,
        nullable=False,
        server_default=text("'uploaded'"),
        index=True,
    )
    pages: Mapped[Optional[int]] = mapped_column(Integer, nullable=True)

    organization: Mapped[Optional["Organization"]] = relationship(
        "Organization",
        back_populates="textbooks",
    )
    subject: Mapped[Optional["Subject"]] = relationship(
        "Subject",
        back_populates="textbooks",
    )
    assets: Mapped[list["TextbookAsset"]] = relationship(
        "TextbookAsset",
        back_populates="textbook",
        cascade="save-update, merge",
    )

    __table_args__ = (
        check_in("status", TEXTBOOK_STATUSES, name="status"),
        row_version_positive(),
        # doc 03 §5: "**optional** unique(org_id, file_key)". Created — re-uploading
        # the identical object key into the same org is a duplicate, not a new
        # textbook, and catching it in the database is cheaper than reconciling two
        # ingestion runs over one file later.
        #
        # Same NULLS DISTINCT caveat as `programs`: `org_id` is nullable, so two
        # platform-global textbooks may share a file_key. Reported, not silently
        # tightened.
        Index(
            "uq_textbooks_org_id_file_key",
            "org_id",
            "file_key",
            unique=True,
            postgresql_where=text(_SOFT_DELETE_PREDICATE),
        ),
        # doc 03 §5 lists `created_at` among this table's indexes. It comes from
        # TimestampMixin, so it cannot carry index=True at the column and is declared
        # here instead.
        Index("ix_textbooks_created_at", "created_at"),
    )

    def __repr__(self) -> str:  # pragma: no cover - debugging aid
        return (
            f"<Textbook id={self.id!r} org_id={self.org_id!r} "
            f"status={self.status!r} pages={self.pages!r}>"
        )


class TextbookAsset(
    UUIDPrimaryKeyMixin,
    TimestampMixin,
    SoftDeleteMixin,
    RowVersionMixin,
    Base,
):
    """Extracted textbook content or media (doc 03 §5)."""

    __tablename__ = "textbook_assets"

    # CASCADE per doc 03 §5, "textbook delete cascades assets". Assets are derived
    # data: re-running ingestion reproduces them, so there is no history to preserve.
    textbook_id: Mapped[_uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("textbooks.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )
    # RESTRICT per doc 03 §5 chapters: "restrict deletion if outcomes, questions, or
    # textbook assets still reference the chapter".
    chapter_id: Mapped[Optional[_uuid.UUID]] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("chapters.id", ondelete="RESTRICT"),
        nullable=True,
        index=True,
    )
    kind: Mapped[str] = mapped_column(Text, nullable=False, index=True)
    s3_key: Mapped[Optional[str]] = mapped_column(Text, nullable=True)
    data: Mapped[Optional[Any]] = mapped_column(JSONB, nullable=True)
    page: Mapped[Optional[int]] = mapped_column(Integer, nullable=True, index=True)

    textbook: Mapped["Textbook"] = relationship("Textbook", back_populates="assets")
    chapter: Mapped[Optional["Chapter"]] = relationship(
        "Chapter",
        back_populates="textbook_assets",
    )

    __table_args__ = (
        check_in("kind", TEXTBOOK_ASSET_KINDS, name="kind"),
        row_version_positive(),
        # doc 03 §5 lists no uniqueness for this table, and none is invented. Two
        # identical paragraphs on one page are a legitimate extraction result.
    )

    def __repr__(self) -> str:  # pragma: no cover - debugging aid
        return (
            f"<TextbookAsset id={self.id!r} textbook_id={self.textbook_id!r} "
            f"kind={self.kind!r} page={self.page!r}>"
        )


__all__ = [
    "TEXTBOOK_ASSET_KINDS",
    "TEXTBOOK_STATUSES",
    "Textbook",
    "TextbookAsset",
]
