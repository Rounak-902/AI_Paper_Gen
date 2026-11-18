"""create analytics_daily (doc 03 §9 step 15)

Revision ID: 0015
Revises: 0014
Create Date: 2026-08-17

doc 03 §9 step 15: "Create analytics_daily." The daily reporting rollup behind FR-8.2 and
FR-8.3.

This table sits next to ``audit_logs`` in the migration order and is its opposite. Both
look like "history"; only one of them *is*. ``audit_logs`` is a compliance record that
may never be reconstructed. This is a **cache** — doc 03 §5 says so outright ("rollups
are rebuildable") — so it may be truncated and recomputed at any time, and every design
choice below follows from that.

Every literal is frozen on purpose — see revision 0003's docstring.

The only composite natural primary key in the schema
----------------------------------------------------
``PRIMARY KEY (org_id, day)``, per doc 03 §5. Every other table here has a surrogate
``id`` (the two bridge tables in revision 0005 have composite PKs, but over two foreign
keys, not over data).

It is the right choice and not merely doc 03's: the key *is* the identity of the rollup,
and making it the primary key is what turns the nightly recompute into an idempotent
``INSERT ... ON CONFLICT (org_id, day) DO UPDATE`` instead of a delete-then-insert race
against whoever is reading the dashboard.

No ``deleted_at``, no ``row_version``, per doc 03 §5. A cache needs no tombstone, and
optimistic locking is pointless when the writer is a single nightly job whose output is
deterministic.

Notes on shape
--------------
* **The three counters are NOT NULL with no server default**, exactly as doc 03 §5 has
  it — its "Default values" list covers the timestamps only. So a rollup row must supply
  every counter, which suits an UPSERT that always computes all of them and would be
  painful for anything doing partial updates.
* **No non-negativity CHECKs on the counters.** doc 03 asks for none, and the same
  restraint was applied to ``chapters.seq`` in revision 0003. Inventing constraints the
  design document does not specify is how a schema stops matching its own docs.
* **``ix_analytics_daily_org_id`` is redundant** — the primary key's own unique index
  already leads with ``org_id`` — and it is created anyway because doc 03 §5 lists it in
  its own right, the same call made for ``ix_paper_versions_paper_id`` in revision 0011.
  ``ix_analytics_daily_day`` is not redundant and matters: "all tenants, one day" is the
  platform-wide reporting query, and the primary key cannot serve it.

Two open items, reported not resolved
-------------------------------------
1. **``avg_difficulty numeric`` averages a text vocabulary.**
   ``question_bank.difficulty`` is ``easy`` / ``medium`` / ``hard`` (doc 03 §7.3,
   FR-3.3), so an "average difficulty" presupposes a numeric mapping that no document
   in the set defines. Whatever the rollup job picks — 1/2/3 is the obvious guess — will
   be a load-bearing convention invented outside the schema, and two consumers choosing
   differently would produce charts that disagree without either being wrong. The column
   is created exactly as specified, with unqualified ``numeric`` rather than a guessed
   precision, because the scale depends on that mapping.
2. **``day date`` has no stated timezone.** Which calendar day a paper generated at
   23:30 IST belongs to is a question this column cannot answer on its own; the rollup
   job owns the decision (Asia/Kolkata for an Indian K-12 product) and doc 03 never
   states it. A rollup silently computed in UTC would attribute late-evening activity to
   the wrong day, every day, in a way no constraint would catch.
"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

# revision identifiers, used by Alembic.
revision: str = "0015"
down_revision: Union[str, Sequence[str], None] = "0014"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    """Upgrade schema."""

    op.create_table(
        "analytics_daily",
        sa.Column("org_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("day", sa.Date(), nullable=False),
        sa.Column("papers", sa.Integer(), nullable=False),
        sa.Column("ai_questions", sa.Integer(), nullable=False),
        sa.Column("retrieved_questions", sa.Integer(), nullable=False),
        # Unqualified numeric over a text vocabulary — see the module docstring.
        sa.Column("avg_difficulty", sa.Numeric(), nullable=False),
        # FR-8.3's curriculum coverage: chapter and outcome spread, too irregular to
        # normalize and never queried relationally. JSONB per doc 03 §8.
        sa.Column("coverage", postgresql.JSONB(astext_type=sa.Text()), nullable=True),
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
        sa.ForeignKeyConstraint(
            ["org_id"],
            ["organizations.id"],
            name=op.f("fk_analytics_daily_org_id_organizations"),
            ondelete="CASCADE",
        ),
        sa.PrimaryKeyConstraint("org_id", "day", name=op.f("pk_analytics_daily")),
    )
    # doc 03 §5 indexes: day, org_id.
    op.create_index(op.f("ix_analytics_daily_day"), "analytics_daily", ["day"])
    op.create_index(op.f("ix_analytics_daily_org_id"), "analytics_daily", ["org_id"])


def downgrade() -> None:
    """Downgrade schema.

    Fully reversible: one table created from nothing, so dropping it restores revision
    0014's state exactly. Losing the data costs nothing that cannot be recomputed, which
    is the whole point of a rollup — but the *table* still has to come back on a
    re-upgrade, which is why the downgrade is a ``drop_table`` and not a ``TRUNCATE``.
    """

    op.drop_index(op.f("ix_analytics_daily_org_id"), table_name="analytics_daily")
    op.drop_index(op.f("ix_analytics_daily_day"), table_name="analytics_daily")
    op.drop_table("analytics_daily")
