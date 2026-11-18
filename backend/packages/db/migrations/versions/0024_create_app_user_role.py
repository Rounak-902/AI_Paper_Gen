"""create the non-superuser app_user role that row-level security can bind to (P1-T5)

Revision ID: 0024
Revises: 0023
Create Date: 2026-08-20

Not a doc 03 §9 step — see revision 0020's docstring for why the chain stops mapping onto
§9 after step 19. This revision and 0025 together are P1-T5's database half.

Why a role migration exists at all, and why it is *minimal*
----------------------------------------------------------
Row-level security is unverifiable, and very nearly meaningless, against the connection the
migrations run on. PostgreSQL exempts two kinds of role from every policy:

* **superusers** — unconditionally, with no way to opt in; and
* **roles with ``BYPASSRLS``**.

and it exempts a third by default:

* **the table's owner**, unless the table is marked ``FORCE ROW LEVEL SECURITY``.

The local container and every existing test fixture connect as ``postgres``, which is all
three at once. So a suite that "proves RLS works" over that connection proves nothing: the
policies are never consulted. ``IMPLEMENTATION_PLAN.md`` says so in P1-T5's own line
(*"connect as a non-superuser ``app_user``"*) and again in the P1 exit criteria (*"the API
connects to Postgres as a **non-superuser** and RLS is enforced (a raw query without
``SET LOCAL app.org_id`` returns zero rows, not all rows)"*).

**Provisioning the application's database role in full is P1-T10's job** (``infra/``,
local-infra bring-up, PgBouncer, the cross-pool isolation test). This revision creates the
smallest version of that role which makes revision 0025's policies testable, and nothing
more. Specifically:

=====================================  ==========================================
Done here (P1-T5)                      Deliberately left to P1-T10
=====================================  ==========================================
The role exists, ``NOSUPERUSER``,      A password / any credential material. The
``NOBYPASSRLS``, ``NOCREATEDB``,       role is created with **no password**, so it
``NOCREATEROLE``, ``NOREPLICATION``,   cannot authenticate over TCP at all until
``LOGIN``                              somebody sets one.
``USAGE`` on schema ``public``         Where that credential comes from
                                       (env var, secret store, compose file).
``SELECT``/``INSERT``/``UPDATE`` on    ``PGBOUNCER`` auth_query / auth_file wiring
the application tables                 and the pool's own login.
``USAGE, SELECT`` on sequences         ``connection_limit``, ``statement_timeout``
                                       and other per-role ``ALTER ROLE ... SET``
                                       tuning.
Default privileges, so tables added    Whether staging/production use one role or
by later revisions inherit the grant   several (read-only replica role, migration
                                       role distinct from the runtime role).
=====================================  ==========================================

**This role is not a placeholder to be thrown away.** ``app_user`` is the same role name
the plan names and the same role P1-T10 should keep; what P1-T10 adds is credentials,
pooling and environment-specific tuning on top. It is safe for P1-T10 to ``ALTER`` this
role. It should not need to ``DROP`` and recreate it — if it does, the privilege grants
below have to be reissued, because ``DROP ROLE`` takes its ACL entries with it.

Why no password is set in a migration
-------------------------------------
A migration is a file in git. A password in it is a password in git, in every clone, and in
the ``alembic upgrade head --sql`` render that CI publishes as an artifact. Roles are also
*cluster*-scoped, not database-scoped, so a password written here would apply to a shared
object from a per-database script.

The consequence is deliberate and has to be handled by whoever provisions an environment:
after ``alembic upgrade head``, ``app_user`` can connect over the container's Unix socket
(the official ``postgres`` image writes ``local all all trust`` into ``pg_hba.conf``) but
**not** over TCP, where the same file requires ``scram-sha-256``. ``scripts/verify-p1-t5.ps1``
therefore issues its own ``ALTER ROLE app_user PASSWORD ...`` before the asyncpg tests run,
with a local-only fixture value. P1-T10 replaces that with real provisioning.

Which privileges, and the three deliberate holes in the blanket grant
--------------------------------------------------------------------
The grant is ``SELECT, INSERT, UPDATE`` — never ``DELETE``, and never any DDL.

1. **No ``DELETE`` on anything.** ADR-004 makes soft deletion the only deletion: *"Every
   read path filters ``deleted_at IS NULL``"*, and hard deletion exists only as an audited
   maintenance operation. The repository base class in ``app/db/repository.py`` enforces
   that in Python. Withholding the privilege enforces it in the one place a bug in the
   repository cannot reach. A maintenance hard-delete runs as the owner, which is exactly
   the audited, out-of-band path ADR-004 describes.
2. **No ``UPDATE`` on ``audit_logs`` or ``usage``.** ADR-004: they are *"append-only: no
   ``UPDATE``, no ``DELETE`` in normal operation (CN-22)"*. With ``DELETE`` already absent
   cluster-wide, revoking ``UPDATE`` on these two makes append-only a privilege fact rather
   than a convention. Note what this does *not* do: revision 0021's residue about
   ``UPDATE approvals`` stands, because ``approvals`` is only append-only "by construction"
   and doc 03 gives it no such guarantee to enforce.
3. **No ``INSERT``/``UPDATE`` on ``alembic_version``.** The runtime role must not be able to
   restamp the migration chain. ``SELECT`` is kept so a health check can report the applied
   revision.

``USAGE, SELECT`` on sequences is needed because ``usage.id`` and ``audit_logs.id`` are
``bigint GENERATED ... AS IDENTITY``, whose default is a ``nextval()`` over a sequence the
inserting role must be allowed to advance. Every other primary key is
``uuid DEFAULT gen_random_uuid()``, which needs no sequence at all — so this grant exists
for exactly two tables and is written as a blanket for future ones.

``CREATE`` on schema ``public`` is not granted. PostgreSQL 16 already revokes it from
``PUBLIC`` by default, so there is nothing to take away and no ``REVOKE`` is issued here —
issuing one would silently change cluster state that this revision does not own and cannot
correctly restore on downgrade. What *is* asserted, by ``scripts/verify-p1-t5.ps1``, is the
observable end state: ``app_user`` cannot create a table.

Why the DO blocks
-----------------
Three things here are not expressible in plain DDL:

* ``CREATE ROLE`` has no ``IF NOT EXISTS``, and the role is **cluster-scoped** while this
  migration is per-database. Applying the chain to ``papergen`` and then ``papergen_test``
  (which ``scripts/verify-p1-t4.ps1`` and its successor both do, on every run) would fail
  the second time on a bare ``CREATE ROLE``.
* ``ALTER DEFAULT PRIVILEGES FOR ROLE`` requires a literal role name; the role in question
  is whoever is running the migration, known only at apply time as ``current_user``.
* ``DROP ROLE`` on downgrade must not be unconditional, for the same cluster-scope reason —
  see :func:`downgrade`.

Because they are ``op.execute`` strings, all three render intact into
``alembic upgrade head --sql`` and resolve ``current_user`` when that script is applied,
which is the correct moment for it.
"""
from typing import Sequence, Union

from alembic import op

# revision identifiers, used by Alembic.
revision: str = "0024"
down_revision: Union[str, Sequence[str], None] = "0023"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None

#: The application's runtime database role. Named by ``IMPLEMENTATION_PLAN.md`` (P1-T5 and
#: the P1 exit criteria); doc 03 and doc 07 describe the mechanism but never name the role.
_APP_ROLE = "app_user"

#: Tables the application may append to but never rewrite (ADR-004, CN-22).
_APPEND_ONLY_TABLES: tuple[str, ...] = ("audit_logs", "usage")

#: Alembic's own bookkeeping table. Readable, never writable by the runtime role.
_MIGRATION_STATE_TABLE = "alembic_version"

#: Every attribute is spelled out, including the ``NO`` forms that are already the default.
#: A reader auditing this role should not have to know PostgreSQL's defaults to see that
#: ``BYPASSRLS`` is off — and ``NOBYPASSRLS`` is the single most important word in the file.
_CREATE_ROLE = f"""
DO $do$
BEGIN
    IF NOT EXISTS (SELECT 1 FROM pg_catalog.pg_roles WHERE rolname = '{_APP_ROLE}') THEN
        CREATE ROLE {_APP_ROLE}
            LOGIN
            NOSUPERUSER
            NOBYPASSRLS
            NOCREATEDB
            NOCREATEROLE
            NOREPLICATION
            NOINHERIT;
        RAISE NOTICE 'created role {_APP_ROLE} (no password set - see revision 0024)';
    ELSE
        -- The role is cluster-scoped, so a second database in the same cluster finds it
        -- already there. Re-assert the security-relevant attributes rather than trusting
        -- whatever created it: this is the line that would catch a hand-made app_user
        -- that somebody had granted SUPERUSER or BYPASSRLS to.
        ALTER ROLE {_APP_ROLE}
            LOGIN
            NOSUPERUSER
            NOBYPASSRLS
            NOCREATEDB
            NOCREATEROLE
            NOREPLICATION
            NOINHERIT;
        RAISE NOTICE 'role {_APP_ROLE} already existed; attributes re-asserted';
    END IF;
END
$do$
"""

_GRANT_SCHEMA = f"GRANT USAGE ON SCHEMA public TO {_APP_ROLE}"

_GRANT_TABLES = f"GRANT SELECT, INSERT, UPDATE ON ALL TABLES IN SCHEMA public TO {_APP_ROLE}"

_GRANT_SEQUENCES = f"GRANT USAGE, SELECT ON ALL SEQUENCES IN SCHEMA public TO {_APP_ROLE}"

_REVOKE_APPEND_ONLY = tuple(
    f"REVOKE UPDATE ON public.{table} FROM {_APP_ROLE}" for table in _APPEND_ONLY_TABLES
)

_REVOKE_MIGRATION_STATE = (
    f"REVOKE INSERT, UPDATE ON public.{_MIGRATION_STATE_TABLE} FROM {_APP_ROLE}"
)

#: ``quote_ident`` makes this injection-safe even though the owner's name is interpolated at
#: apply time. ``current_user`` is the migration runner, which is the role that will own any
#: table a later revision creates.
#:
#: **Deliberately not ``format('... %I ...')``**, which is the idiomatic way to write this.
#: Alembic turns an ``op.execute`` string into a SQLAlchemy ``text()`` construct, and for a
#: driver whose paramstyle is ``format`` — which is what the asyncpg dialect uses — the
#: compiler doubles literal percent signs. A ``%I`` in a migration is therefore a
#: mode-dependent hazard: it can render differently online, offline (``--sql``) and under a
#: different driver. Plain ``||`` concatenation has no such ambiguity. **No statement in this
#: revision or 0025 contains a percent sign, on purpose.**
_DEFAULT_PRIVILEGES = f"""
DO $do$
BEGIN
    EXECUTE 'ALTER DEFAULT PRIVILEGES FOR ROLE ' || quote_ident(current_user)
         || ' IN SCHEMA public GRANT SELECT, INSERT, UPDATE ON TABLES TO {_APP_ROLE}';
    EXECUTE 'ALTER DEFAULT PRIVILEGES FOR ROLE ' || quote_ident(current_user)
         || ' IN SCHEMA public GRANT USAGE, SELECT ON SEQUENCES TO {_APP_ROLE}';
END
$do$
"""

_REVOKE_DEFAULT_PRIVILEGES = f"""
DO $do$
BEGIN
    EXECUTE 'ALTER DEFAULT PRIVILEGES FOR ROLE ' || quote_ident(current_user)
         || ' IN SCHEMA public REVOKE SELECT, INSERT, UPDATE ON TABLES FROM {_APP_ROLE}';
    EXECUTE 'ALTER DEFAULT PRIVILEGES FOR ROLE ' || quote_ident(current_user)
         || ' IN SCHEMA public REVOKE USAGE, SELECT ON SEQUENCES FROM {_APP_ROLE}';
END
$do$
"""

#: ``DROP OWNED BY`` revokes every privilege the role holds *in the current database* and
#: drops objects it owns there. It is the documented prerequisite for ``DROP ROLE`` and the
#: only way to undo ``GRANT ... ON ALL TABLES`` without enumerating tables.
_DROP_OWNED = f"DROP OWNED BY {_APP_ROLE}"

#: A role is cluster-scoped, so after the grants are gone the role itself may still be
#: referenced from another database in the same cluster — ``papergen`` and ``papergen_test``
#: are exactly that pair. An unconditional ``DROP ROLE`` would then abort the downgrade and
#: strand the chain mid-cycle, which is worse than leaving a privilege-less role behind.
_DROP_ROLE = f"""
DO $do$
BEGIN
    IF EXISTS (SELECT 1 FROM pg_catalog.pg_roles WHERE rolname = '{_APP_ROLE}') THEN
        BEGIN
            DROP ROLE {_APP_ROLE};
            RAISE NOTICE 'dropped role {_APP_ROLE}';
        EXCEPTION
            WHEN dependent_objects_still_exist OR insufficient_privilege THEN
                -- ``RAISE NOTICE USING MESSAGE =`` rather than a format-placeholder RAISE,
                -- for the percent-sign reason documented on _DEFAULT_PRIVILEGES.
                RAISE NOTICE USING MESSAGE =
                    'role {_APP_ROLE} kept: ' || SQLERRM || '. It is cluster-scoped and may '
                    || 'still be referenced from another database in this cluster (or this '
                    || 'role may not drop it). Its privileges in THIS database have been '
                    || 'revoked, which is what this downgrade owns.';
        END;
    END IF;
END
$do$
"""


def upgrade() -> None:
    """Upgrade schema.

    Order matters twice. The role must exist before anything is granted to it, and the
    blanket ``GRANT ... ON ALL TABLES`` must come before the two ``REVOKE`` statements that
    carve holes in it — ``ALL TABLES`` would otherwise re-grant what was just revoked.

    ``ON ALL TABLES IN SCHEMA public`` is evaluated now, against the tables that exist now.
    This revision sits after 0023, so "now" is all 26 application tables plus
    ``alembic_version``. Tables created by later revisions are covered by
    :data:`_DEFAULT_PRIVILEGES` instead, provided they are created by the same role that
    runs this one. A revision that creates a table under a *different* owner must grant
    explicitly, and ``scripts/verify-p1-t5.ps1`` asserts the privilege set per table so that
    omission shows up as a failure rather than a 403 in production.
    """

    op.execute(_CREATE_ROLE)
    op.execute(_GRANT_SCHEMA)
    op.execute(_GRANT_TABLES)
    op.execute(_GRANT_SEQUENCES)

    for statement in _REVOKE_APPEND_ONLY:
        op.execute(statement)

    op.execute(_REVOKE_MIGRATION_STATE)
    op.execute(_DEFAULT_PRIVILEGES)


def downgrade() -> None:
    """Downgrade schema.

    Mirror image: default privileges first (``DROP OWNED BY`` does not remove ACL defaults
    that grant *to* a role, only those granted *by* it), then every privilege in this
    database, then the role — conditionally, and only if nothing else in the cluster still
    needs it.

    This asymmetry is intentional and is the reason a ``downgrade`` then ``upgrade`` cycle
    works even when the role survives: :data:`_CREATE_ROLE` takes the ``ELSE`` branch and
    re-asserts the attributes, and every grant is reissued from scratch.
    """

    op.execute(_REVOKE_DEFAULT_PRIVILEGES)
    op.execute(_DROP_OWNED)
    op.execute(_DROP_ROLE)
