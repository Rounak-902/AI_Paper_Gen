"""create invitations table with RLS (P1-T8)

Revision ID: 0026
Revises: 0025
Create Date: 2026-08-23

Not a doc 03 §9 step. Doc 03 defines no ``invitations`` table anywhere — no §5
column list, no §7 data-dictionary entry — and **ADR-012 §3 explicitly rejects
one** in favour of ``memberships.status = 'invited'``. Both P1-T3 and P1-T4
therefore declined to create it.

Why it is created here anyway, in one sentence: ``memberships.user_id`` is
``NOT NULL``, so an invitation addressed to an email with no account cannot be a
``memberships`` row, and P1-T8's required accept flow is precisely the one that
*creates* the account. The full argument, including how ADR-012's model is still
honoured for already-registered invitees, is in
``app/models/invitation.py``'s module docstring. ADR-012 needs an amendment; that
is flagged in the P1-T8 report rather than done here.

What this revision does
-----------------------
1. ``CREATE TABLE public.invitations`` matching ``app/models/invitation.py``.
2. Three indexes, two of them partial-unique per ADR-004.
3. ``ENABLE`` + ``FORCE ROW LEVEL SECURITY`` and a ``tenant_isolation_policy``
   with the **same shape** revision 0025 gave every other ``org_id NOT NULL``
   table — except that the predicate is written in the hardened
   ``NULLIF(current_setting('app.org_id', true), '')`` form from the start.
   Revision 0027 retrofits that form onto ``memberships``; there is no reason to
   ship a new table with the unhardened spelling and then immediately fix it.

No explicit GRANT statements
----------------------------
Revision 0024 ran ``ALTER DEFAULT PRIVILEGES ... GRANT SELECT, INSERT, UPDATE ON
TABLES TO app_user`` for exactly this case, so a table created by a later
revision inherits the grants automatically. Verified in the P1-T8 harness rather
than assumed: proof 2 performs an INSERT and a SELECT against this table as
``app_user``.

Note the consequence, which is deliberate: ``app_user`` has **no DELETE grant on
anything** (0024 never issued one). Revocation is therefore an UPDATE that sets
``status = 'revoked'``, and the route is ``POST /invitations/{id}/revoke`` rather
than ``DELETE /invitations/{id}``. A ``DELETE`` route would have been a route that
can only ever return 500.

No percent signs
----------------
Same constraint as revisions 0024 and 0025: Alembic wraps ``op.execute`` strings
in ``text()``, and asyncpg's ``format`` paramstyle treats ``%`` as the start of a
placeholder. Every string below is percent-free; this note exists so a future
edit does not reintroduce one.
"""

from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

# revision identifiers, used by Alembic.
revision: str = "0026"
down_revision: Union[str, Sequence[str], None] = "0025"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None

_APP_ROLE = "app_user"
_TABLE = "invitations"

#: Kept in sync with ``app.models.membership.MEMBERSHIP_ROLES`` and
#: ``app.models.invitation.INVITATION_STATUSES``. Written out as literals rather
#: than imported, because a migration must describe the schema **at this point in
#: history** — importing the live model would make an old revision change meaning
#: the next time someone edits the vocabulary.
_ROLES = ("teacher", "coaching_admin", "super_admin")
_STATUSES = ("pending", "accepted", "revoked", "expired")


def _in_list(column: str, values: tuple[str, ...]) -> str:
    rendered = ", ".join(f"'{v}'" for v in values)
    return f"{column} IN ({rendered})"


def upgrade() -> None:
    """Upgrade schema."""
    op.create_table(
        _TABLE,
        sa.Column(
            "id",
            postgresql.UUID(as_uuid=True),
            server_default=sa.text("gen_random_uuid()"),
            nullable=False,
        ),
        sa.Column("org_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("email", postgresql.CITEXT(), nullable=False),
        sa.Column("role", sa.Text(), nullable=False),
        sa.Column("branch_id", postgresql.UUID(as_uuid=True), nullable=True),
        sa.Column("invited_by", postgresql.UUID(as_uuid=True), nullable=True),
        sa.Column("token_hash", sa.Text(), nullable=False),
        sa.Column("expires_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column(
            "status", sa.Text(), server_default=sa.text("'pending'"), nullable=False
        ),
        sa.Column("accepted_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column(
            "accepted_user_id", postgresql.UUID(as_uuid=True), nullable=True
        ),
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
        sa.Column(
            "row_version", sa.Integer(), server_default=sa.text("1"), nullable=False
        ),
        sa.PrimaryKeyConstraint("id", name="pk_invitations"),
        sa.ForeignKeyConstraint(
            ["org_id"],
            ["organizations.id"],
            name="fk_invitations_org_id_organizations",
            ondelete="CASCADE",
        ),
        sa.ForeignKeyConstraint(
            ["branch_id"],
            ["branches.id"],
            name="fk_invitations_branch_id_branches",
            ondelete="SET NULL",
        ),
        sa.ForeignKeyConstraint(
            ["invited_by"],
            ["users.id"],
            name="fk_invitations_invited_by_users",
            ondelete="SET NULL",
        ),
        sa.ForeignKeyConstraint(
            ["accepted_user_id"],
            ["users.id"],
            name="fk_invitations_accepted_user_id_users",
            ondelete="SET NULL",
        ),
        sa.CheckConstraint(_in_list("role", _ROLES), name="ck_invitations_role"),
        sa.CheckConstraint(_in_list("status", _STATUSES), name="ck_invitations_status"),
        sa.CheckConstraint(
            "row_version > 0", name="ck_invitations_row_version_positive"
        ),
    )

    op.create_index("ix_invitations_org_id", _TABLE, ["org_id"])
    op.create_index("ix_invitations_email", _TABLE, ["email"])
    op.create_index("ix_invitations_status", _TABLE, ["status"])
    op.create_index("ix_invitations_expires_at", _TABLE, ["expires_at"])

    # Partial-unique per ADR-004: a soft-deleted row must not keep occupying its
    # unique key.
    op.create_index(
        "uq_invitations_token_hash",
        _TABLE,
        ["token_hash"],
        unique=True,
        postgresql_where=sa.text("deleted_at IS NULL"),
    )
    # At most one *open* ask per address per org. Narrower than (org_id, email)
    # alone so that revoke-then-reinvite works.
    op.create_index(
        "uq_invitations_org_id_email",
        _TABLE,
        ["org_id", "email"],
        unique=True,
        postgresql_where=sa.text("deleted_at IS NULL AND status = 'pending'"),
    )

    # -- RLS -------------------------------------------------------------------
    op.execute(f"ALTER TABLE public.{_TABLE} ENABLE ROW LEVEL SECURITY")
    op.execute(f"ALTER TABLE public.{_TABLE} FORCE ROW LEVEL SECURITY")
    op.execute(
        f"CREATE POLICY tenant_isolation_policy ON public.{_TABLE} "
        f"FOR ALL TO {_APP_ROLE} "
        f"USING (org_id = NULLIF(current_setting('app.org_id', true), '')::uuid) "
        f"WITH CHECK (org_id = NULLIF(current_setting('app.org_id', true), '')::uuid)"
    )


def downgrade() -> None:
    """Downgrade schema.

    ``DROP TABLE`` removes the policy and the indexes with it; the explicit
    ``DROP POLICY`` is there so a partially-applied upgrade (table created,
    policy created, something later failed) still downgrades cleanly.
    """
    op.execute(f"DROP POLICY IF EXISTS tenant_isolation_policy ON public.{_TABLE}")
    op.drop_table(_TABLE)
