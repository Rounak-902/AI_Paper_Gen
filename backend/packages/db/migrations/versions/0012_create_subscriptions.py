"""create subscriptions (doc 03 §9 step 12)

Revision ID: 0012
Revises: 0011
Create Date: 2026-08-17

doc 03 §9 step 12: "Create subscriptions." One table — the first half of doc 03 §4's
**Billing** group, with ``usage`` following in revision 0013.

Every literal is frozen on purpose — see revision 0003's docstring.

What this revision deliberately does *not* do
---------------------------------------------
It does **not** add the ``organizations.subscription_id`` foreign key. Revision 0002
shipped that column as a bare UUID with no ``REFERENCES`` clause because its target
table did not exist yet (expand-migrate-contract, doc 03 §8), and it is tempting to
close the loop here, one statement after the target appears.

doc 03 §9 places it later, and the wording is specific enough to follow rather than
override:

* Step 4 is "Create curriculum_versions **and wire version relations**" — when §9 wants
  a constraint wired in the same step as the table, it says so.
* Step 12 is "Create subscriptions", full stop.
* Step 18 is "Add indexes, **constraints**, RLS policies, partitions, and seed data",
  and by that point the ``organizations.subscription_id`` FK is the only outstanding
  constraint in the entire chain.

So the FK lands in revision 0018. Nothing breaks in between: the column already exists
and is nullable, so revisions 0012 through 0017 are all valid states. The choice is
reported as a judgment call because the alternative reading — that "create
subscriptions" implies wiring what points at it — is defensible, and the DDL is
identical either way; only which revision owns it differs.

Notes on shape
--------------
* **``org_id`` is a plain index, not unique**, exactly as doc 03 §5 lists it. Nothing
  here enforces "one live subscription per organization", and that is right: a plan
  change is a new row with a new period, so an organization legitimately accumulates a
  history. Which row is *current* is a question about ``status`` and ``period_end``,
  answered by the service layer; ``organizations.subscription_id`` is the denormalized
  pointer that makes it cheap.
* **``period_start`` / ``period_end`` are ``date``, not ``timestamptz``**, per doc 03
  §5 — a billing period is a calendar concept. Both nullable, because a free plan has
  no period.
* **No ``CHECK (period_end >= period_start)``.** doc 03 §5 asks for none, and an
  open-ended enterprise contract is a real state with a NULL end — so the constraint
  would have to be written as an implication to avoid rejecting it. Not invented;
  reported.
* **``provider_ref`` is a payment-provider reference, not a secret.** Safe to log, and
  the join key for webhook handling.

Two vocabularies, one of them hedged
------------------------------------
``plan``: doc 03 §5 says only "plan constrained to plan values" and §7.3 has no row for
the column. The four values come from the Blueprint §8 DDL comment
(``plan text NOT NULL, -- free/standard/pro/enterprise``), the only enumeration in the
document set — the same precedent revision 0002 used for ``organizations.type``.

``status``: doc 03 §5 says "status constrained to active/past_due/canceled/suspended
**style** values". The hedge is doc 03's, and the four names are taken literally rather
than read as licence to invent — ``trialing`` and ``incomplete``, which every payment
provider emits, are therefore absent, and adding one is a one-line CHECK migration
(ADR-011). Reported. ``canceled`` keeps doc 03's single-l American spelling, which is
also what the providers' own APIs emit; a vocabulary value is a wire format, not English.

Open item, reported not resolved
--------------------------------
``uq_subscriptions_provider_ref`` is the **one uniqueness rule in this schema without a
``WHERE deleted_at IS NULL`` predicate**, and that is a conscious deviation from
ADR-004's pattern rather than an oversight.

ADR-004's predicate exists so a human-chosen name can be reused after a soft delete. A
provider reference is not human-chosen and is never reused — a cancel-and-resubscribe
mints a new one — so scoping the constraint to live rows would buy nothing and cost
correctness: the webhook handler looks a ``provider_ref`` up expecting exactly one row,
and a soft-deleted duplicate would hand it two.

A plain UNIQUE also already gives doc 03's "unique **if present**" for free, with no
``WHERE provider_ref IS NOT NULL`` term: PostgreSQL unique constraints are NULLS
DISTINCT, so any number of rows may leave the column NULL while two rows may not share
a non-NULL value.
"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

# revision identifiers, used by Alembic.
revision: str = "0012"
down_revision: Union[str, Sequence[str], None] = "0011"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    """Upgrade schema."""

    op.create_table(
        "subscriptions",
        sa.Column(
            "id",
            postgresql.UUID(as_uuid=True),
            server_default=sa.text("gen_random_uuid()"),
            nullable=False,
        ),
        sa.Column("org_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("plan", sa.Text(), nullable=False),
        sa.Column("seats", sa.Integer(), nullable=True),
        sa.Column("status", sa.Text(), server_default=sa.text("'active'"), nullable=False),
        sa.Column("period_start", sa.Date(), nullable=True),
        sa.Column("period_end", sa.Date(), nullable=True),
        sa.Column("provider_ref", sa.Text(), nullable=True),
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
            "plan IN ('free', 'standard', 'pro', 'enterprise')",
            name=op.f("ck_subscriptions_plan"),
        ),
        sa.CheckConstraint(
            "status IN ('active', 'past_due', 'canceled', 'suspended')",
            name=op.f("ck_subscriptions_status"),
        ),
        sa.CheckConstraint(
            "row_version > 0",
            name=op.f("ck_subscriptions_row_version_positive"),
        ),
        # doc 03 §5: "prefer soft delete" — CASCADE is the hard tenant-offboarding path
        # only, consistent with every other `org_id` in the schema.
        sa.ForeignKeyConstraint(
            ["org_id"],
            ["organizations.id"],
            name=op.f("fk_subscriptions_org_id_organizations"),
            ondelete="CASCADE",
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_subscriptions")),
        # Not partial on `deleted_at` — see the module docstring.
        sa.UniqueConstraint("provider_ref", name=op.f("uq_subscriptions_provider_ref")),
    )
    # doc 03 §5 indexes: org_id, status, period_end. The third drives the renewal sweep
    # and the NT-7 expiry warning.
    op.create_index(op.f("ix_subscriptions_org_id"), "subscriptions", ["org_id"])
    op.create_index(op.f("ix_subscriptions_period_end"), "subscriptions", ["period_end"])
    op.create_index(op.f("ix_subscriptions_status"), "subscriptions", ["status"])


def downgrade() -> None:
    """Downgrade schema.

    Fully reversible: one table created from nothing, so dropping it restores revision
    0011's state exactly.

    Nothing has to be undone on ``organizations`` here, and that is the payoff for
    leaving its foreign key to revision 0018 — the two concerns downgrade independently.
    """

    op.drop_index(op.f("ix_subscriptions_status"), table_name="subscriptions")
    op.drop_index(op.f("ix_subscriptions_period_end"), table_name="subscriptions")
    op.drop_index(op.f("ix_subscriptions_org_id"), table_name="subscriptions")
    op.drop_table("subscriptions")
