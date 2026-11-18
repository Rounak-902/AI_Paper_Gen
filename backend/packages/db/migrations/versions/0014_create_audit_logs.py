"""create audit_logs (doc 03 §9 step 14)

Revision ID: 0014
Revises: 0013
Create Date: 2026-08-17

doc 03 §9 step 14: "Create audit_logs." The append-only compliance trail behind BR-7,
BR-11 and CN-22 — every cross-tenant read by a Super Admin, every membership change,
every approval, every export.

``id bigint GENERATED ALWAYS AS IDENTITY`` for the same reasons as ``usage`` in revision
0013, and the same three columns are absent for the same reason: no ``updated_at``, no
``deleted_at``, no ``row_version``. A compliance trail that can be revised is not one.

Every literal is frozen on purpose — see revision 0003's docstring.

Why ``action`` has NO check constraint
-------------------------------------
Every other controlled vocabulary in this schema is CHECK-constrained (ADR-011), and
doc 03 §5 asks for "action required and normalized" here. It is created NOT NULL with no
CHECK, and that gap is deliberate rather than an omission.

A closed vocabulary on an audit action column fails *closed*: the day someone adds an
endpoint and forgets the migration, the audit write raises — and either the request
fails, or, far more likely, someone wraps the audit write in a ``try/except`` and the
trail silently stops recording that action. **An audit log that rejects unfamiliar
events is an audit log with holes in exactly the places an investigation cares about.**

"Normalized" is therefore implemented as an application convention
(``<entity>.<verb>``, lowercase) rather than a database constraint. Reported as a
conscious deviation from ADR-011 rather than left to be discovered.

Why ``entity_id`` has no foreign key
------------------------------------
It points at whichever table ``entity`` names, so no single ``REFERENCES`` clause can
express it — and a polymorphic pointer with a real FK would delete audit rows when their
subject was deleted, which is the opposite of what an audit trail is for. Both columns
are nullable because some actions have no subject (``auth.login_failed``).

``org_id`` is nullable *and* CASCADE
-----------------------------------
That reads like a contradiction and is not. Nullable because platform-level events (a
Super Admin signing in, a global curriculum publish) belong to no tenant. CASCADE
because doc 03 §5 names no action and both alternatives are worse:

* RESTRICT makes a tenant erasure impossible forever.
* SET NULL is an UPDATE on a row declared immutable, *and* it would quietly relabel one
  tenant's history as platform history — the single most misleading thing this table
  could do.

So erasing an organization erases its trail, on the reading that a hard tenant delete is
a compliance erasure (a right-to-be-forgotten request over rows holding ``actor_id`` and
``ip``) rather than routine maintenance. Rows with ``org_id IS NULL`` survive untouched.
``actor_id`` is RESTRICT, per the append-only rule from revision 0013, so an individual
user cannot be deleted out from under the trail.

Notes on shape
--------------
* **``ip`` is PostgreSQL ``inet``, not text.** It validates on the way in, stores v4 and
  v6 in 7 or 19 bytes instead of 15 or 45 characters, and supports subnet containment
  (``<<=``) so "everything from this /24" is an indexable query rather than a ``LIKE``.
  Nullable — a Celery worker's action has no client address.
* **``meta`` is nullable with no default**, exactly as doc 03 §5 specifies. Worth
  noting because ``question_bank.meta`` is NOT NULL DEFAULT ``'{}'``: two JSONB "meta"
  columns, two nullability rules, one document. Implemented literally in both places and
  flagged — readers here must handle NULL, readers there need not.
* **Four indexes, three of them ``(dimension, ts)`` composites** for the "recent
  activity by X" query the Audit screen (S-16) runs. The fourth,
  ``(entity, entity_id)``, is the polymorphic lookup — "everything that ever happened to
  this row" — which is the query an incident investigation starts from.

Open item, reported not resolved
--------------------------------
No partitioning, for exactly the reasons set out in revision 0013's docstring: doc 03 §5
says "time partition lifecycle handles archival", the Blueprint §8 DDL says
``PARTITION BY RANGE (ts)``, no interval or retention is specified anywhere, and a
partitioned table's primary key must contain the partition key — which doc 03's
"id bigint identity, PK" excludes. Plain table; gap reported once for both tables.
"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

# revision identifiers, used by Alembic.
revision: str = "0014"
down_revision: Union[str, Sequence[str], None] = "0013"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    """Upgrade schema."""

    op.create_table(
        "audit_logs",
        sa.Column("id", sa.BigInteger(), sa.Identity(always=True), nullable=False),
        sa.Column("org_id", postgresql.UUID(as_uuid=True), nullable=True),
        sa.Column("actor_id", postgresql.UUID(as_uuid=True), nullable=True),
        # NOT NULL, and deliberately no CHECK — see the module docstring.
        sa.Column("action", sa.Text(), nullable=False),
        sa.Column("entity", sa.Text(), nullable=True),
        # No foreign key: polymorphic pointer — see the module docstring.
        sa.Column("entity_id", postgresql.UUID(as_uuid=True), nullable=True),
        sa.Column("ip", postgresql.INET(), nullable=True),
        sa.Column("meta", postgresql.JSONB(astext_type=sa.Text()), nullable=True),
        sa.Column(
            "ts",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        # RESTRICT: SET NULL would be an UPDATE on an append-only row.
        sa.ForeignKeyConstraint(
            ["actor_id"],
            ["users.id"],
            name=op.f("fk_audit_logs_actor_id_users"),
            ondelete="RESTRICT",
        ),
        sa.ForeignKeyConstraint(
            ["org_id"],
            ["organizations.id"],
            name=op.f("fk_audit_logs_org_id_organizations"),
            ondelete="CASCADE",
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_audit_logs")),
    )
    # doc 03 §5's four indexes.
    op.create_index(op.f("ix_audit_logs_action_ts"), "audit_logs", ["action", "ts"])
    op.create_index(op.f("ix_audit_logs_actor_id_ts"), "audit_logs", ["actor_id", "ts"])
    op.create_index(
        op.f("ix_audit_logs_entity_entity_id"),
        "audit_logs",
        ["entity", "entity_id"],
    )
    op.create_index(op.f("ix_audit_logs_org_id_ts"), "audit_logs", ["org_id", "ts"])


def downgrade() -> None:
    """Downgrade schema.

    Fully reversible: one table created from nothing, so dropping it restores revision
    0013's state exactly.
    """

    op.drop_index(op.f("ix_audit_logs_org_id_ts"), table_name="audit_logs")
    op.drop_index(op.f("ix_audit_logs_entity_entity_id"), table_name="audit_logs")
    op.drop_index(op.f("ix_audit_logs_actor_id_ts"), table_name="audit_logs")
    op.drop_index(op.f("ix_audit_logs_action_ts"), table_name="audit_logs")
    op.drop_table("audit_logs")
