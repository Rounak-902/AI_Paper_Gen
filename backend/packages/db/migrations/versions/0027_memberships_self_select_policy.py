"""memberships self-select policy and safe-missing-context hardening (P1-T8)

Revision ID: 0027
Revises: 0026
Create Date: 2026-08-23

This revision exists so that ``GET /orgs`` — "list the organizations the calling
user belongs to" — is answerable **as ``app_user``**, which is
``NOBYPASSRLS`` (revision 0024).

The problem it solves
---------------------
Revision 0025 protects ``memberships`` with one policy:

    CREATE POLICY tenant_isolation_policy ON public.memberships
    FOR ALL TO app_user
    USING (org_id = current_setting('app.org_id')::uuid)
    WITH CHECK (org_id = current_setting('app.org_id')::uuid)

"Which orgs does this user belong to" is a question asked *before* any org
context exists — there is no ``app.org_id`` to set, because finding out which
org to act in is the whole point of the query. Under the policy above the
question is unanswerable, and ``app/modules/auth/service.py``'s module docstring
records that as the reason login returns no ``orgs`` array even though doc 08
line 913 shows one. This revision closes that gap.

Doc coverage, stated plainly
----------------------------
There is **none**. A grep across ``docs/`` finds no ``app.user_id``, no
``CREATE POLICY`` of any kind, and no statement about how a pre-org identity read
should be scoped. Doc 07 §7.4's flow ("1. Load user's memberships / 2. Verify
membership exists for activeOrgId") *requires* step 1 to be possible without an
org context, but says nothing about the mechanism. Both policies below were
authored for this task. Listed in the P1-T8 report as authored, not specified.

Why ``app.user_id`` is trustworthy on this branch
-------------------------------------------------
Worth stating explicitly, because the same GUC would have been worthless three
tasks ago. ``app.user_id`` is set from ``AuthenticatedUser.user_id``, which
``app/common/deps.py``'s ``get_current_user`` derives from the ``sub`` claim of an
access token that ``decode_access_token`` has verified with an HMAC signature
check against a single-algorithm allow-list (P1-T7). It is never read from a
path parameter, a query string, a request body, or a header. Before P1-T7 the
principal came from an ``X-Debug-User-Id`` header that any caller could set, and
a self-select policy keyed on it would have been a cross-tenant read primitive
with extra steps. It is a real identity assertion now.

Part 1 — the new policy
-----------------------
``memberships_self_select`` is ``FOR SELECT`` only. It grants no INSERT, UPDATE or
DELETE, so it cannot be used to write a membership for yourself into an org you
do not belong to — which is what a ``FOR ALL`` version of the same predicate
would allow. ``status = 'active'`` and ``deleted_at IS NULL`` narrow it further:
an invited-but-not-accepted or suspended membership is invisible through this
policy, so ``GET /orgs`` and ``GET /auth/me`` list only orgs the caller can
actually act in.

ACCEPTED WIDENING, reported rather than hidden. With **both** GUCs set — the
ordinary request case — the two policies are permissive and therefore OR'd, so a
caller can SELECT their own membership rows in *other* orgs, not just the active
one. Three reasons this is the right trade:

* The exposed rows are the caller's own memberships and nothing else. No other
  tenant's data becomes visible, which is the property RLS exists to protect.
* ``BaseRepository._base_query`` still applies ``WHERE org_id = :org_id``, so no
  application read actually returns them.
* The alternative — adding ``AND current_setting('app.org_id', true) = ''`` so the
  policy only applies in identity sessions — makes the policy's behaviour depend
  on invisible session state, which is materially harder to audit and to test.

Part 2 — hardening the existing policy
--------------------------------------
``tenant_isolation_policy`` on ``memberships`` is dropped and recreated with
``NULLIF(current_setting('app.org_id', true), '')::uuid`` in place of
``current_setting('app.org_id')::uuid``. This is **not cosmetic and not
optional**: without it ``GET /orgs`` fails outright.

The mechanism, precisely. ``current_setting('x')`` with no ``missing_ok``
argument raises ``42704 unrecognized configuration parameter`` when the GUC has
never been set in the backend. Worse, after any ``set_config('app.org_id', ...,
true)`` has run once on a pooled connection the parameter *stays defined* with an
empty-string reset value, so a later transaction that does not set it reads
``''`` and the cast raises ``22P02 invalid input syntax for type uuid: ""``.
Permissive policies are OR'd, but an **erroring** predicate aborts the whole
query — the other policy passing does not rescue it. So a ``SELECT`` on
``memberships`` in an identity-only session would raise rather than return the
caller's rows, no matter how correct ``memberships_self_select`` is.

``NULLIF(current_setting('app.org_id', true), '')`` yields SQL ``NULL`` for both
the never-set and the reset-to-empty cases, ``org_id = NULL`` evaluates to
``NULL``, and a ``NULL`` policy predicate is treated as false. The table
therefore **fails closed** — zero rows from this policy — instead of raising.
That is the behaviour proof 4's negative case checks: with ``app.user_id`` unset
as well, the query must return no rows rather than erroring *or* leaking.

Scope of the hardening
----------------------
Only ``memberships``. The other sixteen tables from revision 0025 keep the
unhardened spelling, deliberately:

* Nothing reads them without an org context. Every other table is reached through
  ``get_db``, which cannot run until ``get_org_context`` has produced an org.
* On those tables the raise is a *useful* failure. A query that reaches
  ``generated_papers`` with no tenant context is a guard-chain hole, and an error
  makes it a loud 500 (which is exactly how ``errors.py`` maps
  ``TenantContextMissing``); the hardened form would silently return an empty
  result set and look like "no data" instead of "no scoping".

Reported as a judgment call: the hardening is applied where a context-free read
is a legitimate operation, and withheld where it would convert a bug into
silence. A blanket sweep across all seventeen tables is a defensible alternative
and is recommended in the P1-T8 report as a follow-up **with** a companion check
that no code path reads a tenant table unscoped.

No percent signs — same asyncpg ``format`` paramstyle constraint as 0024-0026.
"""

from typing import Sequence, Union

from alembic import op

# revision identifiers, used by Alembic.
revision: str = "0027"
down_revision: Union[str, Sequence[str], None] = "0026"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None

_APP_ROLE = "app_user"
_TABLE = "memberships"

#: The hardened org predicate. Used for both USING and WITH CHECK.
_ORG_PREDICATE = "org_id = NULLIF(current_setting('app.org_id', true), '')::uuid"

#: The unhardened predicate revision 0025 shipped, restored by ``downgrade()``.
_ORG_PREDICATE_LEGACY = "org_id = current_setting('app.org_id')::uuid"


def upgrade() -> None:
    """Upgrade schema."""

    # -- Part 2 first ----------------------------------------------------------
    # Order matters for a reader, not for Postgres: harden the existing policy
    # before adding the one that depends on the hardening, so that no intermediate
    # state exists in which memberships_self_select is present but unusable.
    op.execute(f"DROP POLICY IF EXISTS tenant_isolation_policy ON public.{_TABLE}")
    op.execute(
        f"CREATE POLICY tenant_isolation_policy ON public.{_TABLE} "
        f"FOR ALL TO {_APP_ROLE} "
        f"USING ({_ORG_PREDICATE}) "
        f"WITH CHECK ({_ORG_PREDICATE})"
    )

    # -- Part 1 ----------------------------------------------------------------
    # FOR SELECT, and only FOR SELECT. The identity comes from app.user_id, which
    # is set from a verified JWT claim by app/db/session.py's
    # get_identity_session() and from nowhere else.
    op.execute(
        f"CREATE POLICY memberships_self_select ON public.{_TABLE} "
        f"FOR SELECT TO {_APP_ROLE} "
        f"USING ("
        f"user_id = NULLIF(current_setting('app.user_id', true), '')::uuid "
        f"AND status = 'active' "
        f"AND deleted_at IS NULL"
        f")"
    )

    # Supports the new policy's predicate and the ``GET /orgs`` query that relies
    # on it. Revision 0002 indexed (user_id, org_id) as a partial-unique; a plain
    # index on user_id alone is what a user-first scan wants, and the partial
    # unique cannot serve it when the leading column is filtered but org_id is not
    # constrained at all.
    op.execute(
        "CREATE INDEX IF NOT EXISTS ix_memberships_user_id_status "
        f"ON public.{_TABLE} (user_id, status) WHERE deleted_at IS NULL"
    )


def downgrade() -> None:
    """Downgrade schema — restore revision 0025's exact policy.

    The self-select policy is dropped and the org policy is put back in its
    unhardened form, so that ``0027 -> 0026`` leaves ``memberships`` byte-identical
    to what 0025 created. A downgrade that left the hardened predicate in place
    would be a downgrade that does not actually revert.
    """
    op.execute("DROP INDEX IF EXISTS ix_memberships_user_id_status")
    op.execute(f"DROP POLICY IF EXISTS memberships_self_select ON public.{_TABLE}")
    op.execute(f"DROP POLICY IF EXISTS tenant_isolation_policy ON public.{_TABLE}")
    op.execute(
        f"CREATE POLICY tenant_isolation_policy ON public.{_TABLE} "
        f"FOR ALL TO {_APP_ROLE} "
        f"USING ({_ORG_PREDICATE_LEGACY}) "
        f"WITH CHECK ({_ORG_PREDICATE_LEGACY})"
    )
