"""P1-T8 verification tests: tenancy CRUD, invitations, identity RLS, scope, audit.

The same seven proofs ``scripts/verify-p1-t8.ps1`` makes, expressed as pytest. This
file is not a duplicate of that script and does not replace it — the two differ in
exactly one way that matters, and it is the reason both exist.

The script's SQL proofs run through ``psql``, which connects as the DSN's user. The
DSN's user is ``postgres``, a SUPERUSER, and PostgreSQL exempts superusers from
row-level security **even on a table carrying FORCE ROW LEVEL SECURITY**. The script
compensates by issuing ``SET`` statements it writes itself, so it proves the
*policies* are correct while never once executing the application's own session
helpers. Its HTTP proofs go through the app (and therefore through ``app_user``), but
they can only observe what an endpoint returns.

Every test below connects as ``app_user`` — the ``NOBYPASSRLS`` role revision 0024
creates — and drives the real code path: ``get_identity_session`` /
``get_tenant_session``, the real repositories, the real service functions, and for
proofs 3-7 the real ASGI app with the real guard chain. An assertion here therefore
covers the policy *and* the code that depends on it.

How the real ``get_db`` ends up running as ``app_user``
------------------------------------------------------
Without a single ``dependency_overrides`` entry. ``app/db/session.py`` resolves its
factory through a module-level ``_session_factory``, and ``get_db`` calls
``get_tenant_session``, which calls ``get_session_factory()``. So
``init_session_factory(app_user_engine)`` retargets the *real* dependency at the
*real* non-superuser role, and every session opened by every service function
underneath follows. This is strictly stronger than overriding ``get_db`` would be —
an override replaces the thing under test — and it respects this task's constraint
that ``get_db``, ``get_current_user``, ``get_org_context`` and ``require_roles`` are
not to be modified. Nothing in this file touches any of them.

Two consequences worth naming, because they are load-bearing:

* ``ASGITransport`` does **not** run the app's lifespan. That is normally a nuisance;
  here it is the mechanism. The lifespan would call ``init_session_factory(engine)``
  with the *superuser* engine and silently undo the retarget, which would make every
  RLS assertion below vacuous. Because the lifespan never runs, the fixture's factory
  stands. ``tests/test_health.py`` relies on the same property for the opposite
  reason.
* Nothing overrides ``get_current_user``. Overriding it is the obvious way to get an
  authenticated client, and it would void proofs 4, 5 and 6 outright — those proofs
  are *about* what the guard chain derives from a verified token (the identity that
  reaches ``app.user_id``, the role re-derived on an org switch, the branch resolved
  from a membership). A stub principal would prove only that the stub was consulted.
  Tokens are therefore minted with the application's own
  ``security.create_access_token`` and presented as real ``Authorization`` headers.

Why these tests need Redis, and what happens without it
-------------------------------------------------------
``get_current_user`` calls ``is_session_revoked(sid)`` on *every* guarded request
(doc 07 §7.5 requires rejecting revoked tokens), and that is a Redis ``EXISTS``.
``RedisUnavailable`` is deliberately **not** converted into a 401 — it propagates as
a 500, which is correct behaviour and useless as a test result. So every HTTP test
here depends on the ``redis_ready`` fixture, which initialises the real client and
pings it. The DB-only proofs do not, and run without Redis.

DELIBERATE DEVIATION from ``tests/conftest.py``'s stated policy, flagged in the
P1-T8 report rather than resolved silently. That module says database tests "fail
with that instruction rather than skipping — a skipped database test looks like a
pass in CI, which is worse than no test at all", and this file agrees with it and
keeps it: a database that is reachable but unmigrated still reaches conftest's
``pytest.fail``. The deviation is narrower. An *unreachable or uninitialised* Redis
is skipped, not failed, because it is an environmental precondition on a different
service rather than a wrong schema, and because the P1-T8 brief asks for a suite that
degrades cleanly when the infrastructure is absent. The skip message names the fixture
and the URL scheme so a skipped run cannot be mistaken for a clean one, and the DB
proofs — which are the ones that cover RLS — still execute.

Fixture identifiers are deliberately disjoint from the script's
--------------------------------------------------------------
``scripts/verify-p1-t8.ps1`` seeds ``22222222-...`` UUIDs and ``p1t8*@papergen.test``
emails. This file uses ``33333333-...`` and ``p1t8py-*@papergen.test`` so that the two
harnesses cannot collide on ``uq_users_email`` or on a primary key, in either order,
and so that a crashed run of one does not wedge the other. The seed also *pre*-cleans
by name/email prefix before inserting, so a previous crashed run of this file is
recovered from automatically instead of failing on a unique index.
"""

from __future__ import annotations

import hashlib
import json as _json
import os
import uuid
from dataclasses import dataclass
from typing import Any, Optional

import pytest
from httpx import ASGITransport, AsyncClient
from sqlalchemy import text
from sqlalchemy.exc import DBAPIError, ProgrammingError
from sqlalchemy.ext.asyncio import AsyncSession, create_async_engine

from app.common.audit_log import ACTIONS
from app.common.deps import (
    DEBUG_ORG_ID_HEADER,
    DEBUG_ROLES_HEADER,
    DEBUG_USER_ID_HEADER,
    OrgContext,
)
from app.core.config import get_settings
from app.db.session import (
    get_identity_session,
    get_tenant_session,
    init_session_factory,
)
from app.infra.redis import close_redis, get_redis, init_redis
from app.main import app
from app.modules.auth import security
from app.modules.tenancy import schemas, service

pytestmark = pytest.mark.db


# ---------------------------------------------------------------------------
# The non-superuser role, and its local-only password
# ---------------------------------------------------------------------------
#: Created ``NOBYPASSRLS`` by migration 0024. Connecting as this role is the whole
#: point of this file; as ``postgres`` every RLS assertion below would pass vacuously.
APP_USER = "app_user"

def _require_app_user_password() -> str:
    """Return the credential owned by the central PostgreSQL provisioner."""

    password = os.environ.get("PAPERGEN_APP_DB_PASSWORD")
    if not password:
        pytest.fail(
            "PAPERGEN_APP_DB_PASSWORD is required; provision app_user through "
            "infra/postgres/init/02-provision-app-user.sh before running this test.",
            pytrace=False,
        )
    return password


# ---------------------------------------------------------------------------
# Fixture identities
# ---------------------------------------------------------------------------
# The role/branch asymmetry below is load-bearing, not decoration:
#
#   ADMIN_USER   coaching_admin in A (branch NULL), teacher in B (branch NULL)
#                -> two orgs, so proof 4 has something to list
#                -> different role in each, so proof 5 can show the role is
#                   re-derived on switch rather than carried across
#   TEACH_USER   teacher in A, pinned to BRANCH_A1
#                -> proof 6 has a non-null branch_id to resolve, and proof 3 has a
#                   caller who must be refused
#   OUTSIDE_USER coaching_admin in C only
#                -> org C is a real populated tenant the others cannot see, so a
#                   cross-tenant read returning zero rows means isolation and not
#                   an empty table
ORG_A = uuid.UUID("33333333-aaaa-4bbb-8ccc-00000000000a")
ORG_B = uuid.UUID("33333333-aaaa-4bbb-8ccc-00000000000b")
ORG_C = uuid.UUID("33333333-aaaa-4bbb-8ccc-00000000000c")
#: An org id that is not in ``organizations`` at all. Proof 5 switches to it and must
#: get the same 403 as a real-but-unjoined org — a 404 here would make the endpoint an
#: org-existence oracle for anyone with an account.
GHOST_ORG = uuid.UUID("33333333-aaaa-4bbb-8ccc-0000000000ff")

BRANCH_A1 = uuid.UUID("33333333-bbbb-4bbb-8ccc-000000000a01")
BRANCH_B1 = uuid.UUID("33333333-bbbb-4bbb-8ccc-000000000b01")
BRANCH_C1 = uuid.UUID("33333333-bbbb-4bbb-8ccc-000000000c01")

ADMIN_USER = uuid.UUID("33333333-cccc-4bbb-8ccc-000000000001")
TEACH_USER = uuid.UUID("33333333-cccc-4bbb-8ccc-000000000002")
OUTSIDE_USER = uuid.UUID("33333333-cccc-4bbb-8ccc-000000000003")

MEM_ADMIN_A = uuid.UUID("33333333-dddd-4bbb-8ccc-000000000001")
MEM_ADMIN_B = uuid.UUID("33333333-dddd-4bbb-8ccc-000000000002")
MEM_TEACH_A = uuid.UUID("33333333-dddd-4bbb-8ccc-000000000003")
MEM_OUTSIDE_C = uuid.UUID("33333333-dddd-4bbb-8ccc-000000000004")

ADMIN_EMAIL = "p1t8py-admin@papergen.test"
TEACH_EMAIL = "p1t8py-teacher@papergen.test"
OUTSIDE_EMAIL = "p1t8py-outside@papergen.test"
INVITEE_EMAIL = "p1t8py-invitee@papergen.test"
#: The "already registered, not yet a member" invitation case, and it has to be this
#: user rather than the more obvious ``TEACH_EMAIL``. ``accept_invitation`` *refuses*
#: an invitation whose invitee is already an **active** member (service.py L1123-1136:
#: re-applying a role from an invite would be a role change disguised as an
#: invitation), and ``TEACH_USER`` is active in org A. ``OUTSIDE_USER`` has a user row
#: but no membership in A, so inviting them exercises the intended path: a
#: ``status='invited'`` membership at invite time, activated to ``'active'`` on accept,
#: audited as ``membership.activated`` rather than ``membership.created``.
EXISTING_INVITEE_EMAIL = OUTSIDE_EMAIL

#: Every organization this file creates carries this prefix in ``name``, and every user
#: this ADDRESS prefix in ``email``. Teardown deletes by these two patterns, which is
#: what lets it clean up rows the *tests* created (an org from ``POST /orgs``, a user
#: from an invitation acceptance) without the fixture having to track their ids.
ORG_NAME_PREFIX = "P1T8PY"
USER_EMAIL_PREFIX = "p1t8py-"

#: Argon2id at the configured cost is ~250ms per hash. The seeded users never log in
#: with a password here (tokens are minted directly), so their ``password_hash`` only
#: has to be NOT NULL and well-formed enough to store. This is a real Argon2id digest
#: of a throwaway string, generated once at import rather than per user.
_SEED_PASSWORD = "p1t8py-seed-password-not-a-secret"

#: Long enough to satisfy ``InvitationAcceptIn.password``'s ``min_length=12``.
ACCEPT_PASSWORD = "p1t8py-accept-password-9182"


@dataclass(frozen=True)
class Seed:
    """Marker that the committed fixture rows exist. Ids are module constants."""

    org_a: uuid.UUID = ORG_A
    org_b: uuid.UUID = ORG_B
    org_c: uuid.UUID = ORG_C


# ---------------------------------------------------------------------------
# Teardown / pre-clean, shared by both ends of the seed fixture
# ---------------------------------------------------------------------------
#: FK order, and it matters. ``audit_logs`` references both ``organizations`` and
#: ``users``; ``invitations`` references ``organizations``, ``branches`` and ``users``
#: (``invited_by``, ``accepted_user_id``); ``memberships`` references all three.
#: Deleting ``organizations`` first would fail on every one of those.
#:
#: These are hard DELETEs, not the soft deletes ADR-004 mandates for application code.
#: That is correct for a fixture and only possible here: this runs as the DSN's
#: superuser, and revision 0024 never granted DELETE to ``app_user`` on anything —
#: which is itself asserted by proof 4c.
_CLEANUP_SQL = (
    # Rows whose org is one of ours...
    """
    DELETE FROM audit_logs
     WHERE org_id IN (SELECT id FROM organizations WHERE name LIKE :org_like)
    """,
    # ...and rows attributed to one of our users but written against some other org
    # (there should be none; if a bug ever produces one, this stops it accumulating).
    """
    DELETE FROM audit_logs
     WHERE actor_id IN (SELECT id FROM users WHERE email LIKE :user_like)
    """,
    """
    DELETE FROM invitations
     WHERE org_id IN (SELECT id FROM organizations WHERE name LIKE :org_like)
        OR email LIKE :user_like
        OR invited_by IN (SELECT id FROM users WHERE email LIKE :user_like)
        OR accepted_user_id IN (SELECT id FROM users WHERE email LIKE :user_like)
    """,
    """
    DELETE FROM memberships
     WHERE org_id IN (SELECT id FROM organizations WHERE name LIKE :org_like)
        OR user_id IN (SELECT id FROM users WHERE email LIKE :user_like)
    """,
    """
    DELETE FROM branches
     WHERE org_id IN (SELECT id FROM organizations WHERE name LIKE :org_like)
    """,
    "DELETE FROM organizations WHERE name LIKE :org_like",
    "DELETE FROM users WHERE email LIKE :user_like",
)


async def _purge(engine) -> None:
    """Remove every row this file could have created. Idempotent, hence reusable."""
    params = {"org_like": ORG_NAME_PREFIX + "%", "user_like": USER_EMAIL_PREFIX + "%"}
    async with engine.begin() as conn:
        for statement in _CLEANUP_SQL:
            await conn.execute(text(statement), params)


async def _require_p1_t8_schema(engine) -> None:
    """Fail with the command to run, rather than an ``UndefinedTableError`` mid-DELETE.

    ``conftest._require_migrated_schema`` cannot cover this. Its ``REQUIRED_TABLES``
    tuple predates ``invitations``, and this file deliberately bypasses
    ``db_connection`` — the seed rows have to be *committed* to be visible to the
    ``app_user`` engine and to the connection the app checks out for itself, so the
    rollback-isolated fixture is unusable here and its schema probe never runs.

    Run 1 of this suite produced 43 identical ``ERROR at setup`` entries reading
    ``relation "invitations" does not exist``. The cause was not this file: the target
    database (``papergen_test``, per ``conftest.LOCAL_TEST_DSN``) was last migrated by
    ``scripts/verify-p1-t5.ps1``, whose head was 0025, and ``invitations`` is created by
    revision 0026. ``verify-p1-t8.ps1`` migrates only the *dev* database, so running it
    does not fix this. Two revisions missing produced 43 tracebacks pointing at a
    ``DELETE`` statement, which is the least informative place the failure could have
    surfaced; this probe moves it to the front with the remedy attached.
    """
    async with engine.connect() as conn:
        result = await conn.execute(
            text(
                "SELECT to_regclass('public.invitations') IS NOT NULL, "
                "       to_regclass('public.alembic_version') IS NOT NULL"
            )
        )
        has_invitations, has_alembic = result.one()

        revision = "<no alembic_version table>"
        if has_alembic:
            revision = (
                await conn.execute(text("SELECT max(version_num) FROM alembic_version"))
            ).scalar() or "<empty>"

    if not has_invitations:
        # The database *name* is printable; the DSN is not, per conftest's convention.
        database = engine.url.database
        pytest.fail(
            f"database {database!r} is at revision {revision}, which is behind P1-T8: "
            "relation 'invitations' does not exist (created by revision 0026), so no "
            "test in this file can run. Revision 0027 — memberships_self_select, which "
            "proofs 4 and 5 depend on — is missing too.\n\n"
            "Bring the TEST database to head (verify-p1-t8.ps1 migrates only the dev "
            "database, so it will not do this for you):\n"
            "    cd backend\n"
            "    python -m alembic -x db_url=<the papergen_test DSN> upgrade head\n\n"
            "The DSN is TEST_DATABASE_URL if set, otherwise conftest.LOCAL_TEST_DSN.",
            pytrace=False,
        )


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------
@pytest.fixture
def settings():
    """The app's settings object, with the test environment already in place.

    ``conftest.py`` sets ``ENVIRONMENT=test`` before any ``app.*`` import, which is
    what puts the app in ``STUB_AUTH_ENVIRONMENTS`` and therefore mounts
    ``app/common/diagnostics.py``'s routers — proof 6 needs ``GET /_diag/context``,
    the only route in the application that depends on ``get_scope``.
    """
    return get_settings()


@pytest.fixture
async def app_user_engine(db_engine):
    """An engine connected as ``app_user``, the role that RLS actually applies to.

    Built with ``create_async_engine`` rather than ``app.db.engine.create_engine``
    for the reason ``tests/test_p1_t5_rls_and_repository.py`` records: that helper
    selects ``NullPool`` for a transaction-pooled DSN, and a pool that never reuses a
    connection cannot demonstrate that ``set_config(..., true)`` reverts between
    transactions. ``pool_size=1, max_overflow=0`` forces consecutive checkouts onto
    the same physical backend so that the GUC-leak assertion in proof 4a is testing
    something rather than getting lucky.

    The URL is derived from ``db_engine.url``, so conftest's guard that refuses any
    database not named ``*_test`` still governs what this connects to.
    """
    # Role creation/password rotation is cluster-scoped setup owned by
    # infra/postgres/init/02-provision-app-user.sh, never by a test fixture.
    app_user_password = _require_app_user_password()
    url = db_engine.url.set(username=APP_USER, password=app_user_password)
    engine = create_async_engine(url, pool_size=1, max_overflow=0, future=True)
    try:
        yield engine
    finally:
        await engine.dispose()


@pytest.fixture
async def app_user_factory(app_user_engine):
    """Retarget the application's own module-level session factory at ``app_user``.

    This one line is what makes the rest of the file meaningful: after it, the real
    ``get_db``, the real ``get_tenant_session`` and the real ``get_identity_session``
    all open connections as a ``NOBYPASSRLS`` role, with no dependency override and
    no modification to any guard.

    The previous factory is restored on teardown. ``init_session_factory`` writes a
    module global, and leaving it pointing at an engine this fixture is about to
    ``dispose()`` would hand the next test in the session a factory whose pool is
    closed — a failure that would surface far from its cause.
    """
    import app.db.session as session_module

    previous = session_module._session_factory
    factory = init_session_factory(app_user_engine)
    try:
        yield factory
    finally:
        session_module._session_factory = previous


@pytest.fixture
async def seed(db_engine, app_user_factory) -> Seed:
    """Three orgs, three branches, three users and four memberships — COMMITTED.

    Committed rather than created inside conftest's enclosing transaction, because
    every assertion below reads them over a *second, independent* connection (the
    ``app_user`` engine, and for the HTTP tests a connection the app checks out
    itself). An uncommitted row is invisible there, so the transaction-rollback
    isolation conftest normally provides cannot be used and cleanup has to be
    explicit.

    Depends on ``app_user_factory`` purely for ordering: the factory must already be
    retargeted before any test body runs, and expressing that as a fixture dependency
    is more reliable than relying on argument order at each call site.

    Seeds through raw SQL as the superuser rather than through the ORM or the service
    layer, deliberately. A fixture built out of the code under test cannot be used to
    prove that code correct — if ``create_organization`` were used to seed, proof 1
    would be asserting a function against itself.
    """
    # Schema state before anything else: run 1 spent 144 seconds producing 43 copies
    # of the same missing-relation traceback from the DELETE below, which is a worse
    # error message than the one _require_p1_t8_schema raises.
    await _require_p1_t8_schema(db_engine)

    # Recover from a previous crashed run before inserting: these rows are committed,
    # so a run that died between the insert and the teardown leaves residue that would
    # otherwise collide with uq_users_email on the next attempt.
    await _purge(db_engine)

    seed_hash = security.hash_password(_SEED_PASSWORD, get_settings())

    async with db_engine.begin() as conn:
        await conn.execute(
            text(
                "INSERT INTO organizations (id, name, org_type, region, status, "
                "settings) VALUES "
                "(:a, :a_name, 'coaching_center', 'ap-south-1', 'active', '{}'), "
                "(:b, :b_name, 'school',          'ap-south-1', 'active', '{}'), "
                "(:c, :c_name, 'tutor',           'ap-south-1', 'active', '{}')"
            ),
            {
                "a": str(ORG_A),
                "b": str(ORG_B),
                "c": str(ORG_C),
                "a_name": ORG_NAME_PREFIX + " Org Alpha",
                "b_name": ORG_NAME_PREFIX + " Org Beta",
                "c_name": ORG_NAME_PREFIX + " Org Gamma",
            },
        )
        await conn.execute(
            text(
                "INSERT INTO branches (id, org_id, name, region, status) VALUES "
                "(:a1, :a, 'Alpha Branch One', 'ap-south-1', 'active'), "
                "(:b1, :b, 'Beta Branch One',  'ap-south-1', 'active'), "
                "(:c1, :c, 'Gamma Branch One', 'ap-south-1', 'active')"
            ),
            {
                "a1": str(BRANCH_A1),
                "b1": str(BRANCH_B1),
                "c1": str(BRANCH_C1),
                "a": str(ORG_A),
                "b": str(ORG_B),
                "c": str(ORG_C),
            },
        )
        await conn.execute(
            text(
                "INSERT INTO users (id, email, password_hash, full_name, status) "
                "VALUES "
                "(:admin, :admin_email, :h, 'P1T8PY Admin',   'active'), "
                "(:teach, :teach_email, :h, 'P1T8PY Teacher', 'active'), "
                "(:out,   :out_email,   :h, 'P1T8PY Outside', 'active')"
            ),
            {
                "admin": str(ADMIN_USER),
                "teach": str(TEACH_USER),
                "out": str(OUTSIDE_USER),
                "admin_email": ADMIN_EMAIL,
                "teach_email": TEACH_EMAIL,
                "out_email": OUTSIDE_EMAIL,
                "h": seed_hash,
            },
        )
        await conn.execute(
            text(
                "INSERT INTO memberships "
                "(id, user_id, org_id, branch_id, role, status) VALUES "
                "(:m1, :admin, :a, NULL,  'coaching_admin', 'active'), "
                "(:m2, :admin, :b, NULL,  'teacher',        'active'), "
                "(:m3, :teach, :a, :a1,   'teacher',        'active'), "
                "(:m4, :out,   :c, :c1,   'coaching_admin', 'active')"
            ),
            {
                "m1": str(MEM_ADMIN_A),
                "m2": str(MEM_ADMIN_B),
                "m3": str(MEM_TEACH_A),
                "m4": str(MEM_OUTSIDE_C),
                "admin": str(ADMIN_USER),
                "teach": str(TEACH_USER),
                "out": str(OUTSIDE_USER),
                "a": str(ORG_A),
                "b": str(ORG_B),
                "c": str(ORG_C),
                "a1": str(BRANCH_A1),
                "c1": str(BRANCH_C1),
            },
        )

    try:
        yield Seed()
    finally:
        # Unconditional. A failing test that skipped cleanup would leave committed
        # rows behind and the *next* run would fail on a unique index instead of on
        # the real defect, which is how one broken test becomes a broken suite.
        await _purge(db_engine)


@pytest.fixture
async def redis_ready(settings):
    """Initialise the real Redis client, or skip with the *reason*.

    Required by every HTTP test here and by proof 5, for two different reasons:
    ``get_current_user`` performs ``is_session_revoked(sid)`` on every guarded
    request, and ``switch_active_org`` calls ``tokens.retarget_family``. A missing
    Redis is a 500 in both cases — correctly, since doc 07 §4.1 makes Redis a
    correctness dependency of authentication rather than a cache — and a 500 is not a
    useful test outcome.

    See the module docstring for why this skips where conftest would fail.

    Pings through ``get_redis()`` rather than ``ping_redis()`` deliberately. That helper
    catches every transport exception and returns ``False`` by design — it is written for
    the PowerShell harness, which wants only a boolean — so routing through it made the
    ``except`` branch here unreachable for connection *and* authentication failures and
    reduced every one of them to "did not answer PING". That cost a full verification
    round: the container was reporting healthy and the real error was ``WRONGPASS``,
    because ``backend/.env`` still carried a placeholder Redis password while
    ``infra/docker-compose.yml`` used a different ``REDIS_PASSWORD`` from the ignored
    local infra environment. A skip message that cannot distinguish
    "not running" from "wrong password" sends the reader to fix the wrong thing.
    """
    try:
        init_redis(settings)
        alive = bool(await get_redis().ping())
        failure = None
    except Exception as exc:  # noqa: BLE001 - any transport failure means "not ready"
        alive = False
        failure = f"{type(exc).__name__}: {exc}"

    if not alive:
        # Report where it pointed, never the credential — and scrub it out of the driver
        # exception too, since a URL-parsing error can echo back the string it was given.
        scheme, _, netloc = settings.REDIS_URL.partition("://")
        userinfo = netloc.partition("@")[0] if "@" in netloc else ""
        secret = userinfo.partition(":")[2]
        if secret and failure:
            failure = failure.replace(secret, "***")
        location = netloc.rsplit("@", 1)[-1]

        await close_redis()
        pytest.skip(
            "Redis did not answer an authenticated PING, so the guard chain's "
            "revocation check cannot run.\n"
            f"  REDIS_URL: {scheme}://{'<credentials>@' if userinfo else ''}{location}\n"
            f"  failure:   {failure or 'PING returned a falsy reply'}\n"
            "If that is WRONGPASS/AuthenticationError, the server is running fine and "
            "REDIS_URL's password simply does not match infra/.env's REDIS_PASSWORD. "
            "`docker compose ps` "
            "reports the container healthy either way, because its healthcheck "
            "authenticates from inside the container. If it is a ConnectionError, start "
            "it: docker compose -f infra/docker-compose.yml up -d --wait. The "
            "database-only proofs in this file do not need Redis and still ran."
        )

    try:
        yield
    finally:
        # Resets the module global to None, so a later test cannot accidentally reuse
        # a client whose pool this fixture is done with.
        await close_redis()


@pytest.fixture
async def client(redis_ready, app_user_factory):
    """An HTTP client over the real ASGI app.

    ``ASGITransport`` does not run the lifespan, which is what preserves
    ``app_user_factory``'s retarget — see the module docstring. ``redis_ready`` is
    listed first so that an absent Redis skips before an engine is built.
    """
    async with AsyncClient(
        transport=ASGITransport(app=app), base_url="http://test"
    ) as http:
        yield http


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------
def bearer(
    settings,
    *,
    user_id: uuid.UUID,
    org_id: Optional[uuid.UUID],
    role: Optional[str],
    session_id: str = "p1t8py-sid",
) -> dict[str, str]:
    """An ``Authorization`` header carrying a real, signed access token.

    Minted with the application's own ``create_access_token``, so
    ``get_current_user``'s ``decode_access_token`` performs a genuine HMAC signature
    check against it. This is the deliberate alternative to overriding
    ``get_current_user``: the guard chain runs in full, and what it derives from the
    token is what the tests assert on.

    Note that ``role`` here is a *claim*, which is what ``get_org_context`` reads. The
    tests that care whether a role is legitimate (proofs 3 and 5) do not rely on this
    — they assert against the ``memberships`` row the service re-derives from.
    """
    token = security.create_access_token(
        user_id=user_id,
        active_org_id=org_id,
        role=role,
        session_id=session_id,
        settings=settings,
    )
    return {"Authorization": "Bearer " + token}


def ctx_for(
    *, user_id: uuid.UUID, org_id: uuid.UUID, role: str
) -> OrgContext:
    """An ``OrgContext`` for calling a service function directly.

    Constructed rather than obtained from ``get_org_context`` only where the test is
    about the *service's* own authorization rules (the self-modification refusal, the
    super_admin grant guard) rather than about the guard chain. Building it by hand is
    the stronger choice there: it hands the service the most privileged context a real
    caller could hold, so a refusal cannot be an artefact of a weak fixture.
    """
    return OrgContext(org_id=org_id, user_id=user_id, role=role, roles=(role,))


def envelope_data(response) -> Any:
    """``data`` out of doc 07 §22.3's success envelope, asserting the envelope itself."""
    body = response.json()
    assert body["success"] is True, body
    assert "requestId" in body["meta"], body
    return body["data"]


def error_of(response) -> dict[str, Any]:
    """``error`` out of the failure envelope, asserting the envelope itself.

    ``meta.requestId`` — not ``error.requestId``. The correlation id lives in ``meta``
    on both envelope shapes (``app/common/envelope.py``); asserting the wrong path here
    was a real defect in an earlier draft of the PowerShell harness.
    """
    body = response.json()
    assert body["success"] is False, body
    assert "requestId" in body["meta"], body
    return body["error"]


async def scalar(session: AsyncSession, sql: str, **params):
    """Run a scalar query, keeping the assertions readable."""
    result = await session.execute(text(sql), params or None)
    return result.scalar_one()


async def audit_rows(
    engine, org_id: uuid.UUID, action: Optional[str] = None
) -> list[dict[str, Any]]:
    """Read ``audit_logs`` for an org as the superuser, newest last.

    Read as the *superuser* on purpose, and this is the one place in the file where
    that is the right choice rather than a weakness. These assertions ask "was a
    durable row written, and does it name the right actor?" — a question about what
    exists in the table, not about who can see it. Reading it through RLS would
    conflate "no row was written" with "the row is invisible to me", and those are
    different failures. Proof 7's *isolation* assertion is the one that needs RLS, and
    it opens an ``app_user`` session of its own.
    """
    sql = (
        "SELECT id, org_id, actor_id, action, entity, entity_id, meta, ip, ts "
        "  FROM audit_logs WHERE org_id = :org_id"
    )
    params: dict[str, Any] = {"org_id": str(org_id)}
    if action is not None:
        sql += " AND action = :action"
        params["action"] = action
    sql += " ORDER BY id"

    async with engine.begin() as conn:
        result = await conn.execute(text(sql), params)
        rows = [dict(row._mapping) for row in result]

    # ``meta`` is JSONB and this is a raw ``text()`` query, so no SQLAlchemy type is
    # attached to the column and the value arrives however the driver chose to decode
    # it. SQLAlchemy's asyncpg dialect registers a jsonb codec that runs the JSON
    # deserializer, so in practice this is already a ``dict`` — but that is a dialect
    # implementation detail, and a driver or version that handed back the raw text
    # would turn every ``meta["..."]`` assertion below into a confusing ``TypeError``
    # that looks nothing like the audit defect it would be mistaken for. Normalising
    # here weakens nothing: the assertions are about the content, and a value that is
    # neither a mapping nor JSON text is left alone to fail on its own terms.
    for row in rows:
        if isinstance(row["meta"], (str, bytes)):
            row["meta"] = _json.loads(row["meta"])
    return rows


# ===========================================================================
# PROOF 1 — organizations and branches are tenant-scoped, and deletes are soft
# ===========================================================================
async def test_proof1_org_read_is_scoped_by_the_repository_not_by_rls(
    seed, app_user_factory
):
    """``organizations`` is RLS-exempt, so the *application* predicate is the only guard.

    Revision 0025 leaves ``organizations`` out of RLS deliberately — a tenant has to
    read its own org row and there is no ``org_id`` column for a policy to compare
    against. That makes this the one table in the schema where a missing predicate is
    a real cross-tenant read rather than something the database would catch, and it is
    why ``OrganizationRepository`` overrides ``_org_id_column`` to return
    ``Organization.id``.

    So this test is not redundant with the RLS tests below. It is the *only* thing
    standing between a caller in org A and org B's row.
    """
    from app.modules.tenancy.repository import OrganizationRepository

    async with get_tenant_session(ORG_A) as session:
        repo = OrganizationRepository(session, ORG_A)

        own = await repo.get_by_id(ORG_A)
        assert own is not None, "org A cannot read its own organization row"
        assert own.name == ORG_NAME_PREFIX + " Org Alpha"

        # Org B exists and is populated. A None here is isolation, not an empty table.
        assert await repo.get_by_id(ORG_B) is None, (
            "a caller scoped to org A read org B's organization row — the "
            "_org_id_column override is not being applied"
        )
        assert await repo.get_by_id(ORG_C) is None

    # Confirm the row really is there, so the assertion above cannot pass vacuously.
    async with get_tenant_session(ORG_B) as session:
        peer = await OrganizationRepository(session, ORG_B).get_by_id(ORG_B)
        assert peer is not None and peer.name == ORG_NAME_PREFIX + " Org Beta"


async def test_proof1_branch_reads_are_rls_scoped(seed, app_user_factory):
    """Three branches exist; a session in org A sees exactly one, as ``app_user``.

    This is RLS doing the work, not the repository: the assertions go through raw SQL
    with no ``org_id`` predicate of their own, so the only thing filtering them is the
    policy on ``branches``. Running as ``postgres`` this test would see all three and
    still "pass" nothing.
    """
    async with get_tenant_session(ORG_A) as session:
        assert await scalar(session, "SELECT count(*) FROM branches") == 1
        assert (
            await scalar(
                session, "SELECT count(*) FROM branches WHERE id = :b", b=str(BRANCH_B1)
            )
            == 0
        ), "org B's branch was visible from an org A session"
        assert await scalar(
            session, "SELECT current_setting('app.org_id', true)"
        ) == str(ORG_A)

    async with get_tenant_session(ORG_C) as session:
        assert await scalar(session, "SELECT count(*) FROM branches") == 1
        assert (
            await scalar(
                session, "SELECT id::text FROM branches",
            )
            == str(BRANCH_C1)
        )


async def test_proof1_branch_crud_lifecycle_and_soft_delete(
    seed, app_user_factory, db_engine
):
    """Create, update and delete a branch through the service layer as ``app_user``.

    The delete assertion is the interesting one: ADR-004 forbids hard deletes in
    application code and revision 0024 never granted ``app_user`` DELETE at all, so
    "deleted" must mean ``deleted_at IS NOT NULL`` with the row still present. A test
    that only checked the branch had become invisible would pass equally against a
    genuine ``DELETE`` — so it checks both halves: gone from the scoped read, still
    there when read as the superuser.
    """
    ctx = ctx_for(user_id=ADMIN_USER, org_id=ORG_A, role="coaching_admin")

    async with get_tenant_session(ORG_A) as session:
        created = await service.create_branch(
            session=session,
            ctx=ctx,
            org_id=ORG_A,
            payload=schemas.BranchCreateIn(name="P1T8PY New Branch"),
        )
    assert created.org_id == ORG_A
    assert created.row_version == 1
    branch_id = created.id

    async with get_tenant_session(ORG_A) as session:
        updated = await service.update_branch(
            session=session,
            ctx=ctx,
            branch_id=branch_id,
            payload=schemas.BranchUpdateIn(row_version=1, name="P1T8PY Renamed"),
        )
    assert updated.name == "P1T8PY Renamed"
    assert updated.row_version == 2, "optimistic locking did not bump row_version"

    # A stale rowVersion must be a 409, not a silent overwrite.
    from app.common.errors import ConflictError

    async with get_tenant_session(ORG_A) as session:
        with pytest.raises(ConflictError):
            await service.update_branch(
                session=session,
                ctx=ctx,
                branch_id=branch_id,
                payload=schemas.BranchUpdateIn(row_version=1, name="P1T8PY Stale"),
            )

    async with get_tenant_session(ORG_A) as session:
        await service.delete_branch(
            session=session, ctx=ctx, branch_id=branch_id, row_version=2
        )

    # Invisible to the application...
    async with get_tenant_session(ORG_A) as session:
        assert (
            await scalar(
                session,
                "SELECT count(*) FROM branches WHERE id = :b AND deleted_at IS NULL",
                b=str(branch_id),
            )
            == 0
        )
    # ...but the row is still on disk. This is what distinguishes a soft delete.
    async with db_engine.begin() as conn:
        row = (
            await conn.execute(
                text(
                    "SELECT deleted_at FROM branches WHERE id = :b"
                ),
                {"b": str(branch_id)},
            )
        ).first()
    assert row is not None, "the branch row was hard-deleted; ADR-004 requires an UPDATE"
    assert row[0] is not None, "delete_branch did not set deleted_at"


async def test_proof1_cross_org_branch_write_is_refused(seed, app_user_factory):
    """A caller in org A cannot update or delete org B's branch.

    ``NotFoundError`` rather than ``AuthorizationError`` is the correct answer here and
    is asserted as such: the row is invisible under RLS, so from the service's point of
    view it does not exist. Answering 403 would confirm that the id names something
    real in another tenant.
    """
    from app.common.errors import NotFoundError

    ctx = ctx_for(user_id=ADMIN_USER, org_id=ORG_A, role="coaching_admin")

    async with get_tenant_session(ORG_A) as session:
        with pytest.raises(NotFoundError):
            await service.update_branch(
                session=session,
                ctx=ctx,
                branch_id=BRANCH_B1,
                payload=schemas.BranchUpdateIn(row_version=1, name="P1T8PY Hijack"),
            )

    async with get_tenant_session(ORG_A) as session:
        with pytest.raises(NotFoundError):
            await service.delete_branch(
                session=session, ctx=ctx, branch_id=BRANCH_B1, row_version=1
            )

    # The target is untouched — the refusal was a refusal, not a failed write.
    async with get_tenant_session(ORG_B) as session:
        assert (
            await scalar(
                session,
                "SELECT name FROM branches WHERE id = :b",
                b=str(BRANCH_B1),
            )
            == "Beta Branch One"
        )


async def test_proof1_path_org_mismatch_is_refused(seed, app_user_factory):
    """``assert_path_org`` makes the URL's org decorative rather than authoritative.

    Worth its own test because the failure mode without it is not a leak but something
    arguably worse: ``get_db`` sets ``app.org_id`` from the *token*, so a request to
    ``/orgs/{other}/branches`` would quietly operate on the caller's **own** org while
    appearing to address another. A client could delete its own branch believing it had
    deleted a competitor's.
    """
    from app.common.errors import AuthorizationError

    ctx = ctx_for(user_id=ADMIN_USER, org_id=ORG_A, role="coaching_admin")

    async with get_tenant_session(ORG_A) as session:
        with pytest.raises(AuthorizationError):
            await service.create_branch(
                session=session,
                ctx=ctx,
                org_id=ORG_B,  # path says B, token says A
                payload=schemas.BranchCreateIn(name="P1T8PY Wrong Org"),
            )

    # Nothing was created in either org.
    async with get_tenant_session(ORG_B) as session:
        assert await scalar(session, "SELECT count(*) FROM branches") == 1
    async with get_tenant_session(ORG_A) as session:
        assert await scalar(session, "SELECT count(*) FROM branches") == 1


# ===========================================================================
# PROOF 2 — invitation create / list / revoke / accept
# ===========================================================================
async def test_proof2_invitation_token_shape_and_hash(seed, app_user_factory):
    """The token is ``<org_uuid>.<secret>``, and only its SHA-256 is stored.

    Each clause is a separate security property, so each is asserted separately:
    the org prefix must be the *inviting* org (it is what supplies ``app.org_id`` on
    the public accept route), the secret must carry real entropy, and the plaintext
    must appear nowhere in the database.
    """
    ctx = ctx_for(user_id=ADMIN_USER, org_id=ORG_A, role="coaching_admin")

    async with get_tenant_session(ORG_A) as session:
        created = await service.create_invitation(
            session=session,
            ctx=ctx,
            org_id=ORG_A,
            payload=schemas.InvitationCreateIn(email=INVITEE_EMAIL, role="teacher"),
        )

    token = created.token
    prefix, sep, secret = token.partition(".")
    assert sep == ".", "token is not in <org>.<secret> form"
    assert uuid.UUID(prefix) == ORG_A, "token prefix is not the inviting org"
    # secrets.token_urlsafe(32) -> 43 base64url characters -> 256 bits.
    assert len(secret) == 43, f"invitation secret is {len(secret)} chars, expected 43"

    expected_hash = hashlib.sha256(token.encode("utf-8")).hexdigest()

    async with get_tenant_session(ORG_A) as session:
        stored = await scalar(
            session,
            "SELECT token_hash FROM invitations WHERE id = :i",
            i=str(created.invitation.id),
        )
        assert stored == expected_hash
        # The plaintext is never persisted, in this column or any other.
        assert (
            await scalar(
                session,
                "SELECT count(*) FROM invitations WHERE token_hash = :t",
                t=token,
            )
            == 0
        ), "the plaintext token was stored somewhere it should not be"

    # And it is absent from the listing DTO, permanently — a listing that returned the
    # digest would let any admin who can read the list verify guesses offline.
    assert not hasattr(created.invitation, "token_hash")
    assert "tokenHash" not in created.invitation.model_dump(by_alias=True)


async def test_proof2_invitation_for_new_user_creates_account_and_membership(
    seed, app_user_factory, db_engine, settings
):
    """The full happy path for an address with no existing account."""
    ctx = ctx_for(user_id=ADMIN_USER, org_id=ORG_A, role="coaching_admin")

    async with get_tenant_session(ORG_A) as session:
        created = await service.create_invitation(
            session=session,
            ctx=ctx,
            org_id=ORG_A,
            payload=schemas.InvitationCreateIn(
                email=INVITEE_EMAIL, role="teacher", branch_id=BRANCH_A1
            ),
        )
    assert created.invitation.status == "pending"
    assert created.invitation.invited_by == ADMIN_USER

    # No user row yet, so no membership could have been written at invite time.
    async with get_tenant_session(ORG_A) as session:
        assert await scalar(session, "SELECT count(*) FROM memberships") == 2

    accepted = await service.accept_invitation(
        payload=schemas.InvitationAcceptIn(
            token=created.token,
            full_name="P1T8PY Invitee",
            password=ACCEPT_PASSWORD,
        ),
        settings=settings,
    )
    assert accepted.user_created is True
    assert accepted.org_id == ORG_A
    assert accepted.role == "teacher"
    assert accepted.branch_id == BRANCH_A1
    assert accepted.email == INVITEE_EMAIL

    async with get_tenant_session(ORG_A) as session:
        assert (
            await scalar(
                session,
                "SELECT status FROM invitations WHERE id = :i",
                i=str(created.invitation.id),
            )
            == "accepted"
        )
        assert (
            await scalar(
                session,
                "SELECT accepted_user_id::text FROM invitations WHERE id = :i",
                i=str(created.invitation.id),
            )
            == str(accepted.user_id)
        )
        assert (
            await scalar(
                session,
                "SELECT status FROM memberships WHERE user_id = :u",
                u=str(accepted.user_id),
            )
            == "active"
        )

    # The chosen password really is the account's credential, hashed with Argon2id.
    async with db_engine.begin() as conn:
        stored_hash = (
            await conn.execute(
                text("SELECT password_hash FROM users WHERE id = :u"),
                {"u": str(accepted.user_id)},
            )
        ).scalar_one()
    assert stored_hash.startswith("$argon2id$"), "password was not Argon2id-hashed"
    assert ACCEPT_PASSWORD not in stored_hash
    assert security.verify_password(ACCEPT_PASSWORD, stored_hash, settings) is True


async def test_proof2_invitation_for_existing_user_activates_invited_membership(
    seed, app_user_factory, settings
):
    """ADR-012's half of the model: an existing account gets ``status='invited'`` first.

    ``OUTSIDE_USER`` already has an account (and a membership in org C) but none in org
    A, so inviting them must write a membership immediately, in the state that grants
    no access, and acceptance must flip that same row rather than insert a second one —
    ``uq_memberships_user_id_org_id`` would reject the duplicate.
    """
    ctx = ctx_for(user_id=ADMIN_USER, org_id=ORG_A, role="coaching_admin")

    async with get_tenant_session(ORG_A) as session:
        created = await service.create_invitation(
            session=session,
            ctx=ctx,
            org_id=ORG_A,
            payload=schemas.InvitationCreateIn(
                email=EXISTING_INVITEE_EMAIL, role="teacher"
            ),
        )

    async with get_tenant_session(ORG_A) as session:
        assert (
            await scalar(
                session,
                "SELECT status FROM memberships WHERE user_id = :u",
                u=str(OUTSIDE_USER),
            )
            == "invited"
        ), "no invited membership row was written for an existing user"

    # 'invited' grants nothing: the identity policy requires status='active', so the
    # org is invisible to the invitee until they accept.
    orgs_before = await service.list_own_orgs(actor_user_id=OUTSIDE_USER)
    assert [o.org_id for o in orgs_before] == [ORG_C]

    accepted = await service.accept_invitation(
        payload=schemas.InvitationAcceptIn(
            token=created.token,
            full_name="Ignored For Existing Account",
            password=ACCEPT_PASSWORD,
        ),
        settings=settings,
    )
    assert accepted.user_created is False
    assert accepted.user_id == OUTSIDE_USER

    async with get_tenant_session(ORG_A) as session:
        assert (
            await scalar(
                session,
                "SELECT count(*) FROM memberships WHERE user_id = :u",
                u=str(OUTSIDE_USER),
            )
            == 1
        ), "acceptance inserted a second membership instead of activating the first"
        assert (
            await scalar(
                session,
                "SELECT status FROM memberships WHERE user_id = :u",
                u=str(OUTSIDE_USER),
            )
            == "active"
        )

    orgs_after = await service.list_own_orgs(actor_user_id=OUTSIDE_USER)
    assert sorted(str(o.org_id) for o in orgs_after) == sorted(
        [str(ORG_A), str(ORG_C)]
    )


async def test_proof2_accepting_does_not_touch_an_existing_password(
    seed, app_user_factory, db_engine, settings
):
    """An invitation to a registered address must never be an account takeover.

    ``full_name`` and ``password`` are accepted and ignored on this path. If the
    supplied password were applied, anyone able to invite an address could set that
    account's credential and then log in as them — so the stored hash is captured
    before and compared after.
    """
    async with db_engine.begin() as conn:
        before = (
            await conn.execute(
                text("SELECT password_hash, full_name FROM users WHERE id = :u"),
                {"u": str(OUTSIDE_USER)},
            )
        ).first()

    ctx = ctx_for(user_id=ADMIN_USER, org_id=ORG_A, role="coaching_admin")
    async with get_tenant_session(ORG_A) as session:
        created = await service.create_invitation(
            session=session,
            ctx=ctx,
            org_id=ORG_A,
            payload=schemas.InvitationCreateIn(
                email=EXISTING_INVITEE_EMAIL, role="teacher"
            ),
        )

    await service.accept_invitation(
        payload=schemas.InvitationAcceptIn(
            token=created.token,
            full_name="Attacker Chosen Name",
            password="attacker-chosen-password-1",
        ),
        settings=settings,
    )

    async with db_engine.begin() as conn:
        after = (
            await conn.execute(
                text("SELECT password_hash, full_name FROM users WHERE id = :u"),
                {"u": str(OUTSIDE_USER)},
            )
        ).first()

    assert after[0] == before[0], "accepting an invitation overwrote an existing password"
    assert after[1] == before[1], "accepting an invitation overwrote an existing name"


async def test_proof2_revoked_invitation_cannot_be_accepted(
    seed, app_user_factory, settings
):
    """Revocation is a status transition, and it must actually stop acceptance."""
    from app.common.errors import BusinessRuleError

    ctx = ctx_for(user_id=ADMIN_USER, org_id=ORG_A, role="coaching_admin")

    async with get_tenant_session(ORG_A) as session:
        created = await service.create_invitation(
            session=session,
            ctx=ctx,
            org_id=ORG_A,
            payload=schemas.InvitationCreateIn(email=INVITEE_EMAIL, role="teacher"),
        )

    async with get_tenant_session(ORG_A) as session:
        revoked = await service.revoke_invitation(
            session=session, ctx=ctx, invitation_id=created.invitation.id
        )
    assert revoked.status == "revoked"

    with pytest.raises(BusinessRuleError) as exc:
        await service.accept_invitation(
            payload=schemas.InvitationAcceptIn(
                token=created.token,
                full_name="P1T8PY Invitee",
                password=ACCEPT_PASSWORD,
            ),
            settings=settings,
        )
    # One message for every failure reason — see _GENERIC_INVITATION_FAILURE. A caller
    # who could tell "revoked" from "no such token" has an oracle for guessing tokens.
    assert "invalid or has expired" in str(exc.value)

    # And no account was created as a side effect.
    async with get_tenant_session(ORG_A) as session:
        assert (
            await scalar(
                session,
                "SELECT count(*) FROM users WHERE email = :e",
                e=INVITEE_EMAIL,
            )
            == 0
        )


async def test_proof2_listing_is_tenant_scoped(seed, app_user_factory):
    """An invitation created in org A is invisible to org B, and vice versa."""
    ctx_a = ctx_for(user_id=ADMIN_USER, org_id=ORG_A, role="coaching_admin")

    async with get_tenant_session(ORG_A) as session:
        await service.create_invitation(
            session=session,
            ctx=ctx_a,
            org_id=ORG_A,
            payload=schemas.InvitationCreateIn(email=INVITEE_EMAIL, role="teacher"),
        )

    async with get_tenant_session(ORG_A) as session:
        items, _ = await service.list_invitations(
            session=session, ctx=ctx_a, org_id=ORG_A, status=None, limit=50, offset=0
        )
    assert [i.email for i in items] == [INVITEE_EMAIL]

    ctx_b = ctx_for(user_id=ADMIN_USER, org_id=ORG_B, role="coaching_admin")
    async with get_tenant_session(ORG_B) as session:
        items_b, _ = await service.list_invitations(
            session=session, ctx=ctx_b, org_id=ORG_B, status=None, limit=50, offset=0
        )
    assert items_b == [], "org B saw org A's invitation"


async def test_proof2_rewriting_the_token_org_prefix_does_not_help(
    seed, app_user_factory, settings
):
    """The org prefix selects a tenant; it does not authorise anything.

    This is the test that justifies putting a tenant id inside a credential — normally
    a bug. A caller who swaps the prefix for another org scopes the hash lookup to a
    tenant where their digest does not exist, and gets the same generic failure as
    someone who invented the token outright.
    """
    from app.common.errors import BusinessRuleError

    ctx = ctx_for(user_id=ADMIN_USER, org_id=ORG_A, role="coaching_admin")
    async with get_tenant_session(ORG_A) as session:
        created = await service.create_invitation(
            session=session,
            ctx=ctx,
            org_id=ORG_A,
            payload=schemas.InvitationCreateIn(email=INVITEE_EMAIL, role="teacher"),
        )

    _, _, secret = created.token.partition(".")
    forged = str(ORG_C) + "." + secret

    with pytest.raises(BusinessRuleError) as exc:
        await service.accept_invitation(
            payload=schemas.InvitationAcceptIn(
                token=forged,
                full_name="P1T8PY Invitee",
                password=ACCEPT_PASSWORD,
            ),
            settings=settings,
        )
    assert "invalid or has expired" in str(exc.value)

    # No membership was minted in the org named by the forged prefix.
    async with get_tenant_session(ORG_C) as session:
        assert await scalar(session, "SELECT count(*) FROM memberships") == 1

    # The real invitation is still pending and still usable — the forgery did not
    # consume it.
    async with get_tenant_session(ORG_A) as session:
        assert (
            await scalar(
                session,
                "SELECT status FROM invitations WHERE id = :i",
                i=str(created.invitation.id),
            )
            == "pending"
        )


async def test_proof2_inviter_cannot_escalate_above_their_own_role(
    seed, app_user_factory
):
    """A ``coaching_admin`` inviting a ``super_admin`` is a two-step tenant takeover."""
    from app.common.errors import AuthorizationError

    ctx = ctx_for(user_id=ADMIN_USER, org_id=ORG_A, role="coaching_admin")

    async with get_tenant_session(ORG_A) as session:
        with pytest.raises(AuthorizationError):
            await service.create_invitation(
                session=session,
                ctx=ctx,
                org_id=ORG_A,
                payload=schemas.InvitationCreateIn(
                    email=INVITEE_EMAIL, role="super_admin"
                ),
            )

    async with get_tenant_session(ORG_A) as session:
        assert await scalar(session, "SELECT count(*) FROM invitations") == 0


# ===========================================================================
# PROOF 3 — membership role changes, and the refusals around them
# ===========================================================================
async def test_proof3_admin_changes_a_members_role_over_http(
    seed, client, settings, db_engine
):
    """``PATCH /api/v1/memberships/{id}`` through the real guard chain.

    Over HTTP rather than by calling the service, because this proof is partly about
    ``require_roles`` — and ``require_roles`` only exists on the route. The token is a
    real signed one, so ``get_current_user`` verifies a signature, ``get_org_context``
    derives the role from the verified claim, and the handler runs only if both pass.
    """
    response = await client.patch(
        f"/api/v1/memberships/{MEM_TEACH_A}",
        json={"rowVersion": 1, "role": "coaching_admin"},
        headers=bearer(
            settings, user_id=ADMIN_USER, org_id=ORG_A, role="coaching_admin"
        ),
    )
    assert response.status_code == 200, response.text
    data = envelope_data(response)
    assert data["role"] == "coaching_admin"
    assert data["rowVersion"] == 2
    assert data["userId"] == str(TEACH_USER)

    # The change is durable, not just reflected in the response body.
    async with get_tenant_session(ORG_A) as session:
        assert (
            await scalar(
                session,
                "SELECT role FROM memberships WHERE id = :m",
                m=str(MEM_TEACH_A),
            )
            == "coaching_admin"
        )

    # ...and the event name is §4.2's own ``membership.role_changed``, not the
    # generic ``membership.updated``, because the role actually changed.
    rows = await audit_rows(db_engine, ORG_A, ACTIONS.MEMBERSHIP_ROLE_CHANGED)
    assert len(rows) == 1
    assert rows[0]["actor_id"] == ADMIN_USER
    assert rows[0]["meta"]["before"]["role"] == "teacher"
    assert rows[0]["meta"]["after"]["role"] == "coaching_admin"


async def test_proof3_teacher_cannot_change_a_membership(seed, client, settings):
    """``require_roles`` refuses a ``teacher`` before the handler runs.

    The token here is *legitimately* a teacher's — this is not a forgery test. It is
    the ordinary authorization case, and the assertion that the target row is unchanged
    is what distinguishes "refused" from "attempted and failed".
    """
    response = await client.patch(
        f"/api/v1/memberships/{MEM_ADMIN_A}",
        json={"rowVersion": 1, "role": "teacher"},
        headers=bearer(settings, user_id=TEACH_USER, org_id=ORG_A, role="teacher"),
    )
    assert response.status_code == 403, response.text
    error = error_of(response)
    # Generic message: the caller learns they may not, not what exists.
    assert error["message"] == "You do not have access to this resource."

    async with get_tenant_session(ORG_A) as session:
        assert (
            await scalar(
                session,
                "SELECT role FROM memberships WHERE id = :m",
                m=str(MEM_ADMIN_A),
            )
            == "coaching_admin"
        )


async def test_proof3_a_forged_role_claim_does_not_survive_signature_check(
    seed, client, settings
):
    """A token whose ``role`` claim was edited is refused at the signature, not the role.

    This is the gap ``frontend/lib/mock-auth.ts`` has and the server must not: the
    claim set is only trustworthy because it is signed. Editing the payload of an
    otherwise valid token must produce a 401 (bad credential), *not* a 403 (valid
    credential, insufficient role) — a 403 would mean the signature was never checked.
    """
    import base64

    good = security.create_access_token(
        user_id=TEACH_USER,
        active_org_id=ORG_A,
        role="teacher",
        session_id="p1t8py-sid",
        settings=settings,
    )
    header_b64, payload_b64, signature_b64 = good.split(".")

    def _b64d(segment: str) -> bytes:
        return base64.urlsafe_b64decode(segment + "=" * (-len(segment) % 4))

    def _b64e(raw: bytes) -> str:
        return base64.urlsafe_b64encode(raw).rstrip(b"=").decode("ascii")

    payload = _json.loads(_b64d(payload_b64))
    assert payload["role"] == "teacher"
    payload["role"] = "super_admin"
    forged = ".".join(
        [header_b64, _b64e(_json.dumps(payload).encode("utf-8")), signature_b64]
    )

    response = await client.patch(
        f"/api/v1/memberships/{MEM_ADMIN_A}",
        json={"rowVersion": 1, "role": "teacher"},
        headers={"Authorization": "Bearer " + forged},
    )
    assert response.status_code == 401, (
        "a payload-edited token was not rejected at the signature — "
        f"got {response.status_code}: {response.text}"
    )

    async with get_tenant_session(ORG_A) as session:
        assert (
            await scalar(
                session,
                "SELECT role FROM memberships WHERE id = :m",
                m=str(MEM_ADMIN_A),
            )
            == "coaching_admin"
        )


async def test_proof3_no_self_modification(seed, app_user_factory):
    """An admin cannot edit their own membership, which blocks self-escalation.

    ``require_roles("coaching_admin", "super_admin")`` alone would permit
    ``coaching_admin -> super_admin`` on your own row. The context is built by hand
    here precisely so the refusal cannot be attributed to a weak fixture: this is the
    most privileged caller the guard chain could produce for org A.
    """
    from app.common.errors import AuthorizationError

    ctx = ctx_for(user_id=ADMIN_USER, org_id=ORG_A, role="coaching_admin")

    async with get_tenant_session(ORG_A) as session:
        with pytest.raises(AuthorizationError):
            await service.update_membership(
                session=session,
                ctx=ctx,
                membership_id=MEM_ADMIN_A,  # the caller's own row
                payload=schemas.MembershipUpdateIn(row_version=1, role="super_admin"),
            )

    async with get_tenant_session(ORG_A) as session:
        assert (
            await scalar(
                session,
                "SELECT role FROM memberships WHERE id = :m",
                m=str(MEM_ADMIN_A),
            )
            == "coaching_admin"
        )


async def test_proof3_only_super_admin_grants_super_admin(seed, app_user_factory):
    """Rule 1 stops you escalating yourself; this stops two admins escalating each other."""
    from app.common.errors import AuthorizationError

    ctx = ctx_for(user_id=ADMIN_USER, org_id=ORG_A, role="coaching_admin")

    async with get_tenant_session(ORG_A) as session:
        with pytest.raises(AuthorizationError):
            await service.update_membership(
                session=session,
                ctx=ctx,
                membership_id=MEM_TEACH_A,
                payload=schemas.MembershipUpdateIn(row_version=1, role="super_admin"),
            )

    async with get_tenant_session(ORG_A) as session:
        assert (
            await scalar(
                session,
                "SELECT role FROM memberships WHERE id = :m",
                m=str(MEM_TEACH_A),
            )
            == "teacher"
        )

    # A super_admin may. Asserted so the rule above reads as a role restriction rather
    # than as "this field can never be set".
    super_ctx = ctx_for(user_id=ADMIN_USER, org_id=ORG_A, role="super_admin")
    async with get_tenant_session(ORG_A) as session:
        updated = await service.update_membership(
            session=session,
            ctx=super_ctx,
            membership_id=MEM_TEACH_A,
            payload=schemas.MembershipUpdateIn(row_version=1, role="super_admin"),
        )
    assert updated.role == "super_admin"


async def test_proof3_cross_org_membership_is_invisible(seed, app_user_factory):
    """An admin in org A cannot reach a membership in org C."""
    from app.common.errors import NotFoundError

    ctx = ctx_for(user_id=ADMIN_USER, org_id=ORG_A, role="coaching_admin")

    async with get_tenant_session(ORG_A) as session:
        with pytest.raises(NotFoundError):
            await service.update_membership(
                session=session,
                ctx=ctx,
                membership_id=MEM_OUTSIDE_C,
                payload=schemas.MembershipUpdateIn(row_version=1, status="suspended"),
            )

    async with get_tenant_session(ORG_C) as session:
        assert (
            await scalar(
                session,
                "SELECT status FROM memberships WHERE id = :m",
                m=str(MEM_OUTSIDE_C),
            )
            == "active"
        )


# ===========================================================================
# PROOF 4 — the identity-scoped read: memberships_self_select
# ===========================================================================
async def test_proof4a_identity_read_fails_closed_with_no_app_user_id(
    seed, app_user_engine
):
    """(a) With ``app.user_id`` unset the policy admits **zero rows**, and does not raise.

    This is revision 0027's fail-closed property and the single most important
    assertion in the file. The policy compares ``user_id = NULLIF(current_setting(
    'app.user_id', true), '')::uuid``; with the GUC unset that is ``user_id = NULL``,
    which is ``NULL``, and a ``NULL`` predicate is false — so the query returns nothing.

    Both halves are asserted, because they fail differently and only one is safe:

    * **Zero rows.** Nine memberships-visible rows would mean the policy is not being
      applied at all (which is what running as ``postgres`` would show).
    * **No exception.** ``current_setting('app.user_id')`` *without* the ``true``
      missing-ok argument raises ``UndefinedObject`` instead of returning NULL, which
      would surface as a 500. That is fail-closed too, but it is a broken endpoint
      rather than an empty list, and it is the shape revision 0027 hardened away from.

    Deliberately opened with the raw engine rather than ``get_identity_session``, since
    that helper always sets the GUC — the scenario under test is the one where nothing
    set it, which is what a pooled connection handed to the wrong code path looks like.
    """
    async with app_user_engine.connect() as conn:
        current = (
            await conn.execute(text("SELECT current_setting('app.user_id', true)"))
        ).scalar_one()
        assert current in (None, ""), (
            "app.user_id leaked into a fresh connection; set_config(..., true) is "
            f"not transaction-local (saw {current!r})"
        )

        count = (
            await conn.execute(text("SELECT count(*) FROM memberships"))
        ).scalar_one()
        assert count == 0, (
            f"memberships returned {count} rows with no identity set — the RLS "
            "policy is not fail-closed (or this connection is not app_user)"
        )


async def test_proof4a_org_guc_does_not_leak_between_transactions(
    seed, app_user_engine
):
    """``set_config(..., true)`` reverts, so a pooled connection carries nothing forward.

    Meaningful only because ``app_user_engine`` is built with ``pool_size=1,
    max_overflow=0``: the second block below is guaranteed to be the *same* physical
    backend as the first. With ``NullPool`` this would pass by getting a new connection
    every time and prove nothing.

    The revert is the property under test. What the reverted value then *does* to a
    query is deliberately different per table, and this test asserts both behaviours
    rather than assuming one, because revision 0027 chose to apply its hardening to
    exactly one table and to leave the rest raising. An earlier version of this test
    asserted zero rows from ``branches`` and failed against a correct schema — the
    assertion contradicted the migration's own recorded design decision.
    """
    async with app_user_engine.begin() as conn:
        await conn.execute(
            text("SELECT set_config('app.org_id', :o, true)"), {"o": str(ORG_A)}
        )
        assert (
            await conn.execute(text("SELECT count(*) FROM branches"))
        ).scalar_one() == 1

    async with app_user_engine.begin() as conn:
        leaked = (
            await conn.execute(text("SELECT current_setting('app.org_id', true)"))
        ).scalar_one()
        assert leaked in (None, ""), f"app.org_id survived the transaction: {leaked!r}"

    # Hardened tables (0026 ``invitations``, 0027 ``memberships``) read the reverted
    # value through ``NULLIF(..., '')``, so the predicate is NULL and they fail closed
    # **quietly** — zero rows, no error.
    async with app_user_engine.begin() as conn:
        for table in ("memberships", "invitations"):
            count = (
                await conn.execute(text(f"SELECT count(*) FROM {table}"))
            ).scalar_one()
            assert count == 0, (
                f"{table} returned {count} rows with app.org_id reverted to '' — the "
                "NULLIF hardening from revision 0026/0027 is missing or was reverted"
            )

    # The sixteen unhardened tables from revision 0025 cast ``''`` straight to uuid and
    # therefore fail closed **loudly** — 22P02. Revision 0027's "Scope of the hardening"
    # keeps them that way on purpose: "a query that reaches generated_papers with no
    # tenant context is a guard-chain hole, and an error makes it a loud 500", where the
    # hardened form "would silently return an empty result set and look like 'no data'
    # instead of 'no scoping'". Pinning that down here means a future blanket-hardening
    # sweep has to come past this assertion and change it deliberately.
    #
    # Its own transaction: the raise aborts whichever transaction it lands in, so it
    # cannot share one with the quiet assertions above.
    with pytest.raises((ProgrammingError, DBAPIError)) as reverted_exc:
        async with app_user_engine.begin() as conn:
            await conn.execute(text("SELECT count(*) FROM branches"))
    assert 'invalid input syntax for type uuid: ""' in str(reverted_exc.value), (
        "branches did fail with app.org_id reverted, but not with the 22P02 cast error "
        f"revision 0027 documents: {reverted_exc.value!r}"
    )


async def test_proof4b_identity_read_returns_exactly_the_callers_rows(
    seed, app_user_factory
):
    """(b) Set ``app.user_id`` and the caller sees their own memberships — only those.

    ``ADMIN_USER`` is in two orgs, so a correct result is two rows and not one; the
    other two seeded memberships belong to other users and must not appear. Note that
    no ``user_id`` is passed to the repository at all — the filter is the policy.
    """
    orgs = await service.list_own_orgs(actor_user_id=ADMIN_USER)
    assert sorted(str(o.org_id) for o in orgs) == sorted([str(ORG_A), str(ORG_B)])

    by_org = {o.org_id: o for o in orgs}
    # The role differs per org, which is what makes proof 5's re-derivation meaningful.
    assert by_org[ORG_A].role == "coaching_admin"
    assert by_org[ORG_B].role == "teacher"
    assert by_org[ORG_A].branch_id is None
    assert by_org[ORG_A].org_name == ORG_NAME_PREFIX + " Org Alpha"
    assert all(o.membership_status == "active" for o in orgs)

    # A different caller gets a different, disjoint answer over the same code path.
    teacher_orgs = await service.list_own_orgs(actor_user_id=TEACH_USER)
    assert [str(o.org_id) for o in teacher_orgs] == [str(ORG_A)]
    assert teacher_orgs[0].branch_id == BRANCH_A1
    assert teacher_orgs[0].role == "teacher"

    outsider_orgs = await service.list_own_orgs(actor_user_id=OUTSIDE_USER)
    assert [str(o.org_id) for o in outsider_orgs] == [str(ORG_C)]


async def test_proof4b_identity_session_cannot_be_asked_about_someone_else(
    seed, app_user_factory
):
    """The identity read has no ``user_id`` parameter, so it cannot name another user.

    Asserted structurally rather than behaviourally: an endpoint that *accepts* a user
    id is an endpoint that can be asked about somebody else, so the guarantee is that
    the query never names one. This catches a future refactor that adds a convenience
    ``user_id=`` argument and reintroduces the hazard.
    """
    import inspect

    from app.modules.tenancy.repository import IdentityRepository

    for name in ("list_own_memberships", "get_own_membership_in"):
        params = set(inspect.signature(getattr(IdentityRepository, name)).parameters)
        assert "user_id" not in params, (
            f"IdentityRepository.{name} accepts a user_id — the identity read must "
            "derive the caller from app.user_id, never from an argument"
        )

    # And behaviourally: a session opened for one user cannot see another's rows even
    # with a hand-written query that asks for them by id.
    async with get_identity_session(TEACH_USER) as session:
        assert (
            await scalar(
                session,
                "SELECT count(*) FROM memberships WHERE user_id = :u",
                u=str(ADMIN_USER),
            )
            == 0
        ), "an identity session for one user could read another user's memberships"
        assert await scalar(session, "SELECT count(*) FROM memberships") == 1


async def test_proof4c_identity_session_is_read_only_for_memberships(
    seed, app_user_factory
):
    """(c) ``memberships_self_select`` is a SELECT policy — it grants no write path.

    A user who could UPDATE their own membership row could set ``role='super_admin'``
    directly and bypass every service-layer guard proof 3 asserts. Each statement is
    run in its own block because the first failure aborts the transaction.

    Both outcomes are accepted, and the distinction is worth naming: an UPDATE with no
    permissive policy for that command raises ``insufficient_privilege``, whereas an
    UPDATE whose *rows* are all filtered out simply affects zero rows. Either means no
    write happened; asserting only one would make this brittle against a future policy
    that expresses the same restriction the other way.
    """
    updated_rows: Optional[int] = None
    try:
        async with get_identity_session(ADMIN_USER) as session:
            result = await session.execute(
                text(
                    "UPDATE memberships SET role = 'super_admin' "
                    " WHERE user_id = :u"
                ),
                {"u": str(ADMIN_USER)},
            )
            updated_rows = result.rowcount
    except (ProgrammingError, DBAPIError):
        updated_rows = 0  # refused outright, which is the stronger outcome

    assert updated_rows == 0, (
        f"an identity session updated {updated_rows} membership rows — "
        "memberships_self_select must not grant UPDATE"
    )

    # The definitive check: read it back as the superuser, past RLS entirely.
    async with get_tenant_session(ORG_A) as session:
        assert (
            await scalar(
                session,
                "SELECT role FROM memberships WHERE id = :m",
                m=str(MEM_ADMIN_A),
            )
            == "coaching_admin"
        )

    # DELETE is not granted to app_user on any table (revision 0024).
    deleted_rows: Optional[int] = None
    try:
        async with get_identity_session(ADMIN_USER) as session:
            result = await session.execute(
                text("DELETE FROM memberships WHERE user_id = :u"),
                {"u": str(ADMIN_USER)},
            )
            deleted_rows = result.rowcount
    except (ProgrammingError, DBAPIError):
        deleted_rows = 0

    assert deleted_rows == 0, "app_user was able to DELETE a membership row"


async def test_proof4_http_get_orgs_returns_the_callers_own_orgs(
    seed, client, settings
):
    """The HTTP half: ``GET /api/v1/orgs`` under a real token, with no org context.

    This route is guarded by ``get_current_user`` **alone** — there is no
    ``get_org_context``, because the question "which orgs am I in?" is asked before an
    org is chosen. So the token deliberately carries ``org_id=None`` and ``role=None``,
    which is exactly what P1-T7's login issues to a multi-org user.
    """
    response = await client.get(
        "/api/v1/orgs",
        headers=bearer(settings, user_id=ADMIN_USER, org_id=None, role=None),
    )
    assert response.status_code == 200, response.text
    data = envelope_data(response)
    assert sorted(item["orgId"] for item in data) == sorted(
        [str(ORG_A), str(ORG_B)]
    )
    roles = {item["orgId"]: item["role"] for item in data}
    assert roles[str(ORG_A)] == "coaching_admin"
    assert roles[str(ORG_B)] == "teacher"

    # A different token, over the same route, gets only its own — org C is a real,
    # populated tenant that simply is not this caller's.
    other = await client.get(
        "/api/v1/orgs",
        headers=bearer(settings, user_id=TEACH_USER, org_id=None, role=None),
    )
    assert [item["orgId"] for item in envelope_data(other)] == [str(ORG_A)]


async def test_proof4_unauthenticated_get_orgs_is_401(seed, client):
    """No token, no identity, no rows — and the right status code for the reason."""
    response = await client.get("/api/v1/orgs")
    assert response.status_code == 401, response.text
    assert error_of(response)["message"]


# ===========================================================================
# PROOF 5 — POST /auth/switch-org
# ===========================================================================
async def test_proof5_switch_remints_token_with_the_target_orgs_role(
    seed, client, settings, db_engine
):
    """The role in the new token is re-derived from the target membership.

    ``ADMIN_USER`` is ``coaching_admin`` in A and ``teacher`` in B. Carrying the old
    claim forward would make them an admin of org B — a full privilege escalation
    reachable by anyone who administers any single org, including one they created
    themselves via ``POST /orgs``. So the assertion is on the *decoded* new token, not
    on the response body alone.
    """
    response = await client.post(
        "/api/v1/auth/switch-org",
        json={"orgId": str(ORG_B)},
        headers=bearer(
            settings,
            user_id=ADMIN_USER,
            org_id=ORG_A,
            role="coaching_admin",
            session_id="p1t8py-switch",
        ),
    )
    assert response.status_code == 200, response.text
    data = envelope_data(response)

    claims = security.decode_access_token(data["accessToken"], settings)
    assert claims.user_id == ADMIN_USER
    assert claims.active_org_id == ORG_B
    assert claims.role == "teacher", (
        f"switch-org carried the old role forward: token says {claims.role!r}, "
        "but the caller is only a teacher in org B"
    )
    # Same session id: a new one would orphan the refresh family, leaving a token that
    # logout and reuse-detection could never revoke.
    assert claims.session_id == "p1t8py-switch"
    assert data["activeOrg"]["role"] == "teacher"
    assert data["activeOrg"]["orgId"] == str(ORG_B)

    # The switch is durably attributed, in the *target* org.
    rows = await audit_rows(db_engine, ORG_B, ACTIONS.AUTH_ORG_SWITCHED)
    assert len(rows) == 1
    assert rows[0]["actor_id"] == ADMIN_USER
    assert rows[0]["meta"]["role"] == "teacher"
    assert rows[0]["meta"]["fromOrgId"] == str(ORG_A)
    assert rows[0]["meta"]["toOrgId"] == str(ORG_B)


async def test_proof5_switch_rejects_a_client_supplied_role(
    seed, client, settings, db_engine
):
    """The target role is server-derived and cannot be supplied by the caller."""
    response = await client.post(
        "/api/v1/auth/switch-org",
        json={"orgId": str(ORG_B), "role": "super_admin"},
        headers=bearer(
            settings,
            user_id=ADMIN_USER,
            org_id=None,
            role=None,
        ),
    )

    assert response.status_code == 422, response.text
    error = error_of(response)
    assert error["code"] == "VALIDATION_FAILED"
    assert any(detail.get("field") == "role" for detail in error["details"])
    assert "accessToken" not in response.text
    assert await audit_rows(db_engine, ORG_B, ACTIONS.AUTH_ORG_SWITCHED) == []


async def test_proof5_the_reminted_token_actually_carries_the_narrower_role(
    seed, client, settings
):
    """Using the post-switch token on an admin route in org B must fail.

    The claim assertion above proves the token *says* ``teacher``; this proves the
    guard chain acts on it. Without this, a bug that re-derived the role correctly but
    left some other privilege path open would still pass.
    """
    switch = await client.post(
        "/api/v1/auth/switch-org",
        json={"orgId": str(ORG_B)},
        headers=bearer(
            settings, user_id=ADMIN_USER, org_id=ORG_A, role="coaching_admin"
        ),
    )
    assert switch.status_code == 200, switch.text
    new_token = envelope_data(switch)["accessToken"]

    response = await client.get(
        f"/api/v1/orgs/{ORG_B}/memberships",
        headers={"Authorization": "Bearer " + new_token},
    )
    assert response.status_code == 403, (
        "the re-minted token still had admin access in the target org: "
        f"{response.status_code} {response.text}"
    )


async def test_proof5_switch_to_a_non_member_org_is_403_not_404(
    seed, client, settings, db_engine
):
    """A real org the caller is not in, and an org that does not exist, look identical.

    Both must be 403. A 404 for the nonexistent one would turn this endpoint into an
    org-existence oracle for any authenticated user.
    """
    headers = bearer(
        settings, user_id=ADMIN_USER, org_id=ORG_A, role="coaching_admin"
    )

    real_but_foreign = await client.post(
        "/api/v1/auth/switch-org", json={"orgId": str(ORG_C)}, headers=headers
    )
    assert real_but_foreign.status_code == 403, real_but_foreign.text

    nonexistent = await client.post(
        "/api/v1/auth/switch-org", json={"orgId": str(GHOST_ORG)}, headers=headers
    )
    assert nonexistent.status_code == 403, nonexistent.text

    # Indistinguishable to the caller — same status and same message.
    assert (
        error_of(real_but_foreign)["message"] == error_of(nonexistent)["message"]
    )

    # And no token was minted, and no audit row written in the org that refused.
    assert "accessToken" not in real_but_foreign.text
    assert await audit_rows(db_engine, ORG_C, ACTIONS.AUTH_ORG_SWITCHED) == []


async def test_proof5_switch_to_an_invited_but_not_active_membership_is_refused(
    seed, app_user_factory
):
    """``status='invited'`` grants no access, so it cannot be switched into.

    The invited row is written directly rather than through ``create_invitation``, to
    keep this test about the *switch* rule: it must reject any membership that is not
    ``active``, however that row came to exist.
    """
    from app.common.errors import AuthorizationError

    async with get_tenant_session(ORG_B) as session:
        await session.execute(
            text(
                "INSERT INTO memberships (id, user_id, org_id, role, status) "
                "VALUES (:m, :u, :o, 'teacher', 'invited')"
            ),
            {
                "m": str(uuid.uuid4()),
                "u": str(OUTSIDE_USER),
                "o": str(ORG_B),
            },
        )

    with pytest.raises(AuthorizationError):
        await service.authorize_org_switch(
            actor_user_id=OUTSIDE_USER, target_org_id=ORG_B
        )

    # It is also invisible to the org list, from the same policy.
    orgs = await service.list_own_orgs(actor_user_id=OUTSIDE_USER)
    assert [str(o.org_id) for o in orgs] == [str(ORG_C)]


# ===========================================================================
# PROOF 6 — get_scope resolves branch_id from the caller's membership
# ===========================================================================
async def test_proof6_resolve_branch_id_reads_the_membership(seed, app_user_factory):
    """Doc 07 §8.2: a non-null ``branch_id`` narrows; null means org-wide.

    Two ways of getting ``None`` are asserted separately because they are different
    facts that happen to share a return value: an org-wide member (membership exists,
    ``branch_id`` is NULL) and a non-member (no membership at all).
    """
    assert (
        await service.resolve_branch_id(org_id=ORG_A, user_id=TEACH_USER)
        == BRANCH_A1
    )
    assert await service.resolve_branch_id(org_id=ORG_A, user_id=ADMIN_USER) is None
    assert await service.resolve_branch_id(org_id=ORG_A, user_id=OUTSIDE_USER) is None
    # A branch pinned in one org does not follow the user into another.
    assert await service.resolve_branch_id(org_id=ORG_B, user_id=TEACH_USER) is None


async def test_proof6_get_scope_over_http_via_diag_context(seed, client, settings):
    """``GET /api/v1/_diag/context`` — the only route that depends on ``get_scope``.

    The branch-scoped caller and the org-wide caller are checked over the same route
    with different tokens, so the difference in the response is attributable to the
    membership rather than to the request.
    """
    scoped = await client.get(
        "/api/v1/_diag/context",
        headers=bearer(settings, user_id=TEACH_USER, org_id=ORG_A, role="teacher"),
    )
    assert scoped.status_code == 200, scoped.text
    data = envelope_data(scoped)
    assert data["userId"] == str(TEACH_USER)
    assert data["orgId"] == str(ORG_A)
    assert data["branchId"] == str(BRANCH_A1), (
        "get_scope did not resolve the teacher's branch from their membership"
    )
    assert data["isOrgWide"] is False

    wide = await client.get(
        "/api/v1/_diag/context",
        headers=bearer(
            settings, user_id=ADMIN_USER, org_id=ORG_A, role="coaching_admin"
        ),
    )
    assert wide.status_code == 200, wide.text
    wide_data = envelope_data(wide)
    assert wide_data["branchId"] is None
    assert wide_data["isOrgWide"] is True


async def test_proof6_scope_is_not_taken_from_a_request_header(seed, client, settings):
    """A caller cannot widen their own scope by asking for it.

    ``get_scope`` derives ``branch_id`` from a database read, not from the request, so
    a caller pinned to a branch stays pinned no matter what headers they send. This is
    the header-forgery case applied to *scope* rather than to identity — the same class
    of hazard proof 7 checks for ``actor_id``.
    """
    response = await client.get(
        "/api/v1/_diag/context",
        headers={
            **bearer(settings, user_id=TEACH_USER, org_id=ORG_A, role="teacher"),
            DEBUG_USER_ID_HEADER: str(ADMIN_USER),
            DEBUG_ORG_ID_HEADER: str(ORG_B),
            DEBUG_ROLES_HEADER: "super_admin",
            "X-Branch-Id": "",
        },
    )
    assert response.status_code == 200, response.text
    data = envelope_data(response)
    assert data["userId"] == str(TEACH_USER), "a debug header overrode the token's user"
    assert data["orgId"] == str(ORG_A), "a debug header overrode the token's org"
    assert data["role"] == "teacher", "a debug header overrode the token's role"
    assert data["branchId"] == str(BRANCH_A1), "scope was widened by a request header"
    assert data["isOrgWide"] is False


# ===========================================================================
# PROOF 7 — durable, attributed, append-only, tenant-isolated audit rows
# ===========================================================================
async def test_proof7_mutations_write_durable_audit_rows(
    seed, app_user_factory, db_engine
):
    """Every mutation lands a row in ``audit_logs``, committed with its own transaction.

    "Durable" is the load-bearing word and is why the read goes through a *separate*
    connection: a row staged with ``session.add`` and never committed would be visible
    to the transaction that made it and to nothing else. The existing
    ``middleware/audit.py`` writes no rows at all — it emits log lines — so this is the
    service-layer table P1-T8 adds, and it either committed or it did not.
    """
    ctx = ctx_for(user_id=ADMIN_USER, org_id=ORG_A, role="coaching_admin")

    async with get_tenant_session(ORG_A) as session:
        branch = await service.create_branch(
            session=session,
            ctx=ctx,
            org_id=ORG_A,
            payload=schemas.BranchCreateIn(name="P1T8PY Audited Branch"),
        )

    rows = await audit_rows(db_engine, ORG_A, ACTIONS.BRANCH_CREATED)
    assert len(rows) == 1, f"expected one branch.created row, got {len(rows)}"
    row = rows[0]
    assert row["org_id"] == ORG_A
    assert row["actor_id"] == ADMIN_USER
    assert row["action"] == "branch.created"
    assert row["entity"] == "branches"
    assert row["entity_id"] == branch.id
    assert row["ts"] is not None

    async with get_tenant_session(ORG_A) as session:
        await service.update_branch(
            session=session,
            ctx=ctx,
            branch_id=branch.id,
            payload=schemas.BranchUpdateIn(row_version=1, name="P1T8PY Audited II"),
        )
    async with get_tenant_session(ORG_A) as session:
        await service.delete_branch(
            session=session, ctx=ctx, branch_id=branch.id, row_version=2
        )

    actions = [r["action"] for r in await audit_rows(db_engine, ORG_A)]
    assert actions == ["branch.created", "branch.updated", "branch.deleted"], actions


async def test_proof7_the_audit_row_shares_the_mutation_transaction(
    seed, app_user_factory, db_engine
):
    """A rollback takes the audit row with it — the write is not autonomous.

    ``record_audit`` is a plain ``session.add`` on the caller's session, so the INSERT
    rides the caller's commit. This test is what makes that a *demonstrated* property
    rather than a code-reading one: the block below creates a branch (staging both the
    branch and its audit row) and then raises, so ``get_tenant_session`` rolls back. If
    the audit write had been given its own connection or its own commit — a tempting
    "so we never lose an audit row" change — the row would survive and this fails.

    The direction matters. An audit row that outlives a rolled-back mutation records a
    change that never happened, which is worse than a missing row: it is a false entry
    in the one table that is supposed to be evidence.
    """

    class _Abort(Exception):
        """Local sentinel, so an unrelated failure inside the block cannot pass this."""

    ctx = ctx_for(user_id=ADMIN_USER, org_id=ORG_A, role="coaching_admin")

    with pytest.raises(_Abort):
        async with get_tenant_session(ORG_A) as session:
            branch = await service.create_branch(
                session=session,
                ctx=ctx,
                org_id=ORG_A,
                payload=schemas.BranchCreateIn(name="P1T8PY Rolled Back"),
            )
            # Both rows exist in *this* transaction, so the rollback below has
            # something to undo and the assertions after it are not vacuous.
            assert branch.id is not None
            await session.flush()
            assert (
                await scalar(
                    session,
                    "SELECT count(*) FROM audit_logs WHERE action = 'branch.created'",
                )
                == 1
            ), "the audit row was not staged in the mutation's own transaction"
            raise _Abort

    assert await audit_rows(db_engine, ORG_A) == [], (
        "an audit row survived the rollback of the mutation it describes — the audit "
        "write is not sharing the mutation's transaction"
    )
    async with get_tenant_session(ORG_A) as session:
        assert (
            await scalar(
                session,
                "SELECT count(*) FROM branches WHERE name = :n",
                n="P1T8PY Rolled Back",
            )
            == 0
        ), "the branch itself survived the rollback"


async def test_proof7_a_refused_mutation_writes_no_audit_row(
    seed, app_user_factory, db_engine
):
    """A refusal records nothing — pinning the *order* of the check and the audit write.

    Being precise about what this does and does not prove, because the two look alike.
    ``update_branch`` performs its optimistic-lock check inside
    ``_update_or_translate`` and only calls ``record_audit`` *after* it returns, so on
    the stale ``rowVersion`` below the audit row is never even staged and atomicity is
    not what keeps it out.

    That is worth a test anyway rather than a comment, because the opposite ordering is
    already present in this very module: ``delete_branch`` calls ``record_audit``
    *before* ``repo.soft_delete``. So a future edit that made ``update_branch`` match
    its sibling would move this case onto the rollback path — where the previous test's
    property becomes the only thing keeping the table honest. This test fails loudly if
    such an edit also broke it, instead of the defect surfacing as a phantom
    ``branch.updated`` entry in a compliance export months later.
    """
    from app.common.errors import ConflictError

    ctx = ctx_for(user_id=ADMIN_USER, org_id=ORG_A, role="coaching_admin")

    async with get_tenant_session(ORG_A) as session:
        branch = await service.create_branch(
            session=session,
            ctx=ctx,
            org_id=ORG_A,
            payload=schemas.BranchCreateIn(name="P1T8PY Refused Branch"),
        )

    before = [r["action"] for r in await audit_rows(db_engine, ORG_A)]
    assert before == ["branch.created"]

    with pytest.raises(ConflictError):
        async with get_tenant_session(ORG_A) as session:
            await service.update_branch(
                session=session,
                ctx=ctx,
                branch_id=branch.id,
                payload=schemas.BranchUpdateIn(row_version=99, name="P1T8PY Nope"),
            )

    after = [r["action"] for r in await audit_rows(db_engine, ORG_A)]
    assert after == before, (
        "a refused mutation left an audit row behind: the trail now records a change "
        f"that did not happen ({after})"
    )

    # And the branch is genuinely untouched, so the refusal was real.
    async with get_tenant_session(ORG_A) as session:
        assert (
            await scalar(
                session, "SELECT name FROM branches WHERE id = :i", i=str(branch.id)
            )
            == "P1T8PY Refused Branch"
        )


async def test_proof7_forged_debug_headers_cannot_influence_attribution(
    seed, client, settings, db_engine
):
    """The audit row's ``actor_id`` and ``org_id`` come from the verified token only.

    This is the central attribution proof, and it is the one that connects P1-T8's audit
    work to P1-T7's. The request below presents a legitimate admin token *and* debug
    headers naming a different user and a different org. ``middleware/audit.py`` still
    reads those headers for its operational log lines, so the row this asserts on has to
    be sourced somewhere else entirely — from ``ctx``, which ``get_org_context`` builds
    from the token's signed claims.

    Note what this establishes about the guard chain as it now stands: P1-T7 replaced
    ``get_current_user``'s stub-header path with a mandatory ``Authorization`` header, so
    ``X-Debug-User-Id`` is no longer an input to authentication at all. The headers below
    are inert with respect to identity — which is the property being pinned, so that a
    future re-introduction of a header fallback fails here loudly.
    """
    response = await client.post(
        f"/api/v1/orgs/{ORG_A}/branches",
        json={"name": "P1T8PY Attribution Branch"},
        headers={
            **bearer(
                settings, user_id=ADMIN_USER, org_id=ORG_A, role="coaching_admin"
            ),
            DEBUG_USER_ID_HEADER: str(TEACH_USER),
            DEBUG_ORG_ID_HEADER: str(ORG_C),
            DEBUG_ROLES_HEADER: "super_admin",
        },
    )
    assert response.status_code == 201, response.text

    rows = await audit_rows(db_engine, ORG_A, ACTIONS.BRANCH_CREATED)
    assert len(rows) == 1
    assert rows[0]["actor_id"] == ADMIN_USER, (
        "the audit row's actor came from X-Debug-User-Id rather than from the "
        f"verified token (actor_id={rows[0]['actor_id']}, expected {ADMIN_USER})"
    )
    assert rows[0]["org_id"] == ORG_A, (
        "the audit row's org came from X-Debug-Org-Id rather than from the token"
    )

    # And nothing at all was written into the org the headers named.
    assert await audit_rows(db_engine, ORG_C) == [], (
        "a forged X-Debug-Org-Id caused a write into another tenant's audit log"
    )
    async with get_tenant_session(ORG_C) as session:
        assert await scalar(session, "SELECT count(*) FROM branches") == 1


async def test_proof7_audit_rows_are_append_only_for_app_user(
    seed, app_user_factory, db_engine
):
    """ADR-004's append-only guarantee, enforced by privilege rather than by trigger.

    Revision 0024 REVOKEs UPDATE on ``audit_logs`` and never grants DELETE, so the
    application role physically cannot rewrite history. Enforced by privilege is
    stronger than enforced by application code, which is the point: a bug in a service
    function cannot alter a row it has no grant to alter.

    ``insufficient_privilege`` arrives as a ``ProgrammingError`` through asyncpg; the
    ``DBAPIError`` in the tuple is the wider base, in case the driver classifies it
    differently. What is *not* accepted is success.

    The message is asserted as well as the exception type, and that is not belt-and-
    braces here — it is what separates two very different outcomes. PostgreSQL uses
    SQLSTATE ``42501`` for *both* a missing grant and an RLS ``WITH CHECK`` violation,
    so a bare ``pytest.raises`` would also pass if UPDATE had been granted and the row
    had merely been invisible to the policy. "permission denied" is the privilege
    answer, and privilege is what ADR-004 relies on.
    """
    ctx = ctx_for(user_id=ADMIN_USER, org_id=ORG_A, role="coaching_admin")
    async with get_tenant_session(ORG_A) as session:
        await service.create_branch(
            session=session,
            ctx=ctx,
            org_id=ORG_A,
            payload=schemas.BranchCreateIn(name="P1T8PY Append Only"),
        )

    rows = await audit_rows(db_engine, ORG_A)
    assert rows, "no audit row to attempt to tamper with"
    target_id = rows[0]["id"]

    with pytest.raises((ProgrammingError, DBAPIError)) as update_exc:
        async with get_tenant_session(ORG_A) as session:
            await session.execute(
                text("UPDATE audit_logs SET action = 'tampered' WHERE id = :i"),
                {"i": target_id},
            )
    assert "permission denied" in str(update_exc.value).lower(), (
        "UPDATE on audit_logs failed for some reason other than a missing grant, so "
        "this does not demonstrate ADR-004's append-only guarantee: "
        + str(update_exc.value)
    )

    with pytest.raises((ProgrammingError, DBAPIError)) as delete_exc:
        async with get_tenant_session(ORG_A) as session:
            await session.execute(
                text("DELETE FROM audit_logs WHERE id = :i"), {"i": target_id}
            )
    assert "permission denied" in str(delete_exc.value).lower(), (
        "DELETE on audit_logs failed for some reason other than a missing grant: "
        + str(delete_exc.value)
    )

    # Unchanged, verified past RLS.
    after = await audit_rows(db_engine, ORG_A)
    assert [r["id"] for r in after] == [r["id"] for r in rows]
    assert after[0]["action"] == rows[0]["action"] != "tampered"


async def test_proof7_audit_rows_are_tenant_isolated(seed, app_user_factory):
    """One tenant's audit log is invisible to another, under RLS as ``app_user``.

    Both orgs get a real row first, so "zero rows visible" cannot be an empty table.
    """
    async with get_tenant_session(ORG_A) as session:
        await service.create_branch(
            session=session,
            ctx=ctx_for(user_id=ADMIN_USER, org_id=ORG_A, role="coaching_admin"),
            org_id=ORG_A,
            payload=schemas.BranchCreateIn(name="P1T8PY Iso A"),
        )
    async with get_tenant_session(ORG_C) as session:
        await service.create_branch(
            session=session,
            ctx=ctx_for(user_id=OUTSIDE_USER, org_id=ORG_C, role="coaching_admin"),
            org_id=ORG_C,
            payload=schemas.BranchCreateIn(name="P1T8PY Iso C"),
        )

    async with get_tenant_session(ORG_A) as session:
        assert await scalar(session, "SELECT count(*) FROM audit_logs") == 1
        assert (
            await scalar(
                session,
                "SELECT count(*) FROM audit_logs WHERE org_id = :o",
                o=str(ORG_C),
            )
            == 0
        ), "org C's audit rows were visible from an org A session"

    async with get_tenant_session(ORG_C) as session:
        assert await scalar(session, "SELECT count(*) FROM audit_logs") == 1
        assert (
            await scalar(session, "SELECT actor_id::text FROM audit_logs")
            == str(OUTSIDE_USER)
        )


async def test_proof7_app_user_cannot_write_an_audit_row_for_another_tenant(
    seed, app_user_factory
):
    """``audit_logs``' ``WITH CHECK`` blocks a forged ``org_id`` on INSERT.

    The read isolation above stops a tenant *seeing* another's rows; this stops them
    *planting* one. It also covers the ``org_id IS NULL`` case, which revision 0025
    deliberately omits from the ``WITH CHECK`` — and which is the reason
    ``create_organization`` mints the org UUID in Python before opening its
    transaction, rather than letting ``gen_random_uuid()`` assign it.

    Each attempt gets its own ``async with``, and ``pytest.raises`` wraps the whole
    block rather than just the ``execute``. That is required, not stylistic: a failed
    statement aborts the PostgreSQL transaction, so catching the error *inside* the
    block would leave ``get_tenant_session`` to commit an aborted transaction and the
    test would die on a ``PendingRollbackError`` that says nothing about RLS.
    """
    with pytest.raises((ProgrammingError, DBAPIError)) as other_org_exc:
        async with get_tenant_session(ORG_A) as session:
            await session.execute(
                text(
                    "INSERT INTO audit_logs (org_id, actor_id, action) "
                    "VALUES (:o, :a, 'forged.cross_tenant')"
                ),
                {"o": str(ORG_C), "a": str(ADMIN_USER)},
            )
    assert "row-level security" in str(other_org_exc.value).lower(), (
        "the cross-tenant INSERT was refused for some reason other than the policy's "
        "WITH CHECK: " + str(other_org_exc.value)
    )

    # ``org_id IS NULL`` is the interesting case: the *USING* clause admits null-org
    # rows (revision 0025 adds ``OR org_id IS NULL`` for the platform-level events
    # described in app/models/audit.py), but the WITH CHECK deliberately does not. So
    # a tenant cannot launder a row into the globally-visible bucket.
    with pytest.raises((ProgrammingError, DBAPIError)) as null_org_exc:
        async with get_tenant_session(ORG_A) as session:
            await session.execute(
                text(
                    "INSERT INTO audit_logs (org_id, actor_id, action) "
                    "VALUES (NULL, :a, 'forged.null_org')"
                ),
                {"a": str(ADMIN_USER)},
            )
    assert "row-level security" in str(null_org_exc.value).lower(), str(
        null_org_exc.value
    )

    # Neither forgery landed. Read from org C — whose row the first attempt tried to
    # plant — and note that a null-org row would be visible from *any* session, so
    # this one query covers both attempts.
    async with get_tenant_session(ORG_C) as session:
        assert (
            await scalar(
                session,
                "SELECT count(*) FROM audit_logs WHERE action LIKE 'forged.%'",
            )
            == 0
        )


async def test_proof7_no_invitation_secret_reaches_the_audit_log(
    seed, app_user_factory, db_engine
):
    """No audit row's ``meta`` contains the invitation token or its digest.

    ``audit_logs`` has no UPDATE grant and no DELETE grant, so anything written here is
    permanent — a credential digest included "for traceability" could never be removed.
    The token hash is checked as well as the plaintext, because including the digest
    would let anyone who can read the audit log verify guesses at the token offline.
    """
    ctx = ctx_for(user_id=ADMIN_USER, org_id=ORG_A, role="coaching_admin")

    async with get_tenant_session(ORG_A) as session:
        created = await service.create_invitation(
            session=session,
            ctx=ctx,
            org_id=ORG_A,
            payload=schemas.InvitationCreateIn(email=INVITEE_EMAIL, role="teacher"),
        )

    token = created.token
    _, _, secret = token.partition(".")
    token_hash = hashlib.sha256(token.encode("utf-8")).hexdigest()

    rows = await audit_rows(db_engine, ORG_A, ACTIONS.INVITATION_SENT)
    assert len(rows) == 1, "invitation.sent was not audited at all"
    blob = str(rows[0]["meta"])

    assert secret not in blob, "the invitation secret was written to the audit log"
    assert token not in blob
    assert token_hash not in blob, "the invitation token hash was written to the audit log"

    # The useful, non-secret attribution *is* there — this is not an empty meta passing
    # the assertions above by accident.
    assert rows[0]["meta"]["email"] == INVITEE_EMAIL
    assert rows[0]["meta"]["role"] == "teacher"
    assert rows[0]["actor_id"] == ADMIN_USER

    # Belt and braces: scan every audit row in the database, not just this action.
    async with db_engine.begin() as conn:
        leaked = (
            await conn.execute(
                text(
                    "SELECT count(*) FROM audit_logs "
                    " WHERE meta::text LIKE :secret OR meta::text LIKE :hash"
                ),
                {"secret": "%" + secret + "%", "hash": "%" + token_hash + "%"},
            )
        ).scalar_one()
    assert leaked == 0


async def test_proof7_org_creation_is_audited_with_the_creator_as_actor(
    seed, client, settings, db_engine
):
    """``POST /orgs`` writes org, membership and two audit rows in one transaction.

    The bootstrap case: there is no org context yet, so the route is guarded by
    ``get_current_user`` alone and the token carries no ``activeOrgId``. All four rows
    have to land together, which is what forces the org's UUID to be minted in Python
    before the transaction opens (``audit_logs``' ``WITH CHECK`` needs ``app.org_id``
    set to a value that does not exist yet).
    """
    response = await client.post(
        "/api/v1/orgs",
        json={"name": ORG_NAME_PREFIX + " Bootstrapped", "orgType": "coaching_center"},
        headers=bearer(settings, user_id=TEACH_USER, org_id=None, role=None),
    )
    assert response.status_code == 201, response.text
    data = envelope_data(response)
    new_org_id = uuid.UUID(data["id"])
    assert data["status"] == "active"
    assert data["rowVersion"] == 1
    assert data["region"] == "ap-south-1", "the column default did not apply"
    assert data["settings"] == {}

    rows = await audit_rows(db_engine, new_org_id)
    assert [r["action"] for r in rows] == ["org.created", "membership.created"], rows
    assert all(r["actor_id"] == TEACH_USER for r in rows)

    # The creator really is an admin of the org they just made.
    orgs = await service.list_own_orgs(actor_user_id=TEACH_USER)
    by_org = {o.org_id: o for o in orgs}
    assert new_org_id in by_org
    assert by_org[new_org_id].role == service.ORG_CREATOR_ROLE == "coaching_admin"


async def test_proof7_invitation_acceptance_attributes_the_invitee_not_the_inviter(
    seed, app_user_factory, db_engine, settings
):
    """The actor on an acceptance is the invitee, even though they were unauthenticated.

    Worth asserting because it is the one place in the module where a durable audit row
    is written for a request that carried no token. The actor is not a header-supplied
    string and not the inviter: it is the id of the account whose invitation was proven,
    which this very transaction may have created.
    """
    ctx = ctx_for(user_id=ADMIN_USER, org_id=ORG_A, role="coaching_admin")

    async with get_tenant_session(ORG_A) as session:
        created = await service.create_invitation(
            session=session,
            ctx=ctx,
            org_id=ORG_A,
            payload=schemas.InvitationCreateIn(email=INVITEE_EMAIL, role="teacher"),
        )

    accepted = await service.accept_invitation(
        payload=schemas.InvitationAcceptIn(
            token=created.token,
            full_name="P1T8PY Invitee",
            password=ACCEPT_PASSWORD,
        ),
        settings=settings,
    )

    sent = await audit_rows(db_engine, ORG_A, ACTIONS.INVITATION_SENT)
    assert len(sent) == 1, f"expected one invitation.sent row, got {len(sent)}"
    assert sent[0]["actor_id"] == ADMIN_USER, (
        "the inviter is the actor on invitation.sent, not the invitee "
        f"(got {sent[0]['actor_id']})"
    )

    for action in (
        ACTIONS.USER_CREATED,
        ACTIONS.MEMBERSHIP_CREATED,
        ACTIONS.INVITATION_ACCEPTED,
    ):
        rows = await audit_rows(db_engine, ORG_A, action)
        assert len(rows) == 1, f"expected exactly one {action} row, got {len(rows)}"
        assert rows[0]["actor_id"] == accepted.user_id, (
            f"{action} was attributed to {rows[0]['actor_id']} rather than to the "
            f"invitee {accepted.user_id}"
        )
        assert rows[0]["org_id"] == ORG_A
