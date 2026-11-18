"""Real-PostgreSQL contracts for migration 0031's privileged write path."""

from __future__ import annotations

import uuid
from decimal import Decimal

import pytest
import sqlalchemy as sa
from sqlalchemy.exc import DBAPIError, ProgrammingError

from app.models import Membership, Organization, User
from app.modules.questions.repository import QuestionRepository

pytestmark = pytest.mark.db


async def _seed_actor(db_session, *, role: str, suffix: str):
    org = Organization(name=f"P2T4 global RLS org {suffix}")
    user = User(
        email=f"p2t4-global-rls-{suffix}@papergen.test",
        full_name=f"P2T4 {role}",
    )
    db_session.add_all([org, user])
    await db_session.flush()
    db_session.add(
        Membership(
            user_id=user.id,
            org_id=org.id,
            role=role,
            status="active",
            branch_id=None,
        )
    )
    await db_session.flush()
    return org.id, user.id


async def _become_app_actor(db_session, *, org_id: uuid.UUID, user_id: uuid.UUID) -> None:
    # The test connection is owned by postgres, so SET LOCAL ROLE gives this same
    # transaction app_user's real grants and RLS behavior while retaining visibility
    # of the uncommitted deterministic fixtures above.
    await db_session.execute(sa.text("SET LOCAL ROLE app_user"))
    await db_session.execute(
        sa.text("SELECT set_config('app.org_id', :org_id, true)"),
        {"org_id": str(org_id)},
    )
    await db_session.execute(
        sa.text("SELECT set_config('app.user_id', :user_id, true)"),
        {"user_id": str(user_id)},
    )


async def _create_global(repo: QuestionRepository, *, user_id: uuid.UUID, stem: str):
    return await repo.create_question(
        qtype="true_false",
        marks=Decimal("1.0"),
        difficulty="easy",
        language="en",
        stem=stem,
        source="institute_bank",
        scope="global",
        org_id=None,
        created_by=user_id,
        answer={"value": True},
    )


@pytest.mark.asyncio
async def test_active_super_admin_can_insert_and_self_root_global_question(db_session) -> None:
    org_id, user_id = await _seed_actor(
        db_session, role="super_admin", suffix=uuid.uuid4().hex
    )
    await _become_app_actor(db_session, org_id=org_id, user_id=user_id)

    result = await _create_global(
        QuestionRepository(db_session, org_id),
        user_id=user_id,
        stem=f"P2T4 live RLS positive {uuid.uuid4()}",
    )

    assert result.question.scope == "global"
    assert result.question.org_id is None
    # This assertion proves both policies: INSERT creates the row, then UPDATE
    # completes QuestionBankRepository's first-version self-rooting flush.
    assert result.question.root_question_id == result.question.id


@pytest.mark.parametrize("role", ["teacher", "coaching_admin"])
@pytest.mark.asyncio
async def test_non_super_admin_cannot_insert_global_question(db_session, role: str) -> None:
    org_id, user_id = await _seed_actor(
        db_session, role=role, suffix=uuid.uuid4().hex
    )
    await _become_app_actor(db_session, org_id=org_id, user_id=user_id)

    with pytest.raises((ProgrammingError, DBAPIError)) as exc_info:
        await _create_global(
            QuestionRepository(db_session, org_id),
            user_id=user_id,
            stem=f"P2T4 live RLS denied {role} {uuid.uuid4()}",
        )

    original = exc_info.value.orig
    assert getattr(original, "sqlstate", None) == "42501"
    assert "row-level security policy" in str(original).lower()
    assert "question_bank" in str(original)


@pytest.mark.asyncio
async def test_missing_actor_context_does_not_turn_tenant_session_privileged(db_session) -> None:
    org_id, user_id = await _seed_actor(
        db_session, role="super_admin", suffix=uuid.uuid4().hex
    )
    await db_session.execute(sa.text("SET LOCAL ROLE app_user"))
    await db_session.execute(
        sa.text("SELECT set_config('app.org_id', :org_id, true)"),
        {"org_id": str(org_id)},
    )

    with pytest.raises((ProgrammingError, DBAPIError)) as exc_info:
        await _create_global(
            QuestionRepository(db_session, org_id),
            user_id=user_id,
            stem=f"P2T4 missing actor context {uuid.uuid4()}",
        )

    assert getattr(exc_info.value.orig, "sqlstate", None) == "42501"


@pytest.mark.asyncio
async def test_soft_deleted_super_admin_membership_is_not_privileged(db_session) -> None:
    org_id, user_id = await _seed_actor(
        db_session, role="super_admin", suffix=uuid.uuid4().hex
    )
    await db_session.execute(
        sa.update(Membership)
        .where(Membership.user_id == user_id, Membership.org_id == org_id)
        .values(deleted_at=sa.func.now())
    )
    await db_session.flush()
    await _become_app_actor(db_session, org_id=org_id, user_id=user_id)

    with pytest.raises((ProgrammingError, DBAPIError)) as exc_info:
        await _create_global(
            QuestionRepository(db_session, org_id),
            user_id=user_id,
            stem=f"P2T4 deleted super-admin membership {uuid.uuid4()}",
        )

    assert getattr(exc_info.value.orig, "sqlstate", None) == "42501"


@pytest.mark.asyncio
async def test_migration_keeps_tenant_policy_and_installs_only_named_global_policies(
    db_session,
) -> None:
    policies = (
        await db_session.execute(
            sa.text(
                """
                SELECT policyname, cmd, permissive, roles, qual, with_check
                FROM pg_policies
                WHERE schemaname = 'public' AND tablename = 'question_bank'
                ORDER BY policyname
                """
            )
        )
    ).mappings().all()
    by_name = {row["policyname"]: row for row in policies}

    tenant_check = by_name["tenant_isolation_policy"]["with_check"]
    assert "org_id IS NULL" not in tenant_check
    assert by_name["global_question_super_admin_insert"]["cmd"] == "INSERT"
    assert by_name["global_question_super_admin_update"]["cmd"] == "UPDATE"
    assert by_name["global_question_super_admin_insert"]["roles"] == ["app_user"]
    assert by_name["global_question_super_admin_update"]["roles"] == ["app_user"]
    assert "app_is_active_super_admin" in by_name[
        "global_question_super_admin_insert"
    ]["with_check"]

    function = (
        await db_session.execute(
            sa.text(
                """
                SELECT prosecdef, proconfig, proacl::text AS acl
                FROM pg_proc
                WHERE oid = 'public.app_is_active_super_admin()'::regprocedure
                """
            )
        )
    ).mappings().one()
    assert function["prosecdef"] is True
    assert "search_path=pg_catalog, public" in function["proconfig"]
    assert "app_user=X" in function["acl"]
    assert not function["acl"].startswith("{=X")
    assert ",=X" not in function["acl"]  # PUBLIC has no EXECUTE entry.
