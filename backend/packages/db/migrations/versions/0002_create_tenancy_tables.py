"""create tenancy tables (doc 03 §9 step 2)

Revision ID: 0002
Revises: 0001
Create Date: 2026-08-16

doc 03 §9 step 2: "Create organizations, branches, users, memberships." Four tables,
no more — ``boards``/``classes``/``subjects``/``chapters``/``learning_outcomes`` are
step 3 and ``curriculum_versions`` is step 4, both owned by P1-T4.

Every literal in this file is frozen on purpose. It would be tidier to import
``ORG_TYPES`` or ``MEMBERSHIP_ROLES`` from ``app.models``, and it would be wrong: a
migration is a snapshot of the schema at a point in history, and importing a live
constant means editing a model silently rewrites what an already-applied revision
claims to have done. Alembic's own autogenerate inlines these for the same reason.

Two shapes here differ from a naive reading of doc 03 §5, both deliberately:

* **Uniqueness is partial.** doc 03 says "unique(org_id, name)", "email unique
  globally", "unique(user_id, org_id)". Each ships as a unique *index* with
  ``WHERE deleted_at IS NULL``, which is ADR-004's own recorded consequence of soft
  deletes: "a soft-deleted row still occupies its unique key, so 'delete and recreate
  with the same name' fails unless the constraint is partial". A plain UNIQUE
  constraint would make a soft-deleted branch hold its name forever.

* **``organizations.subscription_id`` has no FK yet.** doc 03 §5 documents it as a
  nullable reference to ``subscriptions.id``; ``subscriptions`` is §9 step 12 and does
  not exist. The column ships now, the constraint lands in P1-T4 — the expand step of
  the expand-migrate-contract rule in doc 03 §8.
"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

# revision identifiers, used by Alembic.
revision: str = "0002"
down_revision: Union[str, Sequence[str], None] = "0001"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None

_SOFT_DELETE_PREDICATE = "deleted_at IS NULL"


def upgrade() -> None:
    """Upgrade schema."""

    # ------------------------------------------------------------------
    # organizations — tenant master (doc 03 §5)
    # ------------------------------------------------------------------
    op.create_table(
        "organizations",
        sa.Column(
            "id",
            postgresql.UUID(as_uuid=True),
            server_default=sa.text("gen_random_uuid()"),
            nullable=False,
        ),
        sa.Column("name", sa.Text(), nullable=False),
        sa.Column(
            "org_type",
            sa.Text(),
            server_default=sa.text("'coaching_center'"),
            nullable=False,
        ),
        sa.Column(
            "region",
            sa.Text(),
            server_default=sa.text("'ap-south-1'"),
            nullable=False,
        ),
        sa.Column("subscription_id", postgresql.UUID(as_uuid=True), nullable=True),
        sa.Column(
            "settings",
            postgresql.JSONB(astext_type=sa.Text()),
            server_default=sa.text("'{}'::jsonb"),
            nullable=False,
        ),
        sa.Column("status", sa.Text(), server_default=sa.text("'active'"), nullable=False),
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
            "org_type IN ('coaching_center', 'school', 'tutor', 'publisher')",
            name=op.f("ck_organizations_org_type"),
        ),
        sa.CheckConstraint(
            "status IN ('active', 'suspended', 'archived')",
            name=op.f("ck_organizations_status"),
        ),
        sa.CheckConstraint(
            "row_version > 0",
            name=op.f("ck_organizations_row_version_positive"),
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_organizations")),
    )
    # doc 03 §5 indexes for organizations: status, region, org_type.
    op.create_index(op.f("ix_organizations_org_type"), "organizations", ["org_type"])
    op.create_index(op.f("ix_organizations_region"), "organizations", ["region"])
    op.create_index(op.f("ix_organizations_status"), "organizations", ["status"])

    # ------------------------------------------------------------------
    # branches — optional organization subdivision (doc 03 §5)
    # ------------------------------------------------------------------
    op.create_table(
        "branches",
        sa.Column(
            "id",
            postgresql.UUID(as_uuid=True),
            server_default=sa.text("gen_random_uuid()"),
            nullable=False,
        ),
        sa.Column("org_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("name", sa.Text(), nullable=False),
        sa.Column("region", sa.Text(), nullable=True),
        sa.Column("status", sa.Text(), server_default=sa.text("'active'"), nullable=False),
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
            "status IN ('active', 'suspended', 'archived')",
            name=op.f("ck_branches_status"),
        ),
        sa.CheckConstraint("row_version > 0", name=op.f("ck_branches_row_version_positive")),
        # doc 03 §5: "deleting an organization cascades branches".
        sa.ForeignKeyConstraint(
            ["org_id"],
            ["organizations.id"],
            name=op.f("fk_branches_org_id_organizations"),
            ondelete="CASCADE",
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_branches")),
    )
    op.create_index(op.f("ix_branches_org_id"), "branches", ["org_id"])
    op.create_index(op.f("ix_branches_status"), "branches", ["status"])
    # doc 03 §5 "unique(org_id, name)", partial per ADR-004.
    op.create_index(
        "uq_branches_org_id_name",
        "branches",
        ["org_id", "name"],
        unique=True,
        postgresql_where=sa.text(_SOFT_DELETE_PREDICATE),
    )

    # ------------------------------------------------------------------
    # users — identity record (doc 03 §5). No role column: role is per
    # organization and lives on memberships (ADR-011, doc 03 §7.2).
    # ------------------------------------------------------------------
    op.create_table(
        "users",
        sa.Column(
            "id",
            postgresql.UUID(as_uuid=True),
            server_default=sa.text("gen_random_uuid()"),
            nullable=False,
        ),
        # citext, which is why revision 0001 has to run first.
        sa.Column("email", postgresql.CITEXT(), nullable=False),
        # Nullable for SSO-only accounts (doc 03 §5).
        sa.Column("password_hash", sa.Text(), nullable=True),
        # `full_name`, not `name` — ADR-012 §1 settled this; the document was the
        # side that had drifted.
        sa.Column("full_name", sa.Text(), nullable=False),
        # ADR-012 §2 / drift migration D-1: authentication needs a way to disable an
        # identity without deleting it (FR-1.6, AC-1). Because this revision creates
        # the table from nothing, the column is born NOT NULL with the CHECK in place;
        # D-1's add-nullable → backfill → set-NOT-NULL dance is only needed against a
        # database that already has rows.
        sa.Column("status", sa.Text(), server_default=sa.text("'active'"), nullable=False),
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
            "status IN ('active', 'disabled', 'suspended')",
            name=op.f("ck_users_status"),
        ),
        sa.CheckConstraint("row_version > 0", name=op.f("ck_users_row_version_positive")),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_users")),
    )
    op.create_index(op.f("ix_users_status"), "users", ["status"])
    # doc 03 §5 "email unique globally", partial per ADR-004.
    op.create_index(
        "uq_users_email",
        "users",
        ["email"],
        unique=True,
        postgresql_where=sa.text(_SOFT_DELETE_PREDICATE),
    )

    # ------------------------------------------------------------------
    # memberships — org-scoped access and role assignment (doc 03 §5)
    # ------------------------------------------------------------------
    op.create_table(
        "memberships",
        sa.Column(
            "id",
            postgresql.UUID(as_uuid=True),
            server_default=sa.text("gen_random_uuid()"),
            nullable=False,
        ),
        sa.Column("user_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("org_id", postgresql.UUID(as_uuid=True), nullable=False),
        # ADR-012 §4 / drift migration D-2. Null means org-wide access.
        sa.Column("branch_id", postgresql.UUID(as_uuid=True), nullable=True),
        sa.Column("role", sa.Text(), nullable=False),
        sa.Column("status", sa.Text(), server_default=sa.text("'active'"), nullable=False),
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
        # ADR-011: exactly three implemented roles, as a CHECK on a text column and
        # never a native ENUM, so that adding 'hod' / 'reviewer' / 'viewer' later is a
        # one-line constraint migration instead of an irreversible ALTER TYPE.
        sa.CheckConstraint(
            "role IN ('teacher', 'coaching_admin', 'super_admin')",
            name=op.f("ck_memberships_role"),
        ),
        # ADR-012 §3 / drift migration D-3. 'invited' is a membership that exists and
        # deliberately grants nothing yet (FR-1.5).
        sa.CheckConstraint(
            "status IN ('active', 'invited', 'suspended')",
            name=op.f("ck_memberships_status"),
        ),
        sa.CheckConstraint(
            "row_version > 0",
            name=op.f("ck_memberships_row_version_positive"),
        ),
        # doc 03 §5: "deleting a branch should set branch_id to null" — losing a
        # branch widens a member's scope to the org rather than revoking access.
        sa.ForeignKeyConstraint(
            ["branch_id"],
            ["branches.id"],
            name=op.f("fk_memberships_branch_id_branches"),
            ondelete="SET NULL",
        ),
        sa.ForeignKeyConstraint(
            ["org_id"],
            ["organizations.id"],
            name=op.f("fk_memberships_org_id_organizations"),
            ondelete="CASCADE",
        ),
        sa.ForeignKeyConstraint(
            ["user_id"],
            ["users.id"],
            name=op.f("fk_memberships_user_id_users"),
            ondelete="CASCADE",
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_memberships")),
    )
    # doc 03 §5 indexes for memberships: org_id, role, branch_id, user_id, status.
    op.create_index(op.f("ix_memberships_branch_id"), "memberships", ["branch_id"])
    op.create_index(op.f("ix_memberships_org_id"), "memberships", ["org_id"])
    op.create_index(op.f("ix_memberships_role"), "memberships", ["role"])
    op.create_index(op.f("ix_memberships_status"), "memberships", ["status"])
    op.create_index(op.f("ix_memberships_user_id"), "memberships", ["user_id"])
    # doc 03 §5 "unique(user_id, org_id)", partial per ADR-004 so a revoked member
    # can be reinstated instead of colliding with their own tombstone.
    op.create_index(
        "uq_memberships_user_id_org_id",
        "memberships",
        ["user_id", "org_id"],
        unique=True,
        postgresql_where=sa.text(_SOFT_DELETE_PREDICATE),
    )


def downgrade() -> None:
    """Downgrade schema.

    Fully reversible: these four tables are created from nothing, so dropping them
    restores the previous state exactly. Dropped in reverse dependency order so no
    foreign key blocks a drop. The explicit ``drop_index`` calls are redundant —
    ``DROP TABLE`` takes an index with it — but they mirror what autogenerate emits
    and they make the reversal auditable line by line.
    """

    op.drop_index("uq_memberships_user_id_org_id", table_name="memberships")
    op.drop_index(op.f("ix_memberships_user_id"), table_name="memberships")
    op.drop_index(op.f("ix_memberships_status"), table_name="memberships")
    op.drop_index(op.f("ix_memberships_role"), table_name="memberships")
    op.drop_index(op.f("ix_memberships_org_id"), table_name="memberships")
    op.drop_index(op.f("ix_memberships_branch_id"), table_name="memberships")
    op.drop_table("memberships")

    op.drop_index("uq_users_email", table_name="users")
    op.drop_index(op.f("ix_users_status"), table_name="users")
    op.drop_table("users")

    op.drop_index("uq_branches_org_id_name", table_name="branches")
    op.drop_index(op.f("ix_branches_status"), table_name="branches")
    op.drop_index(op.f("ix_branches_org_id"), table_name="branches")
    op.drop_table("branches")

    op.drop_index(op.f("ix_organizations_status"), table_name="organizations")
    op.drop_index(op.f("ix_organizations_region"), table_name="organizations")
    op.drop_index(op.f("ix_organizations_org_type"), table_name="organizations")
    op.drop_table("organizations")
