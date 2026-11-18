"""Shared pytest configuration.

Two independent concerns live here.

**1. Environment for import-time configuration.** ``app.core.config.Settings``
validates at import, so every required variable must exist before any ``app.*``
module is imported. The assignments below are unconditional on purpose: a developer
with a real ``DATABASE_URL`` exported in their shell must not have the test suite
silently retarget itself at that database.

**2. Fixtures for tests that need a real PostgreSQL database.** These tests do not
mock the database — the point of them is to prove the DDL and the ORM agree, which a
fake cannot do. They therefore require:

* a reachable PostgreSQL 16 instance (``infra/docker-compose.yml`` starts one), and
* ``alembic upgrade head`` already applied to the target database.

If the schema is missing the fixtures fail with that instruction rather than
skipping — a skipped database test looks like a pass in CI, which is worse than no
test at all.

The target DSN comes from ``TEST_DATABASE_URL`` when set, otherwise
:data:`LOCAL_TEST_DSN`. Note that this is deliberately *not* ``DATABASE_URL``: the
override has to be explicit, so no ambient environment variable can point a suite
that writes rows at a development or production database.
"""

import os
import uuid
from dataclasses import dataclass
from pathlib import Path
from urllib.parse import quote

import pytest
from sqlalchemy import text
from sqlalchemy.engine import make_url
from sqlalchemy.ext.asyncio import AsyncSession

def _read_local_infra_env() -> dict[str, str]:
    """Read ignored local-infra credentials without importing application settings."""

    env_file = Path(__file__).resolve().parents[2] / "infra" / ".env"
    if not env_file.is_file():
        return {}

    values: dict[str, str] = {}
    for raw_line in env_file.read_text(encoding="utf-8-sig").splitlines():
        line = raw_line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        name, value = line.split("=", 1)
        values[name.strip()] = value.strip()
    return values


_LOCAL_INFRA = _read_local_infra_env()
_LOCAL_POSTGRES_PASSWORD = os.environ.get("POSTGRES_PASSWORD") or _LOCAL_INFRA.get(
    "POSTGRES_PASSWORD"
)
_LOCAL_POSTGRES_PORT = os.environ.get("POSTGRES_PORT") or _LOCAL_INFRA.get(
    "POSTGRES_PORT", "5432"
)

# Keep a syntactically valid URL so unit-only modules can collect before local infra is
# configured. _resolve_test_database_url emits an actionable failure before any DB test
# uses the sentinel credential.
_LOCAL_DSN_PASSWORD = _LOCAL_POSTGRES_PASSWORD or "local_infra_password_required"
LOCAL_TEST_DSN = (
    "postgresql+asyncpg://postgres:"
    f"{quote(_LOCAL_DSN_PASSWORD, safe='')}@localhost:{_LOCAL_POSTGRES_PORT}/papergen_test"
)

# Ensure safe default test environment variables are available for module import
os.environ["DATABASE_URL"] = LOCAL_TEST_DSN
os.environ["JWT_SECRET"] = "access_secret_key_with_high_entropy_32_chars_minimum_ok"
os.environ["JWT_REFRESH_SECRET"] = "refresh_secret_key_with_high_entropy_32_chars_minimum_ok"
os.environ["GEMINI_API_KEY"] = "valid_test_gemini_api_key_sample_123456789"
# Synthetic model IDs on purpose: the real tier IDs are a deployment decision and
# must never be baked into code or fixtures (see app/core/config.py §5).
os.environ["AI_MODEL_GENERATE"] = "test-model-generate-v1"
os.environ["AI_MODEL_CLASSIFY"] = "test-model-classify-v1"
os.environ["AI_EMBEDDING_MODEL"] = "test-model-embedding-v1"
os.environ["ENVIRONMENT"] = "test"

# P1-T5/P1-T8 non-superuser fixtures and Redis-backed auth tests consume the same
# ignored credentials as Compose. Explicit process values still win for a caller that
# deliberately points TEST_DATABASE_URL at another throwaway environment.
if _LOCAL_INFRA.get("PAPERGEN_APP_DB_PASSWORD"):
    os.environ.setdefault(
        "PAPERGEN_APP_DB_PASSWORD", _LOCAL_INFRA["PAPERGEN_APP_DB_PASSWORD"]
    )
if _LOCAL_INFRA.get("REDIS_PASSWORD"):
    redis_password = quote(_LOCAL_INFRA["REDIS_PASSWORD"], safe="")
    redis_port = os.environ.get("REDIS_PORT") or _LOCAL_INFRA.get("REDIS_PORT", "6379")
    os.environ.setdefault(
        "REDIS_URL", f"redis://:{redis_password}@localhost:{redis_port}/0"
    )
    os.environ.setdefault(
        "QUEUE_REDIS_URL", f"redis://:{redis_password}@localhost:{redis_port}/1"
    )

from app.core.config import get_settings
from app.db.engine import create_engine


@pytest.fixture(autouse=True)
def reset_settings_cache():
    """Reset the lru_cache on get_settings before and after every test."""
    get_settings.cache_clear()
    yield
    get_settings.cache_clear()


# ---------------------------------------------------------------------------
# Database fixtures (P1-T3)
# ---------------------------------------------------------------------------

#: Escape hatch for the "must look like a test database" guard below.
ALLOW_NON_TEST_DB_ENV = "PAPERGEN_ALLOW_NON_TEST_DB"

#: The tables doc 03 §9 step 2 creates. Their absence means migrations have not run.
TENANCY_TABLES = ("organizations", "branches", "users", "memberships")

#: The tables doc 03 §9 steps 3-9 add (P1-T4 round 1, revisions 0003-0009).
#: ``question_embeddings`` is deliberately absent — step 7 is gated on ADR-007 and
#: revision 0007 is a documented no-op.
SCHEMA_TABLES = (
    "boards",
    "classes",
    "subjects",
    "chapters",
    "learning_outcomes",
    "curriculum_versions",
    "programs",
    "program_subjects",
    "program_teachers",
    "question_bank",
    "textbooks",
    "textbook_assets",
    "templates",
)

#: The tables doc 03 §9 steps 10-17 add (P1-T4 round 2, revisions 0010-0017). Revisions
#: 0018 and 0019 create no tables: 0018 adds a column, four indexes and one foreign key,
#: and 0019 issues no DDL at all.
OPERATIONS_TABLES = (
    "generated_papers",
    "paper_versions",
    "paper_editor_drafts",
    "approvals",
    "subscriptions",
    "usage",
    "audit_logs",
    "analytics_daily",
    "prompt_versions",
    "notifications",
    "notification_event_receipts",
    "notification_digests",
)

#: Everything a database test may touch. Checked as one set so that running the suite
#: against a database migrated only part of the way up the chain fails with an
#: actionable message instead of ``relation "question_bank" does not exist``.
#:
#: Head also includes P6-T5's notifications-owned inbox and digest bucket tables.
REQUIRED_TABLES = TENANCY_TABLES + SCHEMA_TABLES + OPERATIONS_TABLES


def _resolve_test_database_url() -> str:
    """The DSN database tests run against, with a guard on the database name.

    The guard exists because these tests INSERT, UPDATE and (in one place, to verify
    an ``ON DELETE`` rule) DELETE. Everything happens inside a transaction that is
    rolled back, but "it rolls back" is not a defence worth betting a development
    database on, and a mistyped DSN is a cheap mistake to make.
    """

    explicit_url = os.environ.get("TEST_DATABASE_URL")
    if not explicit_url and not _LOCAL_POSTGRES_PASSWORD:
        pytest.fail(
            "local database credentials are not configured. Run "
            "scripts/verify-p1-t10.ps1 once, or copy infra/.env.example to "
            "infra/.env and replace every placeholder; alternatively set an explicit "
            "TEST_DATABASE_URL for a throwaway *_test database.",
            pytrace=False,
        )

    url = explicit_url or LOCAL_TEST_DSN
    database = make_url(url).database or ""

    if not database.endswith("_test") and not os.environ.get(ALLOW_NON_TEST_DB_ENV):
        # The database *name* is safe to print; the DSN is not, so it is never echoed.
        pytest.fail(
            f"refusing to run database tests against database {database!r}: the name "
            "does not end in '_test'. Point TEST_DATABASE_URL at a throwaway database "
            f"(papergen_test), or set {ALLOW_NON_TEST_DB_ENV}=1 to override this guard.",
            pytrace=False,
        )

    return url


async def _require_migrated_schema(connection) -> None:
    """Fail with an actionable message if the target database has no schema yet."""

    result = await connection.execute(
        text("SELECT tablename FROM pg_tables WHERE schemaname = 'public'")
    )
    present = {row[0] for row in result}
    missing = [table for table in REQUIRED_TABLES if table not in present]

    if missing:
        database = make_url(_resolve_test_database_url()).database
        pytest.fail(
            f"database {database!r} is reachable but not migrated - missing table(s): "
            f"{', '.join(missing)}. Run `alembic upgrade head` from backend/ first "
            "(infra/docker-compose.yml starts the local PostgreSQL).",
            pytrace=False,
        )


@pytest.fixture
async def db_engine():
    """A function-scoped :class:`AsyncEngine` pointed at the test database.

    Function-scoped on purpose. pytest-asyncio 1.x creates a fresh event loop per
    test by default, and an engine built on one loop cannot be used from another —
    a session-scoped engine is the single most common source of
    "attached to a different loop" failures in async test suites. Eight tests do not
    justify the risk of optimising this.

    Built through :func:`app.db.engine.create_engine` rather than
    ``create_async_engine`` directly, so tests exercise the same engine
    configuration the application and Alembic use.
    """

    engine = create_engine(url=_resolve_test_database_url())
    try:
        yield engine
    finally:
        await engine.dispose()


@pytest.fixture
async def db_connection(db_engine):
    """A connection with an open transaction that is always rolled back.

    Every test therefore starts from the same committed state and leaves nothing
    behind, regardless of ordering or of whether the test passed. Note the ordering
    inside: the transaction is opened *before* the schema probe, because a bare
    ``connect()`` starts a transaction implicitly on first execute and calling
    ``begin()`` after that raises.
    """

    async with db_engine.connect() as connection:
        transaction = await connection.begin()
        try:
            await _require_migrated_schema(connection)
            yield connection
        finally:
            await transaction.rollback()


@pytest.fixture
async def db_session(db_connection):
    """An :class:`AsyncSession` joined to :func:`db_connection`'s transaction.

    ``join_transaction_mode="create_savepoint"`` is what makes ``session.commit()``
    inside a test meaningful: the commit really flushes and really releases a
    savepoint, so server defaults, CHECK constraints and unique indexes all fire
    exactly as they would in production — and the enclosing rollback still undoes
    every trace of it. It also means a test can trigger an ``IntegrityError``, call
    ``rollback()``, and carry on, which a plain nested transaction would not allow.

    ``expire_on_commit=False`` is the usual async default: an expired attribute
    would lazy-load on plain attribute access, which raises ``MissingGreenlet``
    outside an awaitable context. Tests that need to prove a value came from the
    database call ``expunge_all()`` and re-select instead, which is a stronger
    guarantee anyway.
    """

    session = AsyncSession(
        bind=db_connection,
        expire_on_commit=False,
        join_transaction_mode="create_savepoint",
    )
    try:
        yield session
    finally:
        await session.close()


# ---------------------------------------------------------------------------
# Seed fixtures (P1-T4)
# ---------------------------------------------------------------------------
#
# Imported here rather than at the top of the file for the same reason every other
# `app.*` import in this module is below the os.environ block: app.core.config
# validates at import time, so the environment has to be in place first.
from app.models import (  # noqa: E402  (see above)
    Board,
    Chapter,
    Class,
    CurriculumVersion,
    Organization,
    Subject,
    User,
)


@dataclass(frozen=True)
class Tenant:
    """IDs of a committed organization and a user, for tests that need an owner."""

    org_id: uuid.UUID
    user_id: uuid.UUID


@dataclass(frozen=True)
class CurriculumChain:
    """IDs of one committed board → class → subject → chapter path.

    ``question_bank``, ``textbooks`` and ``textbook_assets`` all classify against
    this chain, so several tests need it. ``curriculum_version_id`` is included
    because it is the column revision 0004 wires a foreign key onto — a test that
    wants to prove that constraint exists needs both a valid id and the knowledge
    that an arbitrary UUID is not one.
    """

    board_id: uuid.UUID
    class_id: uuid.UUID
    subject_id: uuid.UUID
    chapter_id: uuid.UUID
    curriculum_version_id: uuid.UUID


@pytest.fixture
async def tenant(db_session) -> Tenant:
    """One organization and one user, committed inside the test's transaction."""

    org = Organization(name="Sunrise Coaching Centre")
    user = User(email="author@example.com", full_name="Question Author")
    db_session.add_all([org, user])
    await db_session.flush()
    ids = Tenant(org_id=org.id, user_id=user.id)
    await db_session.commit()
    return ids


@pytest.fixture
async def curriculum(db_session) -> CurriculumChain:
    """A complete curriculum path, version-scoped, committed.

    Deliberately built through the ORM rather than raw INSERTs: that way the fixture
    itself exercises the models' relationships and server defaults, and a mismatch
    between a model and its migration shows up here as a hard error in every test
    that depends on it rather than as a subtle wrong value later.
    """

    board = Board(code="CBSE-TEST", name="CBSE (test fixture)")
    db_session.add(board)
    await db_session.flush()

    version = CurriculumVersion(
        board_id=board.id,
        academic_year="2025-26",
        label="Test fixture syllabus",
        status="active",
    )
    school_class = Class(board_id=board.id, name="Class 10")
    db_session.add_all([version, school_class])
    await db_session.flush()

    subject = Subject(
        class_id=school_class.id,
        curriculum_version_id=version.id,
        name="Mathematics",
    )
    db_session.add(subject)
    await db_session.flush()

    chapter = Chapter(
        subject_id=subject.id,
        curriculum_version_id=version.id,
        seq=1,
        title="Real Numbers",
    )
    db_session.add(chapter)
    await db_session.flush()

    ids = CurriculumChain(
        board_id=board.id,
        class_id=school_class.id,
        subject_id=subject.id,
        chapter_id=chapter.id,
        curriculum_version_id=version.id,
    )
    await db_session.commit()
    return ids
