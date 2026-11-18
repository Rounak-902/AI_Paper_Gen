"""``organizations`` — the tenant master (doc 03 §5, "organizations").

Every tenant-owned table in the schema hangs off this one via ``org_id``. The RLS
policies that resolve against it are **not** written here: no ``CREATE POLICY``
statement exists anywhere in doc 03 or the ADRs, and the non-superuser role the
policies would apply to does not exist yet, so policy authorship belongs to P1-T5
alongside the repository layer that sets ``app.org_id``. P1-T4 delivers the ``org_id``
columns, their foreign keys and their indexes — everything a policy needs to be written
against, and nothing that pretends the policy exists.
"""

from __future__ import annotations

import uuid as _uuid
from typing import TYPE_CHECKING, Any, Optional

from sqlalchemy import ForeignKey, Text, text
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
    from app.models.audit import AnalyticsDaily, AuditLog
    from app.models.billing import Subscription, Usage
    from app.models.branch import Branch
    from app.models.membership import Membership
    from app.models.notification import Notification
    from app.models.paper import GeneratedPaper
    from app.models.program import Program
    from app.models.question import QuestionBank
    from app.models.template import Template
    from app.models.textbook import Textbook

#: doc 03 §5 says only "org_type constrained to approved tenant types" and does not
#: enumerate them. The four values come from the Blueprint §8 tenancy DDL, which is
#: the only place in the document set that spells them out.
ORG_TYPES: tuple[str, ...] = ("coaching_center", "school", "tutor", "publisher")

#: doc 03 §5 says "status constrained to lifecycle values" and never enumerates
#: them — not in §7.3, and the Blueprint §8 DDL declares the column with no CHECK
#: at all. This vocabulary was confirmed with the product owner during P1-T3 and is
#: pending an addition to doc 03 §7.3. Because it is a named CHECK on a text column
#: (never a native ENUM, per ADR-011's reasoning), adding a value later is a
#: one-line migration.
ORG_STATUSES: tuple[str, ...] = ("active", "suspended", "archived")


class Organization(
    UUIDPrimaryKeyMixin,
    TimestampMixin,
    SoftDeleteMixin,
    RowVersionMixin,
    Base,
):
    """Tenant master."""

    __tablename__ = "organizations"

    name: Mapped[str] = mapped_column(Text, nullable=False)
    org_type: Mapped[str] = mapped_column(
        Text,
        nullable=False,
        server_default=text("'coaching_center'"),
        index=True,
    )
    region: Mapped[str] = mapped_column(
        Text,
        nullable=False,
        server_default=text("'ap-south-1'"),
        index=True,
    )
    # doc 03 §5 lists this as a nullable FK to subscriptions.id. The column shipped in
    # revision 0002 *without* its REFERENCES clause, because `subscriptions` is step 12
    # of doc 03 §9 and did not exist yet; revision 0018 adds the constraint. That is the
    # expand-migrate-contract sequence of doc 03 §8 completing, not an oversight — and
    # the FK is declared here rather than left implicit so `alembic check` compares the
    # real thing.
    #
    # 0018 rather than 0012, which is a judgment call and is recorded as one. §9 step 12
    # says only "Create subscriptions"; step 4 says "Create curriculum_versions *and wire
    # version relations*"; step 18 says "Add indexes, constraints, ...". Since §9
    # demonstrably says "and wire" where it wants wiring, and does not here, the
    # constraint belongs to the step that names constraints outright. The schema at head
    # is identical either way — only the intermediate revision at which the cycle closes
    # differs. See revision 0018's docstring.
    #
    # `use_alter=True` is what keeps the resulting cycle harmless. organizations and
    # subscriptions now reference each other, so no linear table ordering satisfies both
    # constraints; the flag tells SQLAlchemy to emit this one FK as a separate ALTER and
    # to drop the edge from its topological sort, which is exactly what revision 0018
    # does by hand. It changes no DDL that Alembic compares — the constraint's name,
    # columns, referent and ON DELETE action are all unaffected — it only stops
    # `sort_tables` raising CircularDependencyError for any code that walks the metadata
    # (`create_all`, a future `alembic revision --autogenerate`, a diagram generator).
    subscription_id: Mapped[Optional[_uuid.UUID]] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("subscriptions.id", ondelete="SET NULL", use_alter=True),
        nullable=True,
    )
    settings: Mapped[dict[str, Any]] = mapped_column(
        JSONB,
        nullable=False,
        server_default=text("'{}'::jsonb"),
    )
    status: Mapped[str] = mapped_column(
        Text,
        nullable=False,
        server_default=text("'active'"),
        index=True,
    )

    branches: Mapped[list["Branch"]] = relationship(
        "Branch",
        back_populates="organization",
        # Default cascade, stated explicitly: ORM-level delete cascade is
        # deliberately absent. ADR-004 forbids hard deletes from application code,
        # so `session.delete(org)` must never quietly remove child rows. The
        # database-level ON DELETE CASCADE exists only for the hard-delete
        # maintenance path (tenant offboarding, GDPR erasure).
        cascade="save-update, merge",
    )
    memberships: Mapped[list["Membership"]] = relationship(
        "Membership",
        back_populates="organization",
        cascade="save-update, merge",
    )
    # Added in P1-T4. These are the org-owned tables of doc 03 §3 "One-to-many"
    # (organizations → programs / question_bank / textbooks / templates). Purely
    # additive: a relationship() emits no DDL, so migrations 0001 and 0002 are
    # untouched and `alembic check` stays clean — which the P1-T4 verification script
    # proves rather than assumes.
    programs: Mapped[list["Program"]] = relationship(
        "Program",
        back_populates="organization",
        cascade="save-update, merge",
    )
    questions: Mapped[list["QuestionBank"]] = relationship(
        "QuestionBank",
        back_populates="organization",
        cascade="save-update, merge",
    )
    textbooks: Mapped[list["Textbook"]] = relationship(
        "Textbook",
        back_populates="organization",
        cascade="save-update, merge",
    )
    templates: Mapped[list["Template"]] = relationship(
        "Template",
        back_populates="organization",
        cascade="save-update, merge",
    )
    # The remaining six, added with migrations 0010-0017 — one per
    # `organizations ||--o{ X` edge in doc 03 §6 that was still missing. Same
    # `save-update, merge` cascade as above and for the same ADR-004 reason: the
    # database has ON DELETE CASCADE for the offboarding path, and the ORM must not
    # give application code a way to trigger it by accident.
    papers: Mapped[list["GeneratedPaper"]] = relationship(
        "GeneratedPaper",
        back_populates="organization",
        cascade="save-update, merge",
    )
    # `foreign_keys` is required on both sides of this pair, not optional: there are now
    # two foreign keys between these tables (subscriptions.org_id and
    # organizations.subscription_id), so SQLAlchemy cannot infer which one a
    # relationship travels along and raises AmbiguousForeignKeysError without the hint.
    subscriptions: Mapped[list["Subscription"]] = relationship(
        "Subscription",
        back_populates="organization",
        foreign_keys="Subscription.org_id",
        cascade="save-update, merge",
    )
    # The denormalized "which contract is in force" pointer, mapping the column doc 03
    # §5 puts on this table. doc 03 §6 does not draw this edge — it shows only
    # `organizations ||--o{ subscriptions` — so the reverse side is deliberately left
    # unmapped, the same treatment `question_bank`'s board/class/subject references get.
    #
    # `post_update=True` is what makes the cycle survive a flush. With two rows that
    # reference each other, no single INSERT ordering satisfies both foreign keys;
    # post_update tells the unit of work to insert the organization with a NULL pointer
    # and issue a follow-up UPDATE. Without it, creating an organization and its first
    # subscription in one flush raises CircularDependencyError.
    current_subscription: Mapped[Optional["Subscription"]] = relationship(
        "Subscription",
        foreign_keys=lambda: [Organization.subscription_id],
        post_update=True,
    )
    usage_records: Mapped[list["Usage"]] = relationship(
        "Usage",
        back_populates="organization",
        cascade="save-update, merge",
    )
    audit_logs: Mapped[list["AuditLog"]] = relationship(
        "AuditLog",
        back_populates="organization",
        cascade="save-update, merge",
    )
    analytics_daily: Mapped[list["AnalyticsDaily"]] = relationship(
        "AnalyticsDaily",
        back_populates="organization",
        cascade="save-update, merge",
    )
    notifications: Mapped[list["Notification"]] = relationship(
        "Notification",
        back_populates="organization",
        cascade="save-update, merge",
    )

    __table_args__ = (
        check_in("org_type", ORG_TYPES, name="org_type"),
        check_in("status", ORG_STATUSES, name="status"),
        row_version_positive(),
        # doc 03 §5 lists exactly three indexes for this table — status, region,
        # org_type — and they are declared with index=True above. Nothing else is
        # added: no index on `deleted_at`, because almost every row has it NULL and
        # a low-selectivity index would not be chosen; the right shape when the
        # repository's `deleted_at IS NULL` filter needs help is a *partial* index
        # on the filtered column, which P1-T4 owns (doc 03 §9 step 18).
        #
        # No unique constraint either: doc 03 §5 says "none beyond id unless a
        # tenant code is later introduced".
    )

    def __repr__(self) -> str:  # pragma: no cover - debugging aid
        return f"<Organization id={self.id!r} name={self.name!r} status={self.status!r}>"


__all__ = ["ORG_STATUSES", "ORG_TYPES", "Organization"]
