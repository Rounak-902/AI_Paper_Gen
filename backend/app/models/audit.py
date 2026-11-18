"""``audit_logs`` / ``analytics_daily`` (doc 03 §5, §9 steps 14-15).

Two tables that both look like "history" and are nothing alike. ``audit_logs`` is the
compliance record (CN-22, append-only, must never be reconstructed); ``analytics_daily``
is a cache (doc 03 §5: "rollups are rebuildable"). The difference decides every design
question in this module — a cache may be truncated and recomputed, a compliance trail
may not.

Why ``audit_logs.action`` has no CHECK constraint
-------------------------------------------------
Every other controlled vocabulary in this schema is CHECK-constrained (ADR-011), and
doc 03 §5 asks for "action required and normalized" here. It is implemented as NOT NULL
with no CHECK, and that gap is intentional.

A closed vocabulary on an audit action column fails *closed*: the day someone adds an
endpoint and forgets the migration, the audit write raises and either the request fails
or — far worse, and far more likely — someone wraps the audit write in a try/except and
the trail silently stops recording that action. An audit log that rejects unfamiliar
events is an audit log with holes in exactly the places an investigation cares about.
"Normalized" is therefore an application convention (``<entity>.<verb>``, lowercase),
not a database constraint. Reported as a deliberate deviation from ADR-011 rather than
left to be discovered.

``entity_id`` has no foreign key for the same class of reason: it points at whichever
table ``entity`` names, so no single ``REFERENCES`` clause can express it, and a
polymorphic pointer with a real FK would delete audit rows when their subject was
deleted. Which is the opposite of what an audit trail is for.
"""

from __future__ import annotations

import datetime as _dt
import decimal as _decimal
import uuid as _uuid
from typing import TYPE_CHECKING, Any, Optional

from sqlalchemy import (
    BigInteger,
    Date,
    DateTime,
    ForeignKey,
    Identity,
    Index,
    Integer,
    Numeric,
    PrimaryKeyConstraint,
    Text,
    func,
)
from sqlalchemy.dialects.postgresql import INET, JSONB, UUID
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.db.base import Base, TimestampMixin

if TYPE_CHECKING:  # pragma: no cover - typing only
    from app.models.organization import Organization
    from app.models.user import User


class AuditLog(Base):
    """One append-only compliance entry (CN-22).

    ``org_id`` is nullable *and* CASCADE, which reads like a contradiction and is not.
    Nullable because platform-level events (a Super Admin signing in, a global
    curriculum publish) belong to no tenant. CASCADE because doc 03 §5 names no action
    and the alternatives are worse: RESTRICT makes a tenant erasure impossible forever,
    and SET NULL is an UPDATE on a row declared immutable — it would also quietly
    relabel one tenant's history as platform history, which is the single most
    misleading thing this table could do.

    So: erasing an organization erases its trail, on the reading that a hard tenant
    delete is a compliance erasure (a right-to-be-forgotten request over rows holding
    ``actor_id`` and ``ip``), not routine maintenance. Rows with ``org_id IS NULL``
    survive untouched. ``actor_id`` is RESTRICT — the append-only rule from
    ``app/models/billing.py`` — so an individual user cannot be deleted out from under
    the trail.
    """

    __tablename__ = "audit_logs"

    id: Mapped[int] = mapped_column(
        BigInteger,
        Identity(always=True),
        primary_key=True,
    )
    org_id: Mapped[Optional[_uuid.UUID]] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("organizations.id", ondelete="CASCADE"),
        nullable=True,
    )
    actor_id: Mapped[Optional[_uuid.UUID]] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("users.id", ondelete="RESTRICT"),
        nullable=True,
    )
    action: Mapped[str] = mapped_column(Text, nullable=False)
    # The subject of the action, as a table name plus id. No FK on either — see the
    # module docstring. `entity` is nullable because some actions have no subject
    # ("auth.login_failed").
    entity: Mapped[Optional[str]] = mapped_column(Text, nullable=True)
    entity_id: Mapped[Optional[_uuid.UUID]] = mapped_column(
        UUID(as_uuid=True),
        nullable=True,
    )
    # PostgreSQL `inet`, not text: it validates the value on the way in, stores v4 and
    # v6 in 7 or 19 bytes instead of 15 or 45 characters, and supports subnet
    # containment (`<<=`) so "everything from this /24" is an indexable query rather
    # than a LIKE. Nullable — a Celery worker's action has no client address.
    ip: Mapped[Optional[str]] = mapped_column(INET, nullable=True)
    # Nullable with no default, exactly as doc 03 §5 specifies — and worth noting
    # because `question_bank.meta` is NOT NULL DEFAULT '{}'. Two JSONB "meta" columns,
    # two different nullability rules, one document. Implemented literally in both
    # places and flagged: readers here must handle NULL, readers there need not.
    meta: Mapped[Optional[Any]] = mapped_column(JSONB, nullable=True)
    ts: Mapped[_dt.datetime] = mapped_column(
        DateTime(timezone=True),
        nullable=False,
        server_default=func.now(),
    )

    organization: Mapped[Optional["Organization"]] = relationship(
        "Organization",
        back_populates="audit_logs",
    )
    actor: Mapped[Optional["User"]] = relationship("User")

    __table_args__ = (
        # doc 03 §5's four indexes. Three are (dimension, ts) composites for the
        # "recent activity by X" query the Audit screen (S-16) runs; the fourth is the
        # polymorphic lookup, "everything that ever happened to this row", which is the
        # query an incident investigation starts from.
        Index("ix_audit_logs_org_id_ts", "org_id", "ts"),
        Index("ix_audit_logs_actor_id_ts", "actor_id", "ts"),
        Index("ix_audit_logs_entity_entity_id", "entity", "entity_id"),
        Index("ix_audit_logs_action_ts", "action", "ts"),
        # Partitioning is absent for the same reason as on `usage`: the Blueprint §8
        # DDL ends this table with `PARTITION BY RANGE (ts)`, but no interval or
        # retention is specified anywhere, and a partitioned table's primary key must
        # include the partition key — which doc 03's "id bigint identity, PK" excludes.
        # Plain table; gap reported.
    )

    def __repr__(self) -> str:  # pragma: no cover - debugging aid
        return (
            f"<AuditLog id={self.id!r} org_id={self.org_id!r} "
            f"action={self.action!r} entity={self.entity!r}>"
        )


class AnalyticsDaily(TimestampMixin, Base):
    """A rebuildable daily rollup, keyed by ``(org_id, day)``.

    The only composite *natural* primary key in the schema — every other table has a
    surrogate ``id``. That is doc 03 §5's explicit choice and it is the right one here:
    the key is the identity of the rollup, and making it the primary key is what makes
    the recompute an idempotent ``INSERT ... ON CONFLICT (org_id, day) DO UPDATE``
    instead of a delete-then-insert race with whoever is reading the dashboard.

    Note what is *not* here, per doc 03 §5: no ``deleted_at``, no ``row_version``. A
    cache does not need a tombstone, and optimistic locking is pointless when the
    writer is a single nightly job whose output is deterministic.
    """

    __tablename__ = "analytics_daily"

    org_id: Mapped[_uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("organizations.id", ondelete="CASCADE"),
        nullable=False,
    )
    # `date`, not `timestamptz`. Which calendar day a paper generated at 23:30 IST
    # belongs to is a timezone question this column cannot answer on its own; the
    # rollup job owns that decision (Asia/Kolkata for an Indian K-12 product), and
    # doc 03 does not state it. Reported — a rollup silently computed in UTC would
    # attribute late-evening activity to the wrong day.
    day: Mapped[_dt.date] = mapped_column(Date, nullable=False)
    # FR-8.2/FR-8.3's counters. NOT NULL with no server default, exactly as doc 03 §5
    # has it: its "Default values" list covers the timestamps only. So a rollup row
    # must supply every counter — which suits an UPSERT that always computes all of
    # them, and would be painful for anything doing partial updates.
    #
    # No non-negativity CHECKs. doc 03 asks for none, and the same restraint was
    # applied to `chapters.seq` in revision 0003; inventing constraints the design
    # document does not specify is how a schema stops matching its own docs.
    papers: Mapped[int] = mapped_column(Integer, nullable=False)
    ai_questions: Mapped[int] = mapped_column(Integer, nullable=False)
    retrieved_questions: Mapped[int] = mapped_column(Integer, nullable=False)
    # Required and numeric — and this is the sharpest unresolved point in the table.
    # `question_bank.difficulty` is a *text* vocabulary (easy/medium/hard, doc 03 §7.3
    # and FR-3.3), so an "average difficulty" presupposes a numeric mapping that no
    # document defines. Whatever the rollup job picks (1/2/3 is the obvious guess) will
    # be a load-bearing convention invented outside the schema, and two consumers
    # picking differently would produce charts that disagree without either being
    # wrong. The column is created exactly as specified and the missing mapping is
    # reported; unqualified `numeric` is used rather than a guessed precision, since
    # the scale depends on that mapping.
    avg_difficulty: Mapped[_decimal.Decimal] = mapped_column(Numeric, nullable=False)
    # FR-8.3's curriculum coverage: chapter and outcome spread, too irregular to
    # normalize and never queried relationally. JSONB per doc 03 §8.
    coverage: Mapped[Optional[Any]] = mapped_column(JSONB, nullable=True)

    organization: Mapped["Organization"] = relationship(
        "Organization",
        back_populates="analytics_daily",
    )

    __table_args__ = (
        PrimaryKeyConstraint("org_id", "day", name="pk_analytics_daily"),
        # doc 03 §5 asks for indexes on `day` and on `org_id`. The second is redundant
        # — the primary key's own unique index already leads with `org_id` — and it is
        # created anyway, for the same reason `ix_paper_versions_paper_id` is: doc 03
        # lists it in its own right, and a schema that quietly drops indexes its design
        # document specifies is one nobody can verify by reading. The `day` index is
        # not redundant and matters: "all tenants, one day" is the platform-wide
        # reporting query, and the PK cannot serve it.
        Index("ix_analytics_daily_day", "day"),
        Index("ix_analytics_daily_org_id", "org_id"),
    )

    def __repr__(self) -> str:  # pragma: no cover - debugging aid
        return (
            f"<AnalyticsDaily org_id={self.org_id!r} day={self.day!r} "
            f"papers={self.papers!r}>"
        )


__all__ = ["AnalyticsDaily", "AuditLog"]
