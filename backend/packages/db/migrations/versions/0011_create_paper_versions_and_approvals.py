"""create paper_versions and approvals (doc 03 §9 step 11)

Revision ID: 0011
Revises: 0010
Create Date: 2026-08-17

doc 03 §9 step 11: "Create paper_versions and approvals." Two tables in one revision
because §9 names them together, the same pairing revisions 0005 and 0008 used.

They complete doc 03 §4's **Papers** group. Neither is mutable, and they are not
immutable in the same way:

* ``paper_versions`` is an **immutable render** — written once, read forever, replaced
  by writing a new label rather than editing this one.
* ``approvals`` is an **append-only trail** — every row is an event that happened, and
  the sequence is the history.

The difference shows in the columns doc 03 §5 gives them. ``paper_versions`` carries an
``updated_at``; ``approvals`` does not. Neither carries ``deleted_at`` or
``row_version``, and both are created without them here rather than "for consistency"
with the mutable tables — a tombstone on a receipt and optimistic locking on a row
nobody updates are both claims about the data that would not be true.

Every literal is frozen on purpose — see revision 0003's docstring.

Notes on shape
--------------
* **``uq_paper_versions_paper_id_label`` is a real UNIQUE constraint**, not the partial
  unique index used by every uniqueness rule in revisions 0003-0009. ADR-004's
  ``WHERE deleted_at IS NULL`` predicate exists so a name can be reused after a soft
  delete; this table has no soft delete, so there is no predicate to write and a plain
  constraint is the honest form. Same for ``approvals``, which has no uniqueness rule
  at all. This is the first non-partial UNIQUE in the schema.
* **``paper_versions.updated_at`` is nullable with no default and no ``onupdate``.**
  doc 03 §5 declares it exactly that way, on a table doc 03 titles "immutable
  renderable paper versions". Implemented literally and reported: an immutable row has
  no use for the column, and attaching an ``onupdate`` to it would be a statement that
  the row is editable. The mixin that would have supplied a NOT NULL default is
  deliberately not used.
* **``pdf_key`` / ``docx_key`` are object-storage keys and nullable.** A version exists
  from the moment it is composed; rendering is an asynchronous job that fills these in
  afterwards. Keys rather than URLs because a signed URL expires and a key does not.
* **``approvals.reviewer_id`` is RESTRICT, not SET NULL.** ``ON DELETE SET NULL`` is an
  UPDATE, and an append-only row may not be updated. So a user who has ever approved a
  paper cannot be hard-deleted, only disabled (``users.status``, ADR-012 §2) — which is
  the correct answer for a separation-of-duties record. Same rule applies to
  ``usage.user_id`` and ``audit_logs.actor_id`` in revisions 0013 and 0014.
* **Both ``paper_id`` FKs are CASCADE**, which doc 03 §5 states outright for once
  ("generated_papers delete cascades versions", "paper delete cascades approvals in
  hard-delete scenarios"). Note the asymmetry that leaves: erasing a paper erases its
  approval history, while erasing a user cannot. That is coherent — the trail answers
  "who approved *this paper*", a question that stops existing with the paper.

Open item, reported not resolved
--------------------------------
BR-6 (the author of a paper may not be its sole approver) is **not enforced here and
cannot be.** It requires ``approvals.reviewer_id <> generated_papers.created_by``, a
comparison across two rows in two tables; a CHECK constraint may only reference columns
of its own row. The two database-level options are a ``BEFORE INSERT`` trigger or a
denormalized copy of ``created_by`` onto ``approvals``, and doc 03 specifies neither.

What this revision does contribute is the ability to *check* it: the two identities are
separate columns, ``approvals`` is append-only so a decision cannot be quietly edited,
and ``ix_approvals_paper_id_created_at`` makes "the full trail for this paper" a single
index scan. Enforcement is P1-T5's.
"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

# revision identifiers, used by Alembic.
revision: str = "0011"
down_revision: Union[str, Sequence[str], None] = "0010"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    """Upgrade schema."""

    op.create_table(
        "paper_versions",
        sa.Column(
            "id",
            postgresql.UUID(as_uuid=True),
            server_default=sa.text("gen_random_uuid()"),
            nullable=False,
        ),
        sa.Column("paper_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("label", sa.Text(), nullable=False),
        sa.Column("content", postgresql.JSONB(astext_type=sa.Text()), nullable=False),
        sa.Column("answer_key", postgresql.JSONB(astext_type=sa.Text()), nullable=True),
        sa.Column("pdf_key", sa.Text(), nullable=True),
        sa.Column("docx_key", sa.Text(), nullable=True),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        # Nullable, no default — see the module docstring.
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=True),
        sa.ForeignKeyConstraint(
            ["paper_id"],
            ["generated_papers.id"],
            name=op.f("fk_paper_versions_paper_id_generated_papers"),
            ondelete="CASCADE",
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_paper_versions")),
        sa.UniqueConstraint(
            "paper_id",
            "label",
            name=op.f("uq_paper_versions_paper_id_label"),
        ),
    )
    # doc 03 §5 indexes: paper_id and created_at. The first duplicates the leading
    # column of the unique constraint above and is created anyway because doc 03 lists
    # it in its own right — the same call made for `ix_question_bank_root_question_id`
    # in revision 0006.
    op.create_index(op.f("ix_paper_versions_created_at"), "paper_versions", ["created_at"])
    op.create_index(op.f("ix_paper_versions_paper_id"), "paper_versions", ["paper_id"])

    op.create_table(
        "approvals",
        sa.Column(
            "id",
            postgresql.UUID(as_uuid=True),
            server_default=sa.text("gen_random_uuid()"),
            nullable=False,
        ),
        sa.Column("paper_id", postgresql.UUID(as_uuid=True), nullable=False),
        # Nullable per doc 03 §5: a system-generated transition has no human reviewer.
        sa.Column("reviewer_id", postgresql.UUID(as_uuid=True), nullable=True),
        sa.Column("action", sa.Text(), nullable=False),
        sa.Column("comment", sa.Text(), nullable=True),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        # doc 03 §5 says only "action constrained to workflow states" and §7.3 has no row
        # for this column. These four verbs come from the Blueprint §8 DDL comment
        # (`action text NOT NULL, -- submit/approve/reject/comment`), the only place in
        # the document set that enumerates them. Note they are verbs, not the paper
        # states in `ck_generated_papers_status` — doc 03's "workflow states" wording is
        # the looser of the two sources, and the mismatch is reported.
        sa.CheckConstraint(
            "action IN ('submit', 'approve', 'reject', 'comment')",
            name=op.f("ck_approvals_action"),
        ),
        sa.ForeignKeyConstraint(
            ["paper_id"],
            ["generated_papers.id"],
            name=op.f("fk_approvals_paper_id_generated_papers"),
            ondelete="CASCADE",
        ),
        # RESTRICT, not SET NULL — see the module docstring.
        sa.ForeignKeyConstraint(
            ["reviewer_id"],
            ["users.id"],
            name=op.f("fk_approvals_reviewer_id_users"),
            ondelete="RESTRICT",
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_approvals")),
    )
    # doc 03 §5 indexes, both composite and both time-ordered: "the trail for this
    # paper" and "everything this reviewer has decided".
    op.create_index(
        op.f("ix_approvals_paper_id_created_at"),
        "approvals",
        ["paper_id", "created_at"],
    )
    op.create_index(
        op.f("ix_approvals_reviewer_id_created_at"),
        "approvals",
        ["reviewer_id", "created_at"],
    )


def downgrade() -> None:
    """Downgrade schema.

    Fully reversible. Reverse creation order — ``approvals`` first, then
    ``paper_versions`` — even though neither references the other, so the sequence
    reads as the mirror of ``upgrade()`` rather than something a reader has to check.
    """

    op.drop_index(op.f("ix_approvals_reviewer_id_created_at"), table_name="approvals")
    op.drop_index(op.f("ix_approvals_paper_id_created_at"), table_name="approvals")
    op.drop_table("approvals")

    op.drop_index(op.f("ix_paper_versions_paper_id"), table_name="paper_versions")
    op.drop_index(op.f("ix_paper_versions_created_at"), table_name="paper_versions")
    op.drop_table("paper_versions")
