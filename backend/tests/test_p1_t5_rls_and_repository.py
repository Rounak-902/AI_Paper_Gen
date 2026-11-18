"""P1-T5 verification tests: session context, repository base class, and lineage.

These tests run against a real migrated PostgreSQL and prove the Python-side
behavior that the psql proofs in the verification script cannot reach.
"""

import uuid

import pytest
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

from app.db.exceptions import (
    EntityNotFoundError,
    OptimisticLockError,
    TenantContextMissing,
)
from app.db.repository import BaseRepository
from app.db.question_repository import QuestionBankRepository
from app.db.session import get_tenant_session, init_session_factory
from app.db.engine import create_engine as build_engine
from app.models.organization import Organization
from app.models.branch import Branch
from app.models.question import QuestionBank

pytestmark = pytest.mark.db


# ---------------------------------------------------------------------------
# Helpers â€” column names match the actual ORM models, not guesses
# ---------------------------------------------------------------------------

async def _create_org(session: AsyncSession, name: str) -> uuid.UUID:
    """Insert a test organization and return its id."""
    org_id = uuid.uuid4()
    await session.execute(
        text(
            "INSERT INTO organizations (id, name, org_type, status) "
            "VALUES (:id, :name, 'school', 'active')"
        ),
        {"id": str(org_id), "name": name},
    )
    return org_id


async def _create_board(session: AsyncSession) -> uuid.UUID:
    """Insert a test board and return its id."""
    board_id = uuid.uuid4()
    await session.execute(
        text(
            "INSERT INTO boards (id, name, code) "
            "VALUES (:id, :name, :code)"
        ),
        {"id": str(board_id), "name": f"Board-{board_id}", "code": f"B-{str(board_id)[:8]}"},
    )
    return board_id


async def _create_class(session: AsyncSession, board_id: uuid.UUID) -> uuid.UUID:
    """Insert a test class and return its id. Classes have board_id + name only."""
    cls_id = uuid.uuid4()
    await session.execute(
        text(
            "INSERT INTO classes (id, board_id, name) "
            "VALUES (:id, :board_id, :name)"
        ),
        {"id": str(cls_id), "board_id": str(board_id), "name": f"Class-{cls_id}"},
    )
    return cls_id


async def _create_subject(session: AsyncSession, class_id: uuid.UUID) -> uuid.UUID:
    """Insert a test subject and return its id. Subjects have class_id + name."""
    sub_id = uuid.uuid4()
    await session.execute(
        text(
            "INSERT INTO subjects (id, class_id, name) "
            "VALUES (:id, :class_id, :name)"
        ),
        {"id": str(sub_id), "class_id": str(class_id), "name": f"Sub-{sub_id}"},
    )
    return sub_id


async def _create_chapter(session: AsyncSession, subject_id: uuid.UUID) -> uuid.UUID:
    """Insert a test chapter and return its id. Chapters use seq + title."""
    ch_id = uuid.uuid4()
    await session.execute(
        text(
            "INSERT INTO chapters (id, subject_id, seq, title) "
            "VALUES (:id, :subject_id, :seq, :title)"
        ),
        {"id": str(ch_id), "subject_id": str(subject_id), "seq": 1, "title": f"Ch-{ch_id}"},
    )
    return ch_id


# ---------------------------------------------------------------------------
# Test: TenantContextMissing
# ---------------------------------------------------------------------------

class TestTenantContextMissing:
    """The session mechanism rejects None org_id before any DB work."""

    @pytest.mark.asyncio
    async def test_none_org_id_raises(self, db_engine):
        """Passing None to get_tenant_session raises TenantContextMissing."""
        factory = init_session_factory(db_engine)
        with pytest.raises(TenantContextMissing):
            async with get_tenant_session(None, session_factory=factory):
                pass  # pragma: no cover


# ---------------------------------------------------------------------------
# Test: Soft-delete filtering
# ---------------------------------------------------------------------------

class TestSoftDelete:
    """The repository base class filters soft-deleted rows by default."""

    @pytest.mark.asyncio
    async def test_soft_delete_excludes_from_reads(self, db_session):
        org_id = await _create_org(db_session, 'Soft-Del Org')
        await db_session.flush()

        repo = BaseRepository[Branch](db_session, org_id, model=Branch)
        branch = await repo.create(name='To Delete', status='active')
        branch_id = branch.id
        await db_session.flush()

        # Verify it's readable
        found = await repo.get_by_id(branch_id)
        assert found is not None, "branch should exist before soft delete"

        # Soft-delete it
        await repo.soft_delete(branch_id)
        await db_session.flush()

        # Normal read should NOT find it
        found_after = await repo.get_by_id(branch_id)
        assert found_after is None, "soft-deleted branch should not be visible"

        # include_deleted=True should find it
        found_deleted = await repo.get_by_id(branch_id, include_deleted=True)
        assert found_deleted is not None, "soft-deleted branch should be visible with include_deleted"
        assert found_deleted.deleted_at is not None


# ---------------------------------------------------------------------------
# Test: Optimistic locking
# ---------------------------------------------------------------------------

class TestOptimisticLocking:
    """The repository rejects updates with a stale row_version."""

    @pytest.mark.asyncio
    async def test_stale_version_raises(self, db_session):
        org_id = await _create_org(db_session, 'OptLock Org')
        await db_session.flush()

        repo = BaseRepository[Branch](db_session, org_id, model=Branch)
        branch = await repo.create(name='Locked Branch', status='active')
        branch_id = branch.id
        initial_version = branch.row_version
        await db_session.flush()

        # First update should succeed
        updated = await repo.update(branch_id, expected_version=initial_version, name='Updated Branch')
        assert updated.row_version == initial_version + 1

        # Second update with the OLD version should fail
        with pytest.raises(OptimisticLockError) as exc_info:
            await repo.update(branch_id, expected_version=initial_version, name='Stale Update')

        assert exc_info.value.expected_version == initial_version


# ---------------------------------------------------------------------------
# Test: Lineage rooting
# ---------------------------------------------------------------------------

class TestLineageRooting:
    """QuestionBankRepository sets root_question_id = id on first versions."""

    @pytest.mark.asyncio
    async def test_first_version_self_roots(self, db_session):
        org_id = await _create_org(db_session, 'Lineage Org')
        board_id = await _create_board(db_session)
        class_id = await _create_class(db_session, board_id)
        subject_id = await _create_subject(db_session, class_id)
        chapter_id = await _create_chapter(db_session, subject_id)
        await db_session.flush()

        repo = QuestionBankRepository(db_session, org_id)
        question = await repo.create(
            stem='What is 2 + 2?',
            answer={'value': '4'},
            qtype='mcq',
            source='institute_bank',
            difficulty='easy',
            marks=1,
            language='en',
            content_hash='test_hash_' + str(uuid.uuid4()),
            scope='org',
            board_id=board_id,
            class_id=class_id,
            subject_id=subject_id,
            chapter_id=chapter_id,
            status='draft',
            is_current=True,
            version_number=1,
        )

        assert question.root_question_id == question.id, (
            f"root_question_id should equal id ({question.id}), "
            f"got {question.root_question_id}"
        )

    @pytest.mark.asyncio
    async def test_fork_requires_root_id(self, db_session):
        """A forked question must provide root_question_id explicitly."""
        org_id = await _create_org(db_session, 'Fork Org')
        board_id = await _create_board(db_session)
        class_id = await _create_class(db_session, board_id)
        subject_id = await _create_subject(db_session, class_id)
        chapter_id = await _create_chapter(db_session, subject_id)
        await db_session.flush()

        repo = QuestionBankRepository(db_session, org_id)
        original = await repo.create(
            stem='Original Q',
            answer={'value': 'A'},
            qtype='mcq',
            source='institute_bank',
            difficulty='easy',
            marks=1,
            language='en',
            content_hash='test_hash_' + str(uuid.uuid4()),
            scope='org',
            board_id=board_id,
            class_id=class_id,
            subject_id=subject_id,
            chapter_id=chapter_id,
            status='draft',
            is_current=True,
            version_number=1,
        )

        with pytest.raises(ValueError, match="root_question_id"):
            await repo.create(
                stem='Forked Q without root_question_id',
                answer={'value': 'B'},
                qtype='mcq',
                source='institute_bank',
                difficulty='easy',
                marks=1,
                language='en',
                content_hash='test_hash_' + str(uuid.uuid4()),
                scope='org',
                board_id=board_id,
                class_id=class_id,
                subject_id=subject_id,
                chapter_id=chapter_id,
                status='draft',
                is_current=True,
                version_number=2,
                previous_version_id=original.id,
                # missing root_question_id on purpose
            )


# ---------------------------------------------------------------------------
# Test: EntityNotFoundError
# ---------------------------------------------------------------------------

class TestEntityNotFound:
    """get_by_id_or_raise raises EntityNotFoundError for missing rows."""

    @pytest.mark.asyncio
    async def test_missing_id_raises(self, db_session):
        org_id = await _create_org(db_session, 'NotFound Org')
        await db_session.flush()

        repo = BaseRepository[Branch](db_session, org_id, model=Branch)
        with pytest.raises(EntityNotFoundError):
            await repo.get_by_id_or_raise(uuid.uuid4())
# ===========================================================================
# Non-superuser coverage: the real code path, under RLS, as app_user
# ===========================================================================
#
# Why this section exists at all.
#
# Every test above uses conftest's db_session fixture, which connects as the
# DSN's user -- postgres, a SUPERUSER. PostgreSQL exempts superusers from
# row-level security entirely, even on a table carrying FORCE ROW LEVEL
# SECURITY. So no assertion made through db_session can see RLS: those tests
# would pass identically if migration 0025 had never run. And the 27 live SQL
# proofs in scripts/verify-p1-t5.ps1 set the tenant context with a literal
# SET LOCAL string written in the script itself, so they prove the *policies*
# are right while never once executing the application's own session helper.
#
# The tests below close that gap: they connect as app_user (created
# NOBYPASSRLS by migration 0024) and drive isolation through the real
# get_tenant_session().
#
# db_session is deliberately left untouched -- the tests above depend on its
# superuser privileges for seeding and on its enclosing rollback for cleanup.

import os

from dataclasses import dataclass

from sqlalchemy.exc import DBAPIError
from sqlalchemy.ext.asyncio import create_async_engine

#: The non-superuser role migration 0024 creates.
APP_USER = "app_user"

def _require_app_user_password() -> str:
    """Return the centrally provisioned app_user credential.

    ``infra/postgres/init/02-provision-app-user.sh`` owns role credentials. Tests
    consume the same environment-provided value and never mutate cluster roles.
    """

    password = os.environ.get("PAPERGEN_APP_DB_PASSWORD")
    if not password:
        pytest.fail(
            "PAPERGEN_APP_DB_PASSWORD is required; provision app_user through "
            "infra/postgres/init/02-provision-app-user.sh before running this test.",
            pytrace=False,
        )
    return password


@dataclass(frozen=True)
class TwoTenants:
    """Two organizations, one branch each, COMMITTED.

    Committed rather than created inside the enclosing test transaction because
    the app_user tests read them over a *second, independent connection*, and an
    uncommitted row is invisible there.
    """

    org_a: uuid.UUID
    org_b: uuid.UUID
    branch_a: uuid.UUID
    branch_b: uuid.UUID


@pytest.fixture
async def committed_tenants(db_engine):
    """Seed two tenants as superuser, then delete them again.

    Teardown is unconditional: these rows are committed, so a failing test that
    skipped cleanup would leave residue behind exactly the way the two cancelled
    verification runs did.
    """

    ids = TwoTenants(
        org_a=uuid.uuid4(),
        org_b=uuid.uuid4(),
        branch_a=uuid.uuid4(),
        branch_b=uuid.uuid4(),
    )

    async with db_engine.begin() as conn:
        for org_id, label in ((ids.org_a, "A"), (ids.org_b, "B")):
            await conn.execute(
                text(
                    "INSERT INTO organizations (id, name, org_type, status) "
                    "VALUES (:id, :name, 'school', 'active')"
                ),
                {"id": str(org_id), "name": "p1t5-appuser-org-" + label},
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
                    "id": str(branch_id),
                    "org_id": str(org_id),
                    "name": "p1t5-appuser-branch-" + label,
                },
            )

    try:
        yield ids
    finally:
        async with db_engine.begin() as conn:
            await conn.execute(
                text("DELETE FROM branches WHERE org_id IN (:a, :b)"),
                {"a": str(ids.org_a), "b": str(ids.org_b)},
            )
            await conn.execute(
                text("DELETE FROM organizations WHERE id IN (:a, :b)"),
                {"a": str(ids.org_a), "b": str(ids.org_b)},
            )


@pytest.fixture
async def app_user_engine(db_engine):
    """An engine connected as app_user over a pool of exactly one connection.

    Two deliberate choices.

    ``pool_size=1, max_overflow=0`` guarantees that consecutive checkouts land on
    the same physical backend, which is what makes the pooled-checkout leak test
    below meaningful rather than lucky. The test still asserts
    ``pg_backend_pid()`` equality, so if the pool ever hands out a second
    connection the test fails instead of passing vacuously.

    The engine is built with ``create_async_engine`` directly rather than
    ``app.db.engine.create_engine`` for the same reason: ``create_engine``
    chooses ``NullPool`` for a transaction-pooled DSN, and a NullPool never
    reuses a connection, so the leak test would have nothing to prove.

    The DSN is derived from ``db_engine.url`` so the conftest guard that refuses
    any database not named ``*_test`` still applies.
    """

    app_user_password = _require_app_user_password()
    url = db_engine.url.set(username=APP_USER, password=app_user_password)
    engine = create_async_engine(url, pool_size=1, max_overflow=0, future=True)
    try:
        yield engine
    finally:
        await engine.dispose()


@pytest.fixture
async def app_user_factory(app_user_engine):
    """The application's own session factory, bound to the app_user engine."""

    return init_session_factory(app_user_engine)


async def _scalar(session: AsyncSession, sql: str, **params):
    """Run a scalar query, keeping the tests readable."""

    result = await session.execute(text(sql), params or None)
    return result.scalar_one()


# ---------------------------------------------------------------------------
# Test: the real session helper actually applies the tenant context
# ---------------------------------------------------------------------------

class TestTenantContextIsActuallyApplied:
    """get_tenant_session() sets app.org_id on a non-superuser connection."""

    @pytest.mark.asyncio
    async def test_set_config_applies_the_org_id(self, app_user_factory, committed_tenants):
        """Regression test for the parameterized-SET bug.

        The previous implementation issued ``SET LOCAL app.org_id = :org_id``.
        ``SET`` is a utility statement whose grammar takes a literal, so the
        driver's ``$1`` was a syntax error and this function raised on every
        call. Any change back to a bound ``SET`` fails here.
        """

        async with get_tenant_session(
            committed_tenants.org_a, session_factory=app_user_factory
        ) as session:
            applied = await _scalar(session, "SELECT current_setting('app.org_id', true)")

        assert applied == str(committed_tenants.org_a), (
            "get_tenant_session() did not apply the tenant context; "
            f"current_setting('app.org_id') was {applied!r}"
        )


# ---------------------------------------------------------------------------
# Test: tenant isolation through the real code path, as a non-superuser
# ---------------------------------------------------------------------------

class TestTenantIsolationAsAppUser:
    """RLS confines app_user to its own tenant when driven by the real helper."""

    @pytest.mark.asyncio
    async def test_reads_see_only_the_current_tenant(self, app_user_factory, committed_tenants):
        """An unqualified count is the strongest form of this assertion.

        ``SELECT count(*) FROM branches`` with no WHERE clause returns 1 only
        because RLS filtered the other tenant's row out. Both org ids are freshly
        generated, so leftover rows from any other run cannot affect the count.
        """

        async with get_tenant_session(
            committed_tenants.org_a, session_factory=app_user_factory
        ) as session:
            assert await _scalar(session, "SELECT count(*) FROM branches") == 1
            visible = await _scalar(
                session,
                "SELECT count(*) FROM branches WHERE id = :bid",
                bid=str(committed_tenants.branch_b),
            )
            assert visible == 0, "org A's session could see org B's branch"

        async with get_tenant_session(
            committed_tenants.org_b, session_factory=app_user_factory
        ) as session:
            assert await _scalar(session, "SELECT count(*) FROM branches") == 1
            visible = await _scalar(
                session,
                "SELECT count(*) FROM branches WHERE id = :bid",
                bid=str(committed_tenants.branch_a),
            )
            assert visible == 0, "org B's session could see org A's branch"

    @pytest.mark.asyncio
    async def test_rls_holds_when_the_repository_is_misconfigured(
        self, app_user_factory, committed_tenants
    ):
        """The second line of defense, proved independently of the first.

        The repository is constructed with org B's id while the session context
        is org A's. The application-level predicate therefore asks for the wrong
        tenant's row on purpose; RLS has to be what stops it. This is the case
        the superuser-connected tests structurally cannot cover.
        """

        async with get_tenant_session(
            committed_tenants.org_a, session_factory=app_user_factory
        ) as session:
            correct = BaseRepository[Branch](session, committed_tenants.org_a, model=Branch)
            assert await correct.get_by_id(committed_tenants.branch_a) is not None

            misconfigured = BaseRepository[Branch](
                session, committed_tenants.org_b, model=Branch
            )
            leaked = await misconfigured.get_by_id(committed_tenants.branch_b)
            assert leaked is None, (
                "RLS did not stop a repository pointed at another tenant while "
                "the session context belonged to org A"
            )

    @pytest.mark.asyncio
    async def test_cross_tenant_write_is_rejected(self, app_user_factory, committed_tenants):
        """A write aimed at another tenant is refused by the policy's WITH CHECK."""

        with pytest.raises(DBAPIError) as exc_info:
            async with get_tenant_session(
                committed_tenants.org_a, session_factory=app_user_factory
            ) as session:
                repo = BaseRepository[Branch](session, committed_tenants.org_a, model=Branch)
                # org_id passed explicitly, so the repository does not inject its
                # own -- this is the cross-tenant INSERT the policy must refuse.
                await repo.create(
                    org_id=committed_tenants.org_b,
                    name="p1t5-cross-tenant-write",
                    status="active",
                )

        assert "row-level security" in str(exc_info.value).lower(), (
            "expected an RLS policy violation, got: " + str(exc_info.value)
        )


# ---------------------------------------------------------------------------
# Test: doc 03 section 8's pooled-checkout requirement
# ---------------------------------------------------------------------------

class TestPooledCheckoutDoesNotLeakTenantContext:
    """doc 03 section 8, verbatim:

        "Every session runs inside a request-scoped transaction that issues
         `SET LOCAL app.org_id = :org_id` **before any query** -- never a
         session-level `SET`. Under PgBouncer transaction pooling a session-level
         setting leaks the tenant context to the next borrower of that backend,
         which is a silent cross-tenant read. This is enforced in the repository
         base class and covered by an isolation test that asserts the setting
         does not survive a pooled checkout."

    "the next borrower of that backend" is what the test below simulates: the
    same physical connection is handed out twice, and the second borrower must
    not be able to see the first borrower's tenant context.
    """

    @pytest.mark.asyncio
    async def test_org_id_does_not_survive_a_pooled_checkout(
        self, app_user_factory, committed_tenants
    ):
        async with get_tenant_session(
            committed_tenants.org_a, session_factory=app_user_factory
        ) as session:
            first_pid = await _scalar(session, "SELECT pg_backend_pid()")
            assert await _scalar(
                session, "SELECT current_setting('app.org_id', true)"
            ) == str(committed_tenants.org_a)
        # Transaction committed and the connection went back to the pool here.

        # A second "request" that sets no context at all.
        async with app_user_factory() as second:
            async with second.begin():
                second_pid = await _scalar(second, "SELECT pg_backend_pid()")
                leaked = await _scalar(
                    second, "SELECT current_setting('app.org_id', true)"
                )

        assert second_pid == first_pid, (
            "the pool handed out a different backend (pids "
            f"{first_pid} then {second_pid}), so this run proves nothing about "
            "leakage; app_user_engine must keep pool_size=1, max_overflow=0"
        )
        assert leaked in (None, ""), (
            "app.org_id survived a pooled checkout and leaked org A's context to "
            f"the next borrower of backend {second_pid}: {leaked!r}"
        )

    @pytest.mark.asyncio
    async def test_reused_connection_fails_closed_without_context(
        self, app_user_factory, committed_tenants
    ):
        """Absent context is an error, not an unfiltered read.

        Two different errors are acceptable here and which one appears depends on
        history, so the assertion is on the failure, not on the message. On a
        connection where app.org_id was never set, ``current_setting`` raises
        ``unrecognized configuration parameter``. On a connection that has
        already served a tenant-scoped transaction the placeholder exists with an
        empty reset value, so the policy's ``::uuid`` cast raises ``invalid input
        syntax for type uuid: ""`` instead. Either way the read fails; what must
        never happen is rows coming back.
        """

        async with get_tenant_session(
            committed_tenants.org_a, session_factory=app_user_factory
        ) as session:
            assert await _scalar(session, "SELECT count(*) FROM branches") == 1

        with pytest.raises(DBAPIError):
            async with app_user_factory() as second:
                async with second.begin():
                    await second.execute(text("SELECT count(*) FROM branches"))

        # The connection is still usable, and a fresh context is applied cleanly.
        async with get_tenant_session(
            committed_tenants.org_b, session_factory=app_user_factory
        ) as third:
            assert await _scalar(third, "SELECT count(*) FROM branches") == 1
            assert await _scalar(
                third, "SELECT current_setting('app.org_id', true)"
            ) == str(committed_tenants.org_b)
