"""create textbooks and textbook_assets (doc 03 §9 step 8)

Revision ID: 0008
Revises: 0007
Create Date: 2026-08-17

doc 03 §9 step 8: "Create textbooks and textbook_assets." The container for an
uploaded source document, and the extracted content the ingestion pipeline produces
from it.

Every literal is frozen on purpose — see revision 0003's docstring.

Notes on shape
--------------
* **``textbooks.org_id`` is nullable** (doc 03 §5), and a NULL means a platform-global
  textbook — the same convention ``question_bank.scope = 'global'`` uses. CASCADE
  covers doc 03 §5's "org deletion cascades org-owned textbooks", reachable only
  through the hard-delete offboarding path.

* **Assets CASCADE from their textbook** (doc 03 §5: "textbook delete cascades
  assets") even though ADR-004 prefers soft deletes everywhere. That is consistent
  rather than contradictory: assets are *derived* data. Re-running ingestion over the
  same file reproduces them, so there is no history in an asset row worth preserving
  once its source is gone. They still carry ``deleted_at`` for the ordinary case where
  one bad extraction is retired without dropping the textbook.

* **``chapter_id`` is RESTRICT**, straight from doc 03 §5 chapters: "restrict deletion
  if outcomes, questions, or textbook assets still reference the chapter".

* **No uniqueness on ``textbook_assets``**, because doc 03 §5 lists none and none
  should be invented — two identical paragraphs on one page is a legitimate
  extraction result, not a bug to reject at the constraint level.

Open items, reported not resolved
---------------------------------
* **``file_key`` here, ``s3_key`` on assets.** Same concept — an object-store key —
  under two names, both taken verbatim from doc 03 §5. Neither is renamed: picking one
  would be a silent resolution, and the fix (whichever way) is a mechanical rename
  migration once doc 03 settles it.

* **``status`` admits ``embedded``, which is unreachable at MVP.** doc 03 §7.3
  enumerates the five values as "persisted values only"; ADR-007 defers embeddings
  entirely, so nothing can legitimately move a textbook into that state while
  revision 0007's gate holds. The value ships because §7.3 lists it and it costs
  nothing; the observation is reported.

* **``unique(org_id, file_key)`` has the NULLS DISTINCT hole**, so two *global*
  textbooks may share a file key. doc 03 §5 marks the constraint "optional" and never
  addresses the null case. Created anyway — re-uploading the identical object key into
  the same org is a duplicate rather than a new textbook, and catching it here is
  cheaper than reconciling two ingestion runs over one file afterwards. Identical in
  shape to the ``programs`` (0005) and ``templates`` (0009) gaps; one product answer
  should cover all three.
"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

# revision identifiers, used by Alembic.
revision: str = "0008"
down_revision: Union[str, Sequence[str], None] = "0007"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None

_SOFT_DELETE_PREDICATE = "deleted_at IS NULL"


def upgrade() -> None:
    """Upgrade schema."""

    # ------------------------------------------------------------------
    # textbooks — uploaded source document (doc 03 §5)
    # ------------------------------------------------------------------
    op.create_table(
        "textbooks",
        sa.Column(
            "id",
            postgresql.UUID(as_uuid=True),
            server_default=sa.text("gen_random_uuid()"),
            nullable=False,
        ),
        sa.Column("org_id", postgresql.UUID(as_uuid=True), nullable=True),
        sa.Column("subject_id", postgresql.UUID(as_uuid=True), nullable=True),
        sa.Column("file_key", sa.Text(), nullable=False),
        sa.Column("status", sa.Text(), server_default=sa.text("'uploaded'"), nullable=False),
        sa.Column("pages", sa.Integer(), nullable=True),
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
        sa.Column("deleted_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("row_version", sa.Integer(), server_default=sa.text("1"), nullable=False),
        # doc 03 §7.3's exact five, which supersede §5's looser "…style lifecycle
        # values" phrasing.
        sa.CheckConstraint(
            "status IN ('uploaded', 'ocr', 'parsed', 'embedded', 'failed')",
            name=op.f("ck_textbooks_status"),
        ),
        sa.CheckConstraint("row_version > 0", name=op.f("ck_textbooks_row_version_positive")),
        sa.ForeignKeyConstraint(
            ["org_id"],
            ["organizations.id"],
            name=op.f("fk_textbooks_org_id_organizations"),
            ondelete="CASCADE",
        ),
        sa.ForeignKeyConstraint(
            ["subject_id"],
            ["subjects.id"],
            name=op.f("fk_textbooks_subject_id_subjects"),
            ondelete="RESTRICT",
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_textbooks")),
    )
    # doc 03 §5 indexes: org_id, subject_id, status, created_at.
    op.create_index(op.f("ix_textbooks_created_at"), "textbooks", ["created_at"])
    op.create_index(op.f("ix_textbooks_org_id"), "textbooks", ["org_id"])
    op.create_index(op.f("ix_textbooks_status"), "textbooks", ["status"])
    op.create_index(op.f("ix_textbooks_subject_id"), "textbooks", ["subject_id"])
    op.create_index(
        "uq_textbooks_org_id_file_key",
        "textbooks",
        ["org_id", "file_key"],
        unique=True,
        postgresql_where=sa.text(_SOFT_DELETE_PREDICATE),
    )

    # ------------------------------------------------------------------
    # textbook_assets — extracted content and media (doc 03 §5)
    # ------------------------------------------------------------------
    op.create_table(
        "textbook_assets",
        sa.Column(
            "id",
            postgresql.UUID(as_uuid=True),
            server_default=sa.text("gen_random_uuid()"),
            nullable=False,
        ),
        sa.Column("textbook_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("chapter_id", postgresql.UUID(as_uuid=True), nullable=True),
        sa.Column("kind", sa.Text(), nullable=False),
        sa.Column("s3_key", sa.Text(), nullable=True),
        sa.Column("data", postgresql.JSONB(astext_type=sa.Text()), nullable=True),
        sa.Column("page", sa.Integer(), nullable=True),
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
        sa.Column("deleted_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("row_version", sa.Integer(), server_default=sa.text("1"), nullable=False),
        sa.CheckConstraint(
            "kind IN ('paragraph', 'exercise', 'question', 'diagram', 'table', "
            "'activity', 'formula')",
            name=op.f("ck_textbook_assets_kind"),
        ),
        sa.CheckConstraint(
            "row_version > 0",
            name=op.f("ck_textbook_assets_row_version_positive"),
        ),
        sa.ForeignKeyConstraint(
            ["chapter_id"],
            ["chapters.id"],
            name=op.f("fk_textbook_assets_chapter_id_chapters"),
            ondelete="RESTRICT",
        ),
        sa.ForeignKeyConstraint(
            ["textbook_id"],
            ["textbooks.id"],
            name=op.f("fk_textbook_assets_textbook_id_textbooks"),
            ondelete="CASCADE",
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_textbook_assets")),
    )
    # doc 03 §5 indexes: textbook_id, chapter_id, kind, page.
    op.create_index(op.f("ix_textbook_assets_chapter_id"), "textbook_assets", ["chapter_id"])
    op.create_index(op.f("ix_textbook_assets_kind"), "textbook_assets", ["kind"])
    op.create_index(op.f("ix_textbook_assets_page"), "textbook_assets", ["page"])
    op.create_index(op.f("ix_textbook_assets_textbook_id"), "textbook_assets", ["textbook_id"])


def downgrade() -> None:
    """Downgrade schema.

    Fully reversible; dropped in reverse dependency order so the assets' CASCADE FK
    does not block dropping ``textbooks``.
    """

    op.drop_index(op.f("ix_textbook_assets_textbook_id"), table_name="textbook_assets")
    op.drop_index(op.f("ix_textbook_assets_page"), table_name="textbook_assets")
    op.drop_index(op.f("ix_textbook_assets_kind"), table_name="textbook_assets")
    op.drop_index(op.f("ix_textbook_assets_chapter_id"), table_name="textbook_assets")
    op.drop_table("textbook_assets")

    op.drop_index("uq_textbooks_org_id_file_key", table_name="textbooks")
    op.drop_index(op.f("ix_textbooks_subject_id"), table_name="textbooks")
    op.drop_index(op.f("ix_textbooks_status"), table_name="textbooks")
    op.drop_index(op.f("ix_textbooks_org_id"), table_name="textbooks")
    op.drop_index(op.f("ix_textbooks_created_at"), table_name="textbooks")
    op.drop_table("textbooks")
