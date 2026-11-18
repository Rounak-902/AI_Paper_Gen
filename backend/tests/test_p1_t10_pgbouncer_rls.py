"""P1-T10 live PgBouncer and dynamic RLS isolation proofs.

These tests are intentionally not a simulation of pooling. Every application-role
connection uses ``P1T10_PGBOUNCER_DATABASE_URL`` and ``NullPool`` so that closing a
logical client returns its transaction to PgBouncer. The verifier uses an isolated
database/user pool with one lazily created backend and sequential clients. Explicit
PID equality proves reuse even when the pool's normal capacity is twenty.
"""

from __future__ import annotations

import os
import re
import uuid
from dataclasses import dataclass

import pytest
from sqlalchemy import text
from sqlalchemy.engine import make_url
from sqlalchemy.ext.asyncio import AsyncEngine, AsyncSession, async_sessionmaker
from sqlalchemy.ext.asyncio import create_async_engine
from sqlalchemy.pool import NullPool

from app.db.session import get_tenant_session

pytestmark = pytest.mark.db


@dataclass(frozen=True)
class SeededTenants:
    org_a: uuid.UUID
    org_b: uuid.UUID
    branch_a: uuid.UUID
    branch_b: uuid.UUID
    invite_a: uuid.UUID
    invite_b: uuid.UUID


def _required_pooler_url() -> str:
    raw = os.environ.get("P1T10_PGBOUNCER_DATABASE_URL", "")
    if not raw:
        pytest.fail(
            "P1T10_PGBOUNCER_DATABASE_URL is required; this test must use the "
            "real PgBouncer service, not a direct PostgreSQL connection.",
            pytrace=False,
        )

    url = make_url(raw)
    if url.drivername != "postgresql+asyncpg":
        pytest.fail("the P1-T10 pooler URL must use postgresql+asyncpg", pytrace=False)
    if url.username != "app_user":
        pytest.fail("the P1-T10 pooler URL must authenticate as app_user", pytrace=False)
    if not (url.database or "").endswith("_test"):
        pytest.fail("the P1-T10 pooler URL must target a *_test database", pytrace=False)
    return raw


@pytest.fixture
async def pgbouncer_engine() -> AsyncEngine:
    """A new TCP client per checkout, with prepared statements pool-safe."""

    engine = create_async_engine(
        _required_pooler_url(),
        poolclass=NullPool,
        connect_args={
            "prepared_statement_cache_size": 0,
            "prepared_statement_name_func": lambda: f"__p1t10_{uuid.uuid4()}__",
        },
    )
    try:
        yield engine
    finally:
        await engine.dispose()


@pytest.fixture
def pgbouncer_factory(
    pgbouncer_engine: AsyncEngine,
) -> async_sessionmaker[AsyncSession]:
    return async_sessionmaker(pgbouncer_engine, expire_on_commit=False)


@pytest.fixture
async def seeded_tenants(db_engine: AsyncEngine):
    """Commit two tenants through the direct owner connection, then clean up."""

    ids = SeededTenants(
        org_a=uuid.uuid4(),
        org_b=uuid.uuid4(),
        branch_a=uuid.uuid4(),
        branch_b=uuid.uuid4(),
        invite_a=uuid.uuid4(),
        invite_b=uuid.uuid4(),
    )
    run_id = uuid.uuid4().hex

    async with db_engine.begin() as conn:
        for org_id, label in ((ids.org_a, "A"), (ids.org_b, "B")):
            await conn.execute(
                text(
                    "INSERT INTO organizations (id, name, org_type, status) "
                    "VALUES (:id, :name, 'school', 'active')"
                ),
                {"id": org_id, "name": f"p1t10-org-{run_id}-{label}"},
            )
        for branch_id, org_id, label in (
            (ids.branch_a, ids.org_a, "A"),
            (ids.branch_b, ids.org_b, "B"),
        ):
            await conn.execute(
                text(
                    "INSERT INTO branches (id, org_id, name, status) "
                    "VALUES (:id, :org_id, :name, 'active')"
                ),
                {
                    "id": branch_id,
                    "org_id": org_id,
                    "name": f"p1t10-branch-{run_id}-{label}",
                },
            )
        for invite_id, org_id, label in (
            (ids.invite_a, ids.org_a, "A"),
            (ids.invite_b, ids.org_b, "B"),
        ):
            await conn.execute(
                text(
                    "INSERT INTO invitations "
                    "(id, org_id, email, role, token_hash, expires_at, status) "
                    "VALUES (:id, :org_id, :email, 'teacher', :token_hash, "
                    "now() + interval '1 day', 'pending')"
                ),
                {
                    "id": invite_id,
                    "org_id": org_id,
                    "email": f"p1t10-{run_id}-{label}@example.test",
                    "token_hash": f"p1t10-{run_id}-{label}",
                },
            )

    try:
        yield ids
    finally:
        async with db_engine.begin() as conn:
            await conn.execute(
                text("DELETE FROM organizations WHERE id IN (:org_a, :org_b)"),
                {"org_a": ids.org_a, "org_b": ids.org_b},
            )


async def _scalar(session: AsyncSession, sql: str, **params):
    result = await session.execute(text(sql), params or None)
    return result.scalar_one()


async def _assert_clean_checkout(
    session: AsyncSession, ids: SeededTenants
) -> tuple[int, str | None]:
    """The exact assertion used by both the negative control and real proof."""

    pid = await _scalar(session, "SELECT pg_backend_pid()")
    setting = await _scalar(
        session, "SELECT current_setting('app.org_id', true)"
    )
    assert setting in (None, ""), f"pooled checkout inherited app.org_id={setting!r}"
    visible = await _scalar(
        session,
        "SELECT count(*) FROM invitations WHERE id IN (:invite_a, :invite_b)",
        invite_a=ids.invite_a,
        invite_b=ids.invite_b,
    )
    assert visible == 0, f"unscoped pooled checkout saw {visible} tenant rows"
    return pid, setting


@pytest.mark.asyncio
async def test_real_pgbouncer_checkout_preserves_tenant_isolation(
    pgbouncer_factory: async_sessionmaker[AsyncSession],
    seeded_tenants: SeededTenants,
) -> None:
    """Prove the control leaks on one backend, while SET LOCAL does not."""

    ids = seeded_tenants

    # Negative control: a deliberately session-scoped value must survive the handoff.
    async with pgbouncer_factory() as first_client:
        async with first_client.begin():
            control_pid = await _scalar(first_client, "SELECT pg_backend_pid()")
            await first_client.execute(
                text("SELECT set_config('app.org_id', :org_id, false)"),
                {"org_id": str(ids.org_a)},
            )

    async with pgbouncer_factory() as second_client:
        async with second_client.begin():
            reused_pid = await _scalar(second_client, "SELECT pg_backend_pid()")
            leaked_setting = await _scalar(
                second_client, "SELECT current_setting('app.org_id', true)"
            )
            leaked_rows = await _scalar(
                second_client,
                "SELECT count(*) FROM invitations WHERE id = :invite_a",
                invite_a=ids.invite_a,
            )
            assert reused_pid == control_pid
            assert leaked_setting == str(ids.org_a)
            assert leaked_rows == 1
            with pytest.raises(AssertionError, match="inherited app.org_id"):
                await _assert_clean_checkout(second_client, ids)
            # Clear the negative control without relying on a PgBouncer reset.
            await second_client.execute(
                text("SELECT set_config('app.org_id', '', false)")
            )

    print(f"P1T10_CONTROL_PID={control_pid}")
    print(f"P1T10_CONTROL_REUSED_PID={reused_pid}")
    print(f"P1T10_CONTROL_LEAK_VISIBLE_ROWS={leaked_rows}")

    # Real code path for org A. The helper under test owns the transaction and GUC.
    async with get_tenant_session(
        ids.org_a, session_factory=pgbouncer_factory
    ) as org_a_session:
        org_a_pid = await _scalar(org_a_session, "SELECT pg_backend_pid()")
        assert await _scalar(
            org_a_session, "SELECT current_setting('app.org_id', true)"
        ) == str(ids.org_a)
        assert await _scalar(
            org_a_session,
            "SELECT count(*) FROM branches WHERE id = :branch_a",
            branch_a=ids.branch_a,
        ) == 1
        assert await _scalar(
            org_a_session,
            "SELECT count(*) FROM branches WHERE id = :branch_b",
            branch_b=ids.branch_b,
        ) == 0

    # A separate client that sets no context must inherit neither A nor its rows.
    async with pgbouncer_factory() as clean_client:
        async with clean_client.begin():
            clean_pid, clean_setting = await _assert_clean_checkout(clean_client, ids)

    # A third logical client applies B and must see only B.
    async with get_tenant_session(
        ids.org_b, session_factory=pgbouncer_factory
    ) as org_b_session:
        org_b_pid = await _scalar(org_b_session, "SELECT pg_backend_pid()")
        assert await _scalar(
            org_b_session, "SELECT current_setting('app.org_id', true)"
        ) == str(ids.org_b)
        assert await _scalar(
            org_b_session,
            "SELECT count(*) FROM branches WHERE id = :branch_b",
            branch_b=ids.branch_b,
        ) == 1
        assert await _scalar(
            org_b_session,
            "SELECT count(*) FROM branches WHERE id = :branch_a",
            branch_a=ids.branch_a,
        ) == 0

    assert control_pid == org_a_pid == clean_pid == org_b_pid, (
        "sequential logical clients did not reuse one PostgreSQL backend: "
        f"control={control_pid}, A={org_a_pid}, clean={clean_pid}, B={org_b_pid}"
    )
    print(f"P1T10_ACTUAL_A_PID={org_a_pid}")
    print(f"P1T10_CLEAN_PID={clean_pid}")
    print(f"P1T10_CLEAN_ORG_SETTING={clean_setting!r}")
    print(f"P1T10_ACTUAL_B_PID={org_b_pid}")
    print("P1T10_TENANT_ISOLATION_SURVIVES_PROVEN_REUSE=YES")


@pytest.mark.asyncio
async def test_dynamic_rls_policy_and_privilege_sweep(
    pgbouncer_factory: async_sessionmaker[AsyncSession],
) -> None:
    """Discover every RLS relation from PostgreSQL and audit it as app_user."""

    async with get_tenant_session(
        uuid.uuid4(), session_factory=pgbouncer_factory
    ) as session:
        identity = (
            await session.execute(
                text("SELECT current_user, session_user, pg_backend_pid()")
            )
        ).one()
        assert identity.current_user == "app_user"
        assert identity.session_user == "app_user"

        relations = (
            await session.execute(
                text(
                    "SELECT n.nspname AS schema_name, c.relname AS table_name, "
                    "c.relrowsecurity AS rls_enabled, "
                    "c.relforcerowsecurity AS rls_forced, "
                    "row_security_active(c.oid) AS rls_active, "
                    "has_table_privilege(current_user, c.oid, 'SELECT') "
                    "AS can_select, "
                    "has_table_privilege(current_user, c.oid, 'DELETE') "
                    "AS can_delete "
                    "FROM pg_class AS c "
                    "JOIN pg_namespace AS n ON n.oid = c.relnamespace "
                    "WHERE n.nspname = 'public' "
                    "AND c.relkind IN ('r', 'p') AND c.relrowsecurity "
                    "ORDER BY n.nspname, c.relname"
                )
            )
        ).mappings().all()
        assert relations, "no public RLS tables were discovered"

        policies = (
            await session.execute(
                text(
                    "SELECT n.nspname AS schema_name, c.relname AS table_name, "
                    "p.polname AS policy_name, p.polcmd::text AS command, "
                    "pg_get_expr(p.polqual, p.polrelid) AS using_expr, "
                    "pg_get_expr(p.polwithcheck, p.polrelid) AS check_expr, "
                    "0::oid = ANY(p.polroles) AS applies_to_public, "
                    "EXISTS (SELECT 1 FROM pg_roles AS ar "
                    "WHERE ar.rolname = 'app_user' AND ar.oid = ANY(p.polroles)) "
                    "AS applies_to_app_user "
                    "FROM pg_policy AS p "
                    "JOIN pg_class AS c ON c.oid = p.polrelid "
                    "JOIN pg_namespace AS n ON n.oid = c.relnamespace "
                    "WHERE n.nspname = 'public' AND c.relrowsecurity "
                    "ORDER BY n.nspname, c.relname, p.polname"
                )
            )
        ).mappings().all()

        policies_by_table: dict[tuple[str, str], list] = {}
        for policy in policies:
            key = (policy["schema_name"], policy["table_name"])
            policies_by_table.setdefault(key, []).append(policy)
            assert policy["applies_to_app_user"], (
                f"{key}.{policy['policy_name']} is not bound to app_user"
            )
            assert not policy["applies_to_public"], (
                f"{key}.{policy['policy_name']} unexpectedly applies to PUBLIC"
            )
            assert policy["using_expr"], (
                f"{key}.{policy['policy_name']} has no USING expression"
            )

        audited_tables: list[str] = []
        for relation in relations:
            schema = relation["schema_name"]
            table = relation["table_name"]
            key = (schema, table)
            assert relation["rls_enabled"]
            assert relation["rls_forced"], f"{schema}.{table} does not FORCE RLS"
            assert relation["rls_active"], f"RLS is not active on {schema}.{table}"
            assert relation["can_select"], f"app_user cannot SELECT {schema}.{table}"
            assert not relation["can_delete"], (
                f"app_user unexpectedly has DELETE on {schema}.{table}"
            )

            table_policies = policies_by_table.get(key, [])
            tenant_policies = [
                policy
                for policy in table_policies
                if policy["command"] == "*"
                and "app.org_id" in (policy["using_expr"] or "")
                and "app.org_id" in (policy["check_expr"] or "")
            ]
            assert tenant_policies, (
                f"{schema}.{table} has no app_user ALL policy with tenant-scoped "
                "USING and WITH CHECK expressions"
            )

            # Names are catalog-derived, but keep interpolation strictly identifier-only.
            assert re.fullmatch(r"[a-z_][a-z0-9_]*", schema)
            assert re.fullmatch(r"[a-z_][a-z0-9_]*", table)
            await session.execute(text(f'SELECT count(*) FROM "{schema}"."{table}"'))
            audited_tables.append(f"{schema}.{table}")

    print(f"P1T10_RLS_BACKEND_PID={identity.pg_backend_pid}")
    print("P1T10_RLS_TABLES=" + ",".join(audited_tables))
    print(f"P1T10_RLS_TABLE_COUNT={len(audited_tables)}")
    print(f"P1T10_RLS_POLICY_COUNT={len(policies)}")
    print("P1T10_DYNAMIC_RLS_SWEEP=PASS")
