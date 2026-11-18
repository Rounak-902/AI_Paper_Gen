"""``subscriptions`` / ``usage`` (doc 03 §5, §9 steps 12-13).

doc 03 §4's **Billing** group. Two tables with opposite temperaments: a subscription is
a mutable contract that gets amended, and a usage row is a meter reading that never
changes once written.

Creating ``subscriptions`` is also what makes the tenancy loop *closeable*.
``organizations.subscription_id`` shipped in revision 0002 as a bare UUID column with no
``REFERENCES`` clause, because its target table did not exist yet
(expand-migrate-contract, doc 03 §8). Revision 0012 supplies the target; **revision 0018
adds the constraint**, on the reading that doc 03 §9 step 12 says only "Create
subscriptions" while step 18 says "Add indexes, constraints, ...". See
``app/models/organization.py`` for what the resulting circularity does and does not cost,
and revision 0018's docstring for why the constraint was placed there rather than here.

Why ``usage.user_id`` is RESTRICT while ``question_bank.created_by`` is SET NULL
-------------------------------------------------------------------------------
``ON DELETE SET NULL`` is an UPDATE. On a table doc 03 describes as *append-only*, an
UPDATE is precisely the operation that is not allowed — so the append-only tables in
this schema (``usage``, ``audit_logs``, ``approvals``) use RESTRICT for their
attribution columns, and the mutable tables use SET NULL. One rule, applied
consistently; the cost is that a user who has ever consumed metered quota cannot be
hard-deleted, only disabled (``users.status``, ADR-012 §2).

``org_id`` goes the other way and is CASCADE on both tables. That is the one place
where "append-only" and "delete a tenant" genuinely collide, and the resolution is
that a hard organization delete is a compliance erasure rather than a normal
operation — doc 03 §5's own "no normal hard delete" wording concedes the abnormal
case exists. Deleting the tenant takes its meter readings with it.

Partitioning is deliberately absent
-----------------------------------
doc 03 §5 says "time partition lifecycle handles archival" for this table, and the
Blueprint §8 DDL ends it with ``PARTITION BY RANGE (ts)``. Neither names a partition
interval, a retention window, or who creates next month's partition — and a
partitioned table's primary key must contain the partition key, which contradicts
doc 03's "id bigint identity, PK" outright. ``usage`` is therefore created as a plain
table and the gap is reported. Converting later is a real migration (create
partitioned parent, copy, swap), not an ``ALTER``, which is exactly why it needs a
decision rather than a guess.
"""

from __future__ import annotations

import datetime as _dt
import decimal as _decimal
import uuid as _uuid
from typing import TYPE_CHECKING, Optional

from sqlalchemy import (
    BigInteger,
    CheckConstraint,
    Date,
    DateTime,
    ForeignKey,
    Identity,
    Index,
    Integer,
    Numeric,
    Text,
    UniqueConstraint,
    func,
    text,
)
from sqlalchemy.dialects.postgresql import UUID
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
    from app.models.user import User

#: doc 03 §5 says only "plan constrained to plan values" and §7.3 has no row for this
#: column. These four come from the Blueprint §8 DDL comment
#: (``plan text NOT NULL, -- free/standard/pro/enterprise``), the only enumeration of
#: them in the document set — the same precedent as
#: :data:`app.models.organization.ORG_TYPES`.
#:
#: What each plan *entitles* (seat counts, paper quotas, AI token ceilings) is
#: deliberately not modelled: doc 03 defines no ``plans`` table and no limits columns,
#: and BR-10/FR-8.5's quota enforcement needs numbers that live in configuration.
#: ``seats`` is the single entitlement doc 03 puts on the row.
SUBSCRIPTION_PLANS: tuple[str, ...] = ("free", "standard", "pro", "enterprise")

#: doc 03 §5 hedges here — "status constrained to active/past_due/canceled/suspended
#: **style** values" — so the four names are taken literally and the hedge is reported
#: rather than treated as licence to invent. ``trialing`` and ``incomplete``, which
#: every payment provider emits, are absent; adding one is a one-line CHECK migration
#: (ADR-011), which is the whole reason controlled vocabularies are CHECK constraints
#: here and not native ENUMs.
#:
#: ``canceled`` keeps the single-l American spelling used by doc 03 and by the payment
#: providers' own APIs, against ``cancelled`` elsewhere in the prose. A vocabulary
#: value is a wire format, not English.
SUBSCRIPTION_STATUSES: tuple[str, ...] = (
    "active",
    "past_due",
    "canceled",
    "suspended",
)

#: doc 03 §5 says "metric constrained to approved meter names" without listing them;
#: the Blueprint §8 DDL comment gives ``papers_generated, ai_tokens, ocr_pages``.
#:
#: This CHECK is the narrowest judgment call in the group, because a *closed*
#: vocabulary on a metering column means an unrecognised meter name is a failed INSERT
#: — and metering happens inside request handlers. The original three values covered
#: every metered event named in FR-8.5, EH-13 and AI-1 at P1. P6-T2 explicitly
#: requires a per-export event, so revision 0037 adds ``exports_generated`` and widens
#: the CHECK with the export artifact table.
USAGE_METRICS: tuple[str, ...] = (
    "papers_generated",
    "ai_tokens",
    "ocr_pages",
    "exports_generated",
)


class Subscription(
    UUIDPrimaryKeyMixin,
    TimestampMixin,
    SoftDeleteMixin,
    RowVersionMixin,
    Base,
):
    """One organization's billing contract.

    Nothing here enforces "one live subscription per organization". doc 03 §5 lists
    ``org_id`` as a plain index, not a unique one, and that is the right call: plan
    changes are modelled as a new row with a new period, so an organization
    legitimately accumulates a history of them. Which row is *current* is a question
    about ``period_end`` and ``status``, resolved by the service layer —
    ``organizations.subscription_id`` is the denormalized pointer that makes the
    answer cheap.
    """

    __tablename__ = "subscriptions"

    org_id: Mapped[_uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("organizations.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )
    plan: Mapped[str] = mapped_column(Text, nullable=False)
    # Nullable: seat-based billing is one plan shape among several, and a metered or
    # unlimited plan has no seat count. Note that nothing joins this to
    # `memberships` — "seats in use" is a COUNT, and doc 03 defines no trigger or
    # CHECK to keep the two in step. Over-subscription is a service-layer concern
    # (BR-10).
    seats: Mapped[Optional[int]] = mapped_column(Integer, nullable=True)
    status: Mapped[str] = mapped_column(
        Text,
        nullable=False,
        server_default=text("'active'"),
        index=True,
    )
    # `date`, not `timestamptz`: a billing period is a calendar concept, and doc 03 §5
    # types both columns as `date`. Nullable because a free plan has no period.
    #
    # No CHECK that period_end >= period_start. doc 03 §5 asks for none, and a
    # half-open period with a NULL end (an open-ended enterprise contract) is a real
    # state — so the constraint would have to be written as an implication to avoid
    # rejecting it. Not invented; reported.
    period_start: Mapped[Optional[_dt.date]] = mapped_column(Date, nullable=True)
    period_end: Mapped[Optional[_dt.date]] = mapped_column(
        Date,
        nullable=True,
        index=True,
    )
    # The payment provider's own subscription id. A reference, not a URL, and not a
    # secret: it is safe to log and is the join key for webhook handling.
    provider_ref: Mapped[Optional[str]] = mapped_column(Text, nullable=True)

    organization: Mapped["Organization"] = relationship(
        "Organization",
        back_populates="subscriptions",
        foreign_keys=lambda: [Subscription.org_id],
    )

    __table_args__ = (
        check_in("plan", SUBSCRIPTION_PLANS, name="plan"),
        check_in("status", SUBSCRIPTION_STATUSES, name="status"),
        # doc 03 §5: "provider_ref unique if present". A plain UNIQUE constraint is
        # already exactly that, because PostgreSQL unique constraints are NULLS
        # DISTINCT — any number of rows may leave the column NULL, and two rows may
        # not share a non-NULL value. No `WHERE provider_ref IS NOT NULL` predicate is
        # needed to get the stated semantics.
        #
        # The deliberate deviation from ADR-004 is the *absence* of a
        # `WHERE deleted_at IS NULL` term, which every other uniqueness rule in this
        # schema carries. ADR-004's predicate exists so a human-chosen name can be
        # reused after a soft delete. A provider reference is not human-chosen and is
        # never reused — a cancel-and-resubscribe mints a new one — so scoping the
        # constraint to live rows would buy nothing and cost correctness: the webhook
        # handler looks a provider_ref up expecting one row, and a soft-deleted
        # duplicate would hand it two. Flagged as a conscious exception.
        UniqueConstraint("provider_ref", name="uq_subscriptions_provider_ref"),
        row_version_positive(),
        # doc 03 §5 indexes: org_id, status, period_end — all three `index=True` above.
        # period_end drives the renewal sweep and the NT-7 expiry warning.
    )

    def __repr__(self) -> str:  # pragma: no cover - debugging aid
        return (
            f"<Subscription id={self.id!r} org_id={self.org_id!r} "
            f"plan={self.plan!r} status={self.status!r}>"
        )


class Usage(Base):
    """One append-only meter reading (CN-22).

    The first table in this schema without a UUID primary key. doc 03 §5 specifies
    ``id bigint identity``, and for a high-volume insert-only log that is the better
    choice on its own merits: a monotonic bigint appends to the right-hand edge of its
    btree instead of scattering random 16-byte keys across it, and the id is half the
    width in every index that carries it.

    ``GENERATED ALWAYS`` rather than ``BY DEFAULT``, per the Blueprint §8 DDL. It
    means an INSERT cannot supply its own id even by accident, which is the correct
    posture for a meter: a client-chosen sequence number in an append-only ledger is a
    way to overwrite history.

    No ``deleted_at``, no ``row_version``, no ``updated_at`` — doc 03 §5 lists none of
    them, and each would be a claim that a meter reading can be revised. Corrections
    are modelled the way ledgers model them: another row.
    """

    __tablename__ = "usage"

    id: Mapped[int] = mapped_column(
        BigInteger,
        Identity(always=True),
        primary_key=True,
    )
    org_id: Mapped[_uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("organizations.id", ondelete="CASCADE"),
        nullable=False,
    )
    # RESTRICT, not SET NULL — see the module docstring. Nullable because system-level
    # consumption (a scheduled re-index, a platform-wide OCR backfill) has no user.
    user_id: Mapped[Optional[_uuid.UUID]] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("users.id", ondelete="RESTRICT"),
        nullable=True,
    )
    metric: Mapped[str] = mapped_column(Text, nullable=False)
    # Unqualified `numeric`, exactly as doc 03 §5 types it: arbitrary precision, no
    # scale limit. Deliberate rather than lazy — the three meters have incompatible
    # magnitudes (`papers_generated` counts in ones, `ai_tokens` in millions), so any
    # precision chosen here would eventually clip one of them. Exact arithmetic also
    # matters more than usual: these values are summed to bill people.
    qty: Mapped[_decimal.Decimal] = mapped_column(Numeric, nullable=False)
    ts: Mapped[_dt.datetime] = mapped_column(
        DateTime(timezone=True),
        nullable=False,
        server_default=func.now(),
    )

    organization: Mapped["Organization"] = relationship(
        "Organization",
        back_populates="usage_records",
    )
    user: Mapped[Optional["User"]] = relationship("User")

    __table_args__ = (
        check_in("metric", USAGE_METRICS, name="metric"),
        # doc 03 §5: "qty positive" — strictly positive, so a zero-quantity row is
        # rejected. That is the stricter reading of the two, and it is the right one:
        # AI-1 says "zero AI usage is metered" by *not writing a row*, so a 0 here
        # would be indistinguishable from a bookkeeping bug. It does mean a refund or
        # correction cannot be expressed as a negative quantity — reported, since a
        # ledger normally wants that option.
        CheckConstraint("qty > 0", name="qty_positive"),
        # doc 03 §5 indexes, both composite and both leading with the tenant: the quota
        # rollup is "sum this metric for this org in this window", and the per-user
        # view is "what has this person consumed lately".
        Index("ix_usage_org_id_metric_ts", "org_id", "metric", "ts"),
        Index("ix_usage_user_id_ts", "user_id", "ts"),
    )

    def __repr__(self) -> str:  # pragma: no cover - debugging aid
        return (
            f"<Usage id={self.id!r} org_id={self.org_id!r} "
            f"metric={self.metric!r} qty={self.qty!r}>"
        )


__all__ = [
    "SUBSCRIPTION_PLANS",
    "SUBSCRIPTION_STATUSES",
    "USAGE_METRICS",
    "Subscription",
    "Usage",
]
