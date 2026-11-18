"""create usage (doc 03 §9 step 13)

Revision ID: 0013
Revises: 0012
Create Date: 2026-08-17

doc 03 §9 step 13: "Create usage." The append-only metering ledger (CN-22) — the second
half of doc 03 §4's **Billing** group, and the first table in this schema without a UUID
primary key.

Every literal is frozen on purpose — see revision 0003's docstring.

``id bigint GENERATED ALWAYS AS IDENTITY``
------------------------------------------
doc 03 §5 specifies "id bigint identity, PK", and for an insert-only log that is the
better key on its own merits: a monotonic bigint appends to the right-hand edge of its
btree instead of scattering random 16-byte values across it, and it is half the width in
every index that carries it.

``ALWAYS`` rather than ``BY DEFAULT``, per the Blueprint §8 DDL. It means an INSERT
cannot supply its own id even by accident — a client-chosen sequence number in an
append-only ledger is a way to overwrite history. ``OVERRIDING SYSTEM VALUE`` remains
available for a deliberate data migration, which is the right place for that power to sit.

No ``updated_at``, no ``deleted_at``, no ``row_version``: doc 03 §5 lists none of them,
and each would be a claim that a meter reading can be revised. Corrections are modelled
the way ledgers model them — another row.

Notes on shape
--------------
* **``qty`` is unqualified ``numeric``**, exactly as doc 03 §5 types it. Deliberate
  rather than lazy: the three meters have incompatible magnitudes
  (``papers_generated`` counts in ones, ``ai_tokens`` in millions), so any precision
  chosen here would eventually clip one of them. Exact arithmetic matters more than
  usual because these values are summed to bill people.
* **``user_id`` is RESTRICT** — ``ON DELETE SET NULL`` is an UPDATE, and an append-only
  row may not be updated. One rule, applied to ``usage``, ``audit_logs`` and
  ``approvals`` alike. Nullable because system-level consumption (a scheduled
  re-index, a platform-wide OCR backfill) has no user.
* **``org_id`` is CASCADE**, going the other way. This is the one place where
  "append-only" and "delete a tenant" genuinely collide, and the resolution is that a
  hard organization delete is a compliance erasure rather than a normal operation —
  doc 03 §5's own "no **normal** hard delete" wording concedes the abnormal case exists.
* **Both indexes are composites leading with the tenant**, per doc 03 §5: the quota
  rollup is "sum this metric for this org in this window" (BR-10, FR-8.5), and the
  per-user view is "what has this person consumed lately".

Open items, reported not resolved
---------------------------------
1. **No partitioning, and doc 03 asks for it in a form that cannot be built as
   specified.** §5 says "time partition lifecycle handles archival" and the Blueprint §8
   DDL ends this table with ``PARTITION BY RANGE (ts)``. Neither names an interval, a
   retention window, or who creates next month's partition — and, decisively, **a
   partitioned table's primary key must contain the partition key**, so doc 03's
   "id bigint identity, PK" and ``PARTITION BY RANGE (ts)`` cannot both hold. The
   options are a composite ``PRIMARY KEY (id, ts)``, dropping the PK for a plain unique
   index, or not partitioning; each is a real decision with different consequences for
   the ORM. Plain table, gap reported. Converting later is a genuine migration (create
   partitioned parent, copy, swap), not an ``ALTER``, which is exactly why it wants a
   decision rather than a guess.
2. **``CHECK (qty > 0)`` forbids a negative correction.** doc 03 §5 says "qty positive",
   and strictly positive is the stricter of the two readings and the right one — AI-1's
   "zero AI usage is metered" is satisfied by *not writing a row*, so a 0 here would be
   indistinguishable from a bookkeeping bug. The cost is that a refund or clawback
   cannot be expressed as a negative quantity, which a ledger normally wants. Reported.
3. **``metric`` is a closed vocabulary, and this is the narrowest such call in the
   schema.** doc 03 §5 asks for "metric constrained to approved meter names" without
   listing them; the three come from the Blueprint §8 DDL comment. The risk is real: a
   closed CHECK on a metering column means an unrecognised meter name is a failed
   INSERT *inside a request handler*. The mitigation is that these three cover every
   metered event named in FR-8.5, EH-13 and AI-1, and that doc 03 explicitly asked for
   the constraint. Flagged so whoever adds a fourth meter knows a migration comes with it.
"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

# revision identifiers, used by Alembic.
revision: str = "0013"
down_revision: Union[str, Sequence[str], None] = "0012"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    """Upgrade schema."""

    op.create_table(
        "usage",
        sa.Column("id", sa.BigInteger(), sa.Identity(always=True), nullable=False),
        sa.Column("org_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("user_id", postgresql.UUID(as_uuid=True), nullable=True),
        sa.Column("metric", sa.Text(), nullable=False),
        # Unqualified numeric — see the module docstring.
        sa.Column("qty", sa.Numeric(), nullable=False),
        sa.Column(
            "ts",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.CheckConstraint(
            "metric IN ('papers_generated', 'ai_tokens', 'ocr_pages')",
            name=op.f("ck_usage_metric"),
        ),
        sa.CheckConstraint("qty > 0", name=op.f("ck_usage_qty_positive")),
        sa.ForeignKeyConstraint(
            ["org_id"],
            ["organizations.id"],
            name=op.f("fk_usage_org_id_organizations"),
            ondelete="CASCADE",
        ),
        # RESTRICT: SET NULL would be an UPDATE on an append-only row.
        sa.ForeignKeyConstraint(
            ["user_id"],
            ["users.id"],
            name=op.f("fk_usage_user_id_users"),
            ondelete="RESTRICT",
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_usage")),
    )
    # doc 03 §5 indexes: (org_id, metric, ts) and (user_id, ts).
    op.create_index(op.f("ix_usage_org_id_metric_ts"), "usage", ["org_id", "metric", "ts"])
    op.create_index(op.f("ix_usage_user_id_ts"), "usage", ["user_id", "ts"])


def downgrade() -> None:
    """Downgrade schema.

    Fully reversible: one table created from nothing, so dropping it restores revision
    0012's state exactly. The identity sequence is owned by the column and goes with the
    table — no separate ``DROP SEQUENCE`` is needed, and issuing one would fail.
    """

    op.drop_index(op.f("ix_usage_user_id_ts"), table_name="usage")
    op.drop_index(op.f("ix_usage_org_id_metric_ts"), table_name="usage")
    op.drop_table("usage")
