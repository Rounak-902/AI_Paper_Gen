"""Live database contract tests for P3-T3 prompt activation and resolution.

These tests intentionally join the real PostgreSQL transaction supplied by
``tests/conftest.py``.  The file package is pure and separately unit-tested; this
module proves that database release metadata selects those real immutable files and
that revision 0032 makes promotion/rollback one UPDATE at the application boundary.
"""

from __future__ import annotations

import re
import uuid
from collections.abc import Callable
from datetime import datetime, timezone

import pytest
import sqlalchemy as sa
from sqlalchemy import event
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from app.models import PromptVersion
from app.modules.prompts import (
    ActivePromptNotFoundError,
    PromptResolver,
    PromptVersionRepository,
)
from packages.prompts import PromptNotFoundError


pytestmark = pytest.mark.db

ACTIVE_INDEX = "uq_prompt_versions_task_model_active"
PROMOTION_TRIGGER = "trg_prompt_versions_retire_active"
PROMOTION_FUNCTION = "fn_prompt_versions_retire_active_for_promotion"


def _record_external_updates(
    session: AsyncSession,
) -> tuple[list[str], Callable[[], None]]:
    """Capture SQL sent by SQLAlchemy, excluding statements run inside triggers."""

    statements: list[str] = []
    sync_bind = session.sync_session.get_bind()

    def before_cursor_execute(
        _connection,
        _cursor,
        statement: str,
        _parameters,
        _context,
        _executemany: bool,
    ) -> None:
        if re.match(r"^\s*UPDATE\b", statement, flags=re.IGNORECASE):
            statements.append(statement)

    event.listen(sync_bind, "before_cursor_execute", before_cursor_execute)

    def remove_listener() -> None:
        event.remove(sync_bind, "before_cursor_execute", before_cursor_execute)

    return statements, remove_listener


async def test_revision_0032_catalog_has_exact_active_index_and_promotion_trigger(
    db_session: AsyncSession,
) -> None:
    """The partial index remains the invariant; the trigger only enables the swap."""

    index_row = (
        await db_session.execute(
            sa.text(
                """
                SELECT
                    index_catalog.indisunique,
                    index_catalog.indimmediate,
                    pg_catalog.pg_get_expr(
                        index_catalog.indpred,
                        index_catalog.indrelid
                    ) AS predicate,
                    ARRAY(
                        SELECT attribute.attname
                        FROM unnest(index_catalog.indkey)
                            WITH ORDINALITY AS key_column(attnum, position)
                        JOIN pg_catalog.pg_attribute AS attribute
                          ON attribute.attrelid = index_catalog.indrelid
                         AND attribute.attnum = key_column.attnum
                        WHERE key_column.position <= index_catalog.indnkeyatts
                        ORDER BY key_column.position
                    ) AS key_columns
                FROM pg_catalog.pg_index AS index_catalog
                JOIN pg_catalog.pg_class AS index_relation
                  ON index_relation.oid = index_catalog.indexrelid
                JOIN pg_catalog.pg_namespace AS index_namespace
                  ON index_namespace.oid = index_relation.relnamespace
                WHERE index_namespace.nspname = 'public'
                  AND index_relation.relname = :index_name
                """
            ),
            {"index_name": ACTIVE_INDEX},
        )
    ).one_or_none()

    assert index_row is not None, f"revision 0016 index {ACTIVE_INDEX} is missing"
    assert index_row.indisunique is True
    assert index_row.indimmediate is True
    assert list(index_row.key_columns) == ["task", "model"]
    assert index_row.predicate == "(status = 'active'::text)"
    assert "deleted_at" not in index_row.predicate

    trigger_row = (
        await db_session.execute(
            sa.text(
                """
                SELECT
                    trigger_catalog.tgenabled::text AS tgenabled,
                    trigger_catalog.tgtype,
                    function_catalog.proname,
                    function_catalog.prosecdef,
                    function_catalog.proconfig,
                    pg_catalog.pg_get_triggerdef(trigger_catalog.oid, false)
                        AS definition
                FROM pg_catalog.pg_trigger AS trigger_catalog
                JOIN pg_catalog.pg_class AS target_relation
                  ON target_relation.oid = trigger_catalog.tgrelid
                JOIN pg_catalog.pg_namespace AS target_namespace
                  ON target_namespace.oid = target_relation.relnamespace
                JOIN pg_catalog.pg_proc AS function_catalog
                  ON function_catalog.oid = trigger_catalog.tgfoid
                WHERE target_namespace.nspname = 'public'
                  AND target_relation.relname = 'prompt_versions'
                  AND trigger_catalog.tgname = :trigger_name
                  AND NOT trigger_catalog.tgisinternal
                """
            ),
            {"trigger_name": PROMOTION_TRIGGER},
        )
    ).one_or_none()

    assert trigger_row is not None, (
        f"revision 0032 trigger {PROMOTION_TRIGGER} is missing"
    )
    assert trigger_row.tgenabled == "O", "promotion trigger must be enabled normally"
    # PostgreSQL's trigger type bitmap: ROW (1) | BEFORE (2) | UPDATE (16).
    assert trigger_row.tgtype == 19
    assert trigger_row.proname == PROMOTION_FUNCTION
    assert trigger_row.prosecdef is False, "promotion hook must remain SECURITY INVOKER"
    assert list(trigger_row.proconfig or ()) == ["search_path=pg_catalog, public"]
    assert "BEFORE UPDATE OF status ON public.prompt_versions" in trigger_row.definition
    assert "FOR EACH ROW" in trigger_row.definition
    assert "new.status = 'active'::text" in trigger_row.definition.lower()
    assert "EXECUTE FUNCTION" in trigger_row.definition
    assert f"{PROMOTION_FUNCTION}()" in trigger_row.definition


async def test_active_resolution_and_one_external_update_promote_and_rollback(
    db_session: AsyncSession,
) -> None:
    """A live resolver observes v1 -> v2 -> v1 without a restart or file edit."""

    # Use the same model for two tasks so the isolation assertion catches a
    # promotion hook that accidentally filters by model but forgets ``task``.
    shared_model = f"p3t3-shared-model-{uuid.uuid4()}"
    generate_v1 = PromptVersion(
        task="generate_mcq",
        version=1,
        template_ref="generate_mcq/v1",
        model=shared_model,
        status="active",
    )
    generate_v2 = PromptVersion(
        task="generate_mcq",
        version=2,
        template_ref="generate_mcq/v2",
        model=shared_model,
        status="candidate",
        previous_version=generate_v1,
    )
    classify_v1 = PromptVersion(
        task="classify_difficulty",
        version=1,
        template_ref="classify_difficulty/v1",
        model=shared_model,
        status="active",
    )
    db_session.add_all([generate_v1, generate_v2, classify_v1])
    await db_session.commit()
    generate_v1_id = generate_v1.id
    generate_v2_id = generate_v2.id
    classify_v1_id = classify_v1.id

    repository = PromptVersionRepository(db_session)
    resolver = PromptResolver(repository)
    initial = await resolver.resolve(task="generate_mcq", model=shared_model)
    independent_before = await resolver.resolve(
        task="classify_difficulty", model=shared_model
    )
    assert initial.prompt_version_id == generate_v1_id
    assert initial.template_ref == "generate_mcq/v1"
    assert "curriculum-bound" in initial.layers.system
    assert '"task": "generate_mcq"' in initial.text
    assert independent_before.prompt_version_id == classify_v1_id
    independent_digest = independent_before.content_sha256

    external_updates, remove_listener = _record_external_updates(db_session)
    try:
        promoted = await repository.activate(generate_v2_id)
        await db_session.commit()
    finally:
        remove_listener()
    assert promoted is not None and promoted.id == generate_v2_id
    assert len(external_updates) == 1
    assert re.match(r"^\s*UPDATE\s+prompt_versions\b", external_updates[0], re.I)
    # The candidate's complete validated release snapshot is an optimistic guard:
    # metadata changed between file validation and this statement cannot go active.
    promotion_sql = external_updates[0].lower()
    for guarded_column in ("task", "version", "template_ref", "model", "row_version"):
        assert f"prompt_versions.{guarded_column} =" in promotion_sql

    # The incumbent was changed inside a trigger.  The repository must synchronize
    # already-loaded ORM objects even though expire_on_commit=False.
    assert generate_v1.status == "retired"
    assert generate_v1.row_version == 2
    assert generate_v2.status == "active"
    assert generate_v2.row_version == 2
    promoted_resolution = await resolver.resolve(
        task="generate_mcq", model=shared_model
    )
    independent_after_promotion = await resolver.resolve(
        task="classify_difficulty", model=shared_model
    )
    assert promoted_resolution.prompt_version_id == generate_v2_id
    assert promoted_resolution.template_ref == "generate_mcq/v2"
    assert promoted_resolution.content_sha256 != initial.content_sha256
    assert independent_after_promotion.prompt_version_id == classify_v1_id
    assert independent_after_promotion.content_sha256 == independent_digest

    external_updates, remove_listener = _record_external_updates(db_session)
    try:
        rolled_back = await repository.activate(generate_v1_id)
        await db_session.commit()
    finally:
        remove_listener()
    assert rolled_back is not None and rolled_back.id == generate_v1_id
    assert len(external_updates) == 1
    assert re.match(r"^\s*UPDATE\s+prompt_versions\b", external_updates[0], re.I)
    rollback_sql = external_updates[0].lower()
    for guarded_column in ("task", "version", "template_ref", "model", "row_version"):
        assert f"prompt_versions.{guarded_column} =" in rollback_sql

    assert generate_v1.status == "active"
    assert generate_v1.row_version == 3
    assert generate_v2.status == "retired"
    assert generate_v2.row_version == 3
    rolled_back_resolution = await resolver.resolve(
        task="generate_mcq", model=shared_model
    )
    independent_after_rollback = await resolver.resolve(
        task="classify_difficulty", model=shared_model
    )
    assert rolled_back_resolution.prompt_version_id == generate_v1_id
    assert rolled_back_resolution.template_ref == "generate_mcq/v1"
    assert rolled_back_resolution.content_sha256 == initial.content_sha256
    assert independent_after_rollback.prompt_version_id == classify_v1_id
    assert independent_after_rollback.content_sha256 == independent_digest

    stored = {
        row.id: row
        for row in (
            await db_session.execute(
                sa.select(PromptVersion).where(
                    PromptVersion.id.in_((generate_v1_id, generate_v2_id))
                )
            )
        ).scalars()
    }
    assert stored[generate_v1_id].status == "active"
    assert stored[generate_v2_id].status == "retired"
    assert stored[generate_v1_id].row_version == 3
    assert stored[generate_v2_id].row_version == 3


async def test_partial_unique_index_rejects_direct_duplicate_active_insert(
    db_session: AsyncSession,
) -> None:
    """Bypassing repository/trigger on INSERT still reaches the database backstop."""

    task = f"p3t3_duplicate_probe_{uuid.uuid4().hex}"
    model = f"p3t3-duplicate-model-{uuid.uuid4()}"
    db_session.add(
        PromptVersion(
            task=task,
            version=1,
            template_ref="probe/v1",
            model=model,
            status="active",
        )
    )
    await db_session.commit()

    with pytest.raises(IntegrityError) as exc_info:
        await db_session.execute(
            sa.text(
                """
                INSERT INTO prompt_versions
                    (task, version, template_ref, model, status)
                VALUES
                    (:task, 2, 'probe/v2', :model, 'active')
                """
            ),
            {"task": task, "model": model},
        )

    assert getattr(exc_info.value.orig, "sqlstate", None) == "23505"
    assert ACTIVE_INDEX in str(exc_info.value)
    await db_session.rollback()


async def test_invalid_candidate_cannot_retire_working_incumbent(
    db_session: AsyncSession,
) -> None:
    """The application release path validates the file before issuing its UPDATE."""

    model = f"p3t3-invalid-candidate-model-{uuid.uuid4()}"
    incumbent = PromptVersion(
        task="generate_mcq",
        version=1,
        template_ref="generate_mcq/v1",
        model=model,
        status="active",
    )
    invalid_candidate = PromptVersion(
        task="generate_mcq",
        version=999,
        template_ref="generate_mcq/v999",
        model=model,
        status="candidate",
        previous_version=incumbent,
    )
    db_session.add_all([incumbent, invalid_candidate])
    await db_session.commit()

    repository = PromptVersionRepository(db_session)
    external_updates, remove_listener = _record_external_updates(db_session)
    try:
        with pytest.raises(PromptNotFoundError, match="generate_mcq/v999"):
            await repository.activate(invalid_candidate.id)
    finally:
        remove_listener()

    assert external_updates == []
    await db_session.commit()
    stored_incumbent = await repository.get_by_id(incumbent.id)
    stored_candidate = await repository.get_by_id(invalid_candidate.id)
    assert stored_incumbent is not None
    assert stored_incumbent.status == "active"
    assert stored_incumbent.row_version == 1
    assert stored_candidate is not None
    assert stored_candidate.status == "candidate"
    assert stored_candidate.row_version == 1


async def test_registry_fails_closed_for_absent_deleted_and_ineligible_targets(
    db_session: AsyncSession,
) -> None:
    """No fallback selects a candidate, retired, soft-deleted, or different pair."""

    task = "generate_mcq"
    model = f"p3t3-fail-closed-model-{uuid.uuid4()}"
    other_model = f"p3t3-other-model-{uuid.uuid4()}"
    candidate = PromptVersion(
        task=task,
        version=1,
        template_ref="generate_mcq/v1",
        model=model,
        status="candidate",
    )
    deleted_active = PromptVersion(
        task=task,
        version=2,
        template_ref="generate_mcq/v2",
        model=other_model,
        status="active",
        deleted_at=datetime.now(timezone.utc),
    )
    db_session.add_all([candidate, deleted_active])
    await db_session.commit()

    repository = PromptVersionRepository(db_session)
    resolver = PromptResolver(repository)
    with pytest.raises(ActivePromptNotFoundError):
        await resolver.resolve(task=task, model=model)
    with pytest.raises(ActivePromptNotFoundError):
        await resolver.resolve(task=task, model=other_model)
    with pytest.raises(ActivePromptNotFoundError):
        await resolver.resolve(task=task, model="missing-model")
    assert await repository.activate(uuid.uuid4()) is None

    promoted = await repository.activate(candidate.id)
    assert promoted is not None
    await db_session.commit()
    assert await repository.activate(candidate.id) is None
