"""authorize verified super-admin global question writes (P2-T4 follow-up)

Revision ID: 0031
Revises: 0030
Create Date: 2026-09-01

Why a separate policy exists
----------------------------
Revision 0025 intentionally lets every tenant *read* shared ``org_id IS NULL`` rows
but lets ``app_user`` write only ``org_id = app.org_id`` rows.  Adding NULL to that
tenant ``WITH CHECK`` would let every teacher and coaching administrator publish
platform-global content, so that policy remains unchanged.

Docs 03 and 07 prescribe transaction-local request context and RLS as a second line
of defence, but they do not specify a privileged-write mechanism.  The smallest
extension consistent with those rules is:

* the API sets the authenticated user's UUID as transaction-local ``app.user_id``
  alongside ``app.org_id``;
* a SECURITY DEFINER predicate re-derives authority from live database state -- an
  active ``super_admin`` membership for that exact user and organization, with both
  the user and organization still active; and
* separate permissive policies admit only a structurally global question when that
  predicate is true.

The predicate does not trust a JWT role string or an ``app.is_super_admin`` flag.
The application request path supplies the actor identity established by the
verified-auth dependency chain; PostgreSQL looks up the actual role itself.  This is
the same shared-``app_user`` trust boundary revision 0027 uses for identity reads (and
revision 0025 uses for ``app.org_id``): it protects against application authorization
mistakes, while compromise of the database credential remains outside RLS's threat
model because that credential can set custom GUCs directly.

Why INSERT and UPDATE
---------------------
First-version creation is two statements: INSERT to obtain the server-generated UUID,
then UPDATE to set ``root_question_id = id``.  INSERT alone would still fail the real
API during that self-rooting flush.  The question-creation handler is the only route
that opts in to actor-aware database context in this task; existing lifecycle routes
remain tenant-only and therefore cannot mutate global rows.  No DELETE policy or
DELETE grant is added.

SECURITY DEFINER hardening
--------------------------
The function owns the minimum privileged lookup needed to evaluate the policy.  It
has a fixed search_path, schema-qualifies every relation, receives no caller-supplied
arguments, returns false for missing/empty/non-matching GUC text, and is executable
only by ``app_user`` (PUBLIC is revoked).  It does not perform the write itself.
"""

from typing import Sequence, Union

from alembic import op

# revision identifiers, used by Alembic.
revision: str = "0031"
down_revision: Union[str, Sequence[str], None] = "0030"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None

_APP_ROLE = "app_user"
_FUNCTION = "app_is_active_super_admin"
_INSERT_POLICY = "global_question_super_admin_insert"
_UPDATE_POLICY = "global_question_super_admin_update"


def upgrade() -> None:
    """Add a database-reverified global question write path."""

    op.execute(
        f"""
        CREATE FUNCTION public.{_FUNCTION}()
        RETURNS boolean
        LANGUAGE sql
        STABLE
        SECURITY DEFINER
        SET search_path = pg_catalog, public
        AS $function$
            SELECT EXISTS (
                SELECT 1
                FROM public.memberships AS membership
                JOIN public.users AS actor
                  ON actor.id = membership.user_id
                JOIN public.organizations AS organization
                  ON organization.id = membership.org_id
                WHERE membership.user_id::text = NULLIF(
                          pg_catalog.current_setting('app.user_id', true), ''
                      )
                  AND membership.org_id::text = NULLIF(
                          pg_catalog.current_setting('app.org_id', true), ''
                      )
                  AND membership.role = 'super_admin'
                  AND membership.status = 'active'
                  AND membership.deleted_at IS NULL
                  AND actor.status = 'active'
                  AND actor.deleted_at IS NULL
                  AND organization.status = 'active'
                  AND organization.deleted_at IS NULL
            )
        $function$
        """
    )
    op.execute(f"REVOKE ALL ON FUNCTION public.{_FUNCTION}() FROM PUBLIC")
    op.execute(f"GRANT EXECUTE ON FUNCTION public.{_FUNCTION}() TO {_APP_ROLE}")

    # Permissive policies OR with 0025's policy.  The original tenant predicate is
    # intentionally not replaced or altered.
    op.execute(
        f"""
        CREATE POLICY {_INSERT_POLICY}
        ON public.question_bank
        AS PERMISSIVE
        FOR INSERT
        TO {_APP_ROLE}
        WITH CHECK (
            scope = 'global'
            AND org_id IS NULL
            AND public.{_FUNCTION}()
        )
        """
    )
    op.execute(
        f"""
        CREATE POLICY {_UPDATE_POLICY}
        ON public.question_bank
        AS PERMISSIVE
        FOR UPDATE
        TO {_APP_ROLE}
        USING (
            scope = 'global'
            AND org_id IS NULL
            AND public.{_FUNCTION}()
        )
        WITH CHECK (
            scope = 'global'
            AND org_id IS NULL
            AND public.{_FUNCTION}()
        )
        """
    )

    op.execute(
        f"""
        COMMENT ON FUNCTION public.{_FUNCTION}() IS
        'True only when transaction-local app.user_id/app.org_id resolve to an active super_admin membership in an active user and organization.'
        """
    )
    op.execute(
        f"""
        COMMENT ON POLICY {_INSERT_POLICY} ON public.question_bank IS
        'Dedicated global INSERT path; tenant_isolation_policy remains unchanged.'
        """
    )
    op.execute(
        f"""
        COMMENT ON POLICY {_UPDATE_POLICY} ON public.question_bank IS
        'Dedicated global UPDATE path required for first-version self-rooting inside the opted-in creation transaction.'
        """
    )


def downgrade() -> None:
    """Remove only the privileged path and restore the 0025-only posture."""

    op.execute(
        f"DROP POLICY IF EXISTS {_UPDATE_POLICY} ON public.question_bank"
    )
    op.execute(
        f"DROP POLICY IF EXISTS {_INSERT_POLICY} ON public.question_bank"
    )
    op.execute(f"REVOKE ALL ON FUNCTION public.{_FUNCTION}() FROM {_APP_ROLE}")
    op.execute(f"DROP FUNCTION IF EXISTS public.{_FUNCTION}()")
