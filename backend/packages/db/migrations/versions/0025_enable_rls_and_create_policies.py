"""enable row-level security and create tenant isolation policies (P1-T5)

Revision ID: 0025
Revises: 0024
Create Date: 2026-08-20

Not a doc 03 §9 step — this is P1-T5's second migration (after 0024, which created
``app_user``). Together they make RLS real.

doc 03 §9 step 18 names "RLS policies" in its title ("Add indexes, constraints, RLS
policies, partitions, and seed data") and revision 0018 handled the indexes and
constraints but explicitly deferred RLS to this task — no ``CREATE POLICY`` statement
exists anywhere in doc 03 or doc 07, so every policy predicate in this file was
authored here, not copied from a spec. Doc 07 §2.9 establishes the mechanism:

    "Every authenticated request resolves an organization context from the JWT.
     Every database query carries ``org_id``. PostgreSQL RLS provides a second
     line of defense. A missed application-level check cannot leak cross-tenant
     data."

And doc 03 §8 establishes the session variable:

    "Every session runs inside a request-scoped transaction that issues
     ``SET LOCAL app.org_id = :org_id`` **before any query**"

What this revision does
-----------------------
For each of the 17 tenant-scoped tables (those that carry an ``org_id`` column or
reach one through a foreign key):

1. ``ALTER TABLE ... ENABLE ROW LEVEL SECURITY``
2. ``ALTER TABLE ... FORCE ROW LEVEL SECURITY``
3. ``CREATE POLICY tenant_isolation_policy ON ... TO app_user USING (...) WITH CHECK (...)``

FORCE ROW LEVEL SECURITY
-------------------------
Applied on every RLS-enabled table. ``app_user`` does not own these tables, so
``FORCE`` is not strictly load-bearing today. But it is defense-in-depth:

* If a future migration accidentally creates a table under ``app_user``, policies
  would still bind.
* If someone manually ``ALTER OWNER``s a table to ``app_user``, the same.
* The cost is zero — no performance or operational downside.

Doc 03 and doc 07 do not mention ``FORCE``. This is a judgment call, documented here
and in the P1-T5 report.

Policy design (judgment calls — docs are silent)
-------------------------------------------------
* **Naming:** ``tenant_isolation_policy`` per table — one combined policy rather than
  separate read/write policies, because the predicate is identical for both directions.
  Simpler to audit than two policies that must stay in sync.

* **USING clause** (governs which existing rows SELECT/UPDATE/DELETE can see):
  ``organization_id = current_setting('app.org_id')::uuid`` for tables where
  ``org_id`` is NOT NULL. For tables where ``org_id`` is nullable (``question_bank``,
  ``textbooks``, ``templates``, ``audit_logs``), the clause adds
  ``OR org_id IS NULL`` so that global/system rows remain visible.

* **WITH CHECK clause** (governs which new/modified rows INSERT/UPDATE can produce):
  Always ``org_id = current_setting('app.org_id')::uuid`` — never ``OR org_id IS NULL``.
  Global content is created by the owner/superuser, not through ``app_user``.

* **Indirect policies** for child tables that reach ``org_id`` through a parent FK
  (``paper_versions``, ``approvals``, ``textbook_assets``, ``program_subjects``,
  ``program_teachers``): subquery-based ``EXISTS (SELECT 1 FROM parent WHERE ...)``.

Tables excluded from RLS (9 tables, all genuinely global)
---------------------------------------------------------
* ``organizations`` — the tenant root; RLS would prevent org listing during org-switch.
* ``users`` — global identity; one user may belong to multiple orgs via memberships.
* ``boards``, ``classes``, ``subjects``, ``chapters``, ``learning_outcomes`` —
  global curriculum reference data. Doc 03 §5 explicitly marks ``boards`` as
  "global board reference data"; the rest are its children with no ``org_id``.
* ``curriculum_versions`` — global curriculum versioning, FK to boards, no ``org_id``.
* ``prompt_versions`` — platform-level AI governance, no ``org_id``.

Why no percent signs
--------------------
Same reason as revision 0024: Alembic turns ``op.execute`` strings into SQLAlchemy
``text()`` constructs, and the asyncpg dialect's ``format`` paramstyle doubles
literal percent signs. No statement in this revision contains one.
"""
from typing import Sequence, Union

from alembic import op

# revision identifiers, used by Alembic.
revision: str = "0025"
down_revision: Union[str, Sequence[str], None] = "0024"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None

_APP_ROLE = "app_user"

# ---------------------------------------------------------------------------
# Tables with a DIRECT org_id column (NOT NULL)
# ---------------------------------------------------------------------------
_DIRECT_ORG_ID_NOT_NULL: tuple[str, ...] = (
    "branches",
    "memberships",
    "programs",
    "generated_papers",
    "subscriptions",
    "usage",
    "analytics_daily",
    "notifications",
)

# ---------------------------------------------------------------------------
# Tables with a DIRECT org_id column that is NULLABLE
# (global/shared content has org_id IS NULL)
# ---------------------------------------------------------------------------
_DIRECT_ORG_ID_NULLABLE: tuple[str, ...] = (
    "question_bank",
    "textbooks",
    "templates",
    "audit_logs",
)

# ---------------------------------------------------------------------------
# Tables with INDIRECT org relationship through a parent FK
# ---------------------------------------------------------------------------
# Each entry: (child_table, fk_column, parent_table, parent_org_nullable)
_INDIRECT_ORG_ID: tuple[tuple[str, str, str, bool], ...] = (
    ("paper_versions", "paper_id", "generated_papers", False),
    ("approvals", "paper_id", "generated_papers", False),
    ("textbook_assets", "textbook_id", "textbooks", True),
    ("program_subjects", "program_id", "programs", False),
    ("program_teachers", "program_id", "programs", False),
)

# All tables that get RLS, in the order policies are created.
_ALL_RLS_TABLES: tuple[str, ...] = (
    _DIRECT_ORG_ID_NOT_NULL
    + _DIRECT_ORG_ID_NULLABLE
    + tuple(t[0] for t in _INDIRECT_ORG_ID)
)


def _enable_rls(table: str) -> str:
    return f"ALTER TABLE public.{table} ENABLE ROW LEVEL SECURITY"


def _force_rls(table: str) -> str:
    return f"ALTER TABLE public.{table} FORCE ROW LEVEL SECURITY"


def _disable_rls(table: str) -> str:
    return f"ALTER TABLE public.{table} DISABLE ROW LEVEL SECURITY"


def _no_force_rls(table: str) -> str:
    return f"ALTER TABLE public.{table} NO FORCE ROW LEVEL SECURITY"


def _drop_policy(table: str) -> str:
    return f"DROP POLICY IF EXISTS tenant_isolation_policy ON public.{table}"


def _policy_direct_not_null(table: str) -> str:
    """Policy for tables with a non-nullable org_id column."""
    return (
        f"CREATE POLICY tenant_isolation_policy ON public.{table} "
        f"FOR ALL TO {_APP_ROLE} "
        f"USING (org_id = current_setting('app.org_id')::uuid) "
        f"WITH CHECK (org_id = current_setting('app.org_id')::uuid)"
    )


def _policy_direct_nullable(table: str) -> str:
    """Policy for tables with a nullable org_id column.

    USING allows reads of both tenant-owned rows AND global rows (org_id IS NULL).
    WITH CHECK restricts writes to the current tenant only — global content is
    created by the owner/superuser, not through app_user.
    """
    return (
        f"CREATE POLICY tenant_isolation_policy ON public.{table} "
        f"FOR ALL TO {_APP_ROLE} "
        f"USING (org_id = current_setting('app.org_id')::uuid OR org_id IS NULL) "
        f"WITH CHECK (org_id = current_setting('app.org_id')::uuid)"
    )


def _policy_indirect(
    child_table: str,
    fk_column: str,
    parent_table: str,
    parent_org_nullable: bool,
) -> str:
    """Policy for child tables that reach org_id through a parent FK.

    Uses an EXISTS subquery rather than IN for better plan stability with the
    optimizer — EXISTS short-circuits and the planner can push the outer row's
    FK value into the subquery as a simple equality.
    """
    if parent_org_nullable:
        parent_predicate = (
            "org_id = current_setting('app.org_id')::uuid OR org_id IS NULL"
        )
    else:
        parent_predicate = "org_id = current_setting('app.org_id')::uuid"

    return (
        f"CREATE POLICY tenant_isolation_policy ON public.{child_table} "
        f"FOR ALL TO {_APP_ROLE} "
        f"USING (EXISTS ("
        f"SELECT 1 FROM public.{parent_table} "
        f"WHERE {parent_table}.id = {child_table}.{fk_column} "
        f"AND ({parent_predicate})"
        f")) "
        f"WITH CHECK (EXISTS ("
        f"SELECT 1 FROM public.{parent_table} "
        f"WHERE {parent_table}.id = {child_table}.{fk_column} "
        f"AND ({parent_predicate})"
        f"))"
    )


def upgrade() -> None:
    """Upgrade schema.

    Order: ENABLE RLS → FORCE RLS → CREATE POLICY, per table. The ENABLE must
    come before CREATE POLICY (PostgreSQL rejects a policy on a table that does
    not have RLS enabled). FORCE comes between because it is an attribute of the
    table, not the policy.
    """

    # -- Direct org_id, NOT NULL -----------------------------------------------
    for table in _DIRECT_ORG_ID_NOT_NULL:
        op.execute(_enable_rls(table))
        op.execute(_force_rls(table))
        op.execute(_policy_direct_not_null(table))

    # -- Direct org_id, NULLABLE -----------------------------------------------
    for table in _DIRECT_ORG_ID_NULLABLE:
        op.execute(_enable_rls(table))
        op.execute(_force_rls(table))
        op.execute(_policy_direct_nullable(table))

    # -- Indirect org_id (child tables) ----------------------------------------
    for child_table, fk_column, parent_table, parent_org_nullable in _INDIRECT_ORG_ID:
        op.execute(_enable_rls(child_table))
        op.execute(_force_rls(child_table))
        op.execute(
            _policy_indirect(child_table, fk_column, parent_table, parent_org_nullable)
        )


def downgrade() -> None:
    """Downgrade schema.

    Reverse order: DROP POLICY → NO FORCE → DISABLE, per table.
    DROP POLICY IF EXISTS is idempotent, which is important because a partial
    upgrade that created some policies but not all should still downgrade cleanly.
    """

    for table in reversed(_ALL_RLS_TABLES):
        op.execute(_drop_policy(table))
        op.execute(_no_force_rls(table))
        op.execute(_disable_rls(table))
