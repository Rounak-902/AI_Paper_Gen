"""Tenant-scoped async database session (P1-T5).

This is the ``SET LOCAL app.org_id`` plumbing that doc 07 §3 places in
``src/infra/db.py`` and doc 07 §12.1 describes under ``get_db``:

    ``async def get_db(org: OrgContext = Depends(get_org_context))``
        → yields an ``AsyncSession`` inside a transaction with
        ``SET LOCAL app.org_id`` already applied.

The implementation lives in ``app/db/`` because that is where the engine and base
already live, and ``src/infra/`` does not exist yet. When P1-T6 builds the guard
chain and the FastAPI dependency, it will import from here.

Why ``SET LOCAL`` and never ``SET``
-----------------------------------
Doc 03 §8 spells this out:

    "Every session runs inside a request-scoped transaction that issues
     ``SET LOCAL app.org_id = :org_id`` **before any query** — never a
     session-level ``SET``. Under PgBouncer transaction pooling a session-level
     setting leaks the tenant context to the next borrower of that backend."

``SET LOCAL`` scopes the variable to the current transaction. When the transaction
commits or rolls back, the setting disappears — so the next transaction on the
same pooled connection starts clean.

How that is actually issued: ``SELECT set_config('app.org_id', :org_id, true)``.
``SET`` is a *utility* statement and its grammar accepts a literal in the value
position, not a placeholder, so ``SET LOCAL app.org_id = :org_id`` reaches the
server as ``SET LOCAL app.org_id = $1`` and is rejected as a syntax error.
``set_config(setting, value, is_local)`` is an ordinary function, so the value can
be bound; ``is_local => true`` gives exactly ``SET LOCAL`` semantics. The wording
of doc 03 §8 describes the *semantics* required, and ``set_config`` with
``is_local`` is the parameterizable spelling of them.

Fail-loud behaviour
-------------------
The task instruction says: "fail loud (raise) rather than silently running unscoped
if no org context was provided." Both ``get_tenant_session`` and
``TenantSessionContext`` enforce this:

* ``org_id`` is typed as ``uuid.UUID`` (not ``Optional``).
* An explicit ``if org_id is None`` guard catches dynamic callers that bypass the
  type checker.
* The ``TenantContextMissing`` exception fires **before** any database statement
  is issued — the session is never opened without an org context.

P1-T8 addition — ``app.user_id`` and ``get_identity_session``
-------------------------------------------------------------
"Which organizations does this user belong to" is asked *before* an org context
exists, so it cannot be answered inside ``get_tenant_session``. P1-T8 adds
:func:`get_identity_session`, which sets ``app.user_id`` (and ``app.org_id`` too
when one is known) with the same ``set_config(..., is_local => true)`` mechanism
and the same bound-parameter discipline. Revision 0027's
``memberships_self_select`` policy is the other half of that pair. See that
function's docstring for why it is a sibling rather than an extra parameter on
:func:`get_tenant_session`.

What this module does NOT do
----------------------------
* It does not resolve ``org_id`` from a JWT or request context — that is P1-T7
  (auth) and P1-T6 (guard chain). This module accepts ``org_id`` as a parameter.
* It does not build the FastAPI ``Depends()`` chain — P1-T6 wraps this in a
  dependency that calls ``get_org_context`` first.
* It does not create the ``async_session_factory`` — it takes one as a parameter
  or builds one from the engine.
"""

from __future__ import annotations

import uuid
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from typing import Optional

from sqlalchemy import text
from sqlalchemy.ext.asyncio import (
    AsyncEngine,
    AsyncSession,
    async_sessionmaker,
)

from app.db.exceptions import IdentityContextMissing, TenantContextMissing

# ---------------------------------------------------------------------------
# Module-level session factory — initialised by ``init_session_factory``
# ---------------------------------------------------------------------------
_session_factory: Optional[async_sessionmaker[AsyncSession]] = None


def init_session_factory(engine: AsyncEngine) -> async_sessionmaker[AsyncSession]:
    """Create and cache the module-level session factory.

    Called once at application startup (lifespan) or at the start of a test suite.
    The factory is ``expire_on_commit=False`` so that objects accessed after the
    ``yield`` in ``get_tenant_session`` do not trigger lazy loads against a closed
    transaction.
    """
    global _session_factory
    _session_factory = async_sessionmaker(
        engine,
        class_=AsyncSession,
        expire_on_commit=False,
    )
    return _session_factory


def get_session_factory() -> async_sessionmaker[AsyncSession]:
    """Return the cached session factory, or raise if uninitialised."""
    if _session_factory is None:
        raise RuntimeError(
            "Session factory not initialised. Call init_session_factory(engine) "
            "at application startup before requesting sessions."
        )
    return _session_factory


# ---------------------------------------------------------------------------
# Tenant-scoped session
# ---------------------------------------------------------------------------

@asynccontextmanager
async def get_tenant_session(
    org_id: uuid.UUID,
    *,
    session_factory: Optional[async_sessionmaker[AsyncSession]] = None,
) -> AsyncIterator[AsyncSession]:
    """Yield an ``AsyncSession`` with the tenant context already applied.

    The context is set with ``set_config('app.org_id', <org_id>, true)``, which is
    ``SET LOCAL`` semantics in a form that accepts a bound parameter (see the
    module docstring).

    This is the single entry point for every tenant-scoped database operation.
    The transaction is committed on successful exit and rolled back on exception.

    :param org_id: the tenant UUID. Must not be None.
    :param session_factory: override the module-level factory (used by tests).
    :raises TenantContextMissing: if ``org_id`` is None.
    """
    if org_id is None:
        raise TenantContextMissing(
            "get_tenant_session() called with org_id=None. "
            "The guard chain must resolve an org context before opening a session."
        )

    factory = session_factory or get_session_factory()

    async with factory() as session:
        async with session.begin():
            # Transaction-scoped tenant context.
            #
            # This is issued via set_config() and NOT as
            # ``SET LOCAL app.org_id = :org_id``. ``SET`` is a utility statement:
            # its grammar takes a literal token in the value position, so the
            # bound form reaches the server as ``SET LOCAL app.org_id = $1`` and
            # is rejected with a syntax error. Every call through this function
            # failed on that line until it was fixed.
            #
            # ``set_config(setting, value, is_local)`` is an ordinary function, so
            # the value can be bound, and ``is_local => true`` reverts the setting
            # when this transaction ends — the same semantics as ``SET LOCAL``,
            # which is what keeps a pooled connection from carrying one request's
            # tenant context into the next (doc 03 §8).
            #
            # Interpolating the UUID into the SQL string would also run, and is
            # deliberately not done: org_id arrives from a JWT claim in P1-T7, and
            # a bound parameter cannot be an injection vector.
            await session.execute(
                text("SELECT set_config('app.org_id', :org_id, true)"),
                {"org_id": str(org_id)},
            )
            yield session
            # Commit happens when the ``session.begin()`` block exits cleanly.
            # Rollback happens on exception.


@asynccontextmanager
async def get_identity_session(
    user_id: uuid.UUID,
    *,
    org_id: Optional[uuid.UUID] = None,
    session_factory: Optional[async_sessionmaker[AsyncSession]] = None,
) -> AsyncIterator[AsyncSession]:
    """Yield an ``AsyncSession`` scoped to a **user identity** (P1-T8).

    Sets ``app.user_id``, and ``app.org_id`` as well when an org is known. This is
    what makes revision 0027's ``memberships_self_select`` policy usable, and
    therefore what makes ``GET /orgs`` and ``GET /auth/me`` answerable as
    ``app_user`` (which is ``NOBYPASSRLS``).

    A SIBLING, NOT AN EXTENSION — and here is why
    ---------------------------------------------
    P1-T8's brief allowed either a new function or a documented extension of
    :func:`get_tenant_session`. A separate function was chosen for one concrete
    reason: extending the existing one would mean adding an optional ``user_id``
    parameter, and every one of its existing call sites would keep compiling while
    silently **not** setting ``app.user_id``. A security-relevant GUC whose absence
    is the default, and whose absence is invisible at the call site, is the shape of
    defect that survives review. With two functions, which contexts a transaction
    carries is legible from the name at the point of use: ``get_tenant_session``
    means org only, ``get_identity_session`` means user (plus org when given).

    :func:`get_tenant_session` is also *stricter* than this function in a way that
    should not be relaxed — it raises when ``org_id`` is None, which is P1-T5's
    fail-loud requirement. Making ``org_id`` optional there would have deleted that
    guarantee for every existing caller.

    What is deliberately NOT here
    -----------------------------
    ``app.user_id`` is set from ``AuthenticatedUser.user_id`` only. It is never
    populated from a path parameter, a query string, a request body or a header —
    if it were, revision 0027's self-select policy would let any caller read any
    user's memberships by naming them, which is the exact vulnerability the policy
    is meant to avoid. On this branch the value originates in the ``sub`` claim of
    an access token verified by HMAC signature check (P1-T7's
    ``decode_access_token``), so the assertion is cryptographic rather than
    advisory. Before P1-T7 it would not have been.

    :param user_id: the authenticated principal. Must not be None.
    :param org_id: optionally also set ``app.org_id``, for operations that need
        both — creating an organization, or accepting an invitation, where a
        ``memberships`` write and an ``audit_logs`` write must happen in the same
        transaction as an identity read.
    :raises IdentityContextMissing: if ``user_id`` is None.
    """
    if user_id is None:
        raise IdentityContextMissing(
            "get_identity_session() called with user_id=None. "
            "get_current_user must resolve a principal before opening a session."
        )

    factory = session_factory or get_session_factory()

    async with factory() as session:
        async with session.begin():
            # set_config(), NOT ``SET LOCAL app.user_id = :user_id``.
            #
            # ``SET`` is a utility statement whose grammar takes a literal in the
            # value position, so the bound form arrives at the server as
            # ``SET LOCAL app.user_id = $1`` and is rejected as a syntax error.
            # This is the exact bug that cost P1-T5 three verification rounds; it
            # is written out here rather than left to a reader to rediscover.
            #
            # ``is_local => true`` gives ``SET LOCAL`` semantics: the setting
            # reverts when this transaction ends, so a PgBouncer-pooled backend
            # cannot carry one request's identity into the next borrower's
            # transaction (doc 03 §8).
            await session.execute(
                text("SELECT set_config('app.user_id', :user_id, true)"),
                {"user_id": str(user_id)},
            )
            if org_id is not None:
                await session.execute(
                    text("SELECT set_config('app.org_id', :org_id, true)"),
                    {"org_id": str(org_id)},
                )
            yield session


@asynccontextmanager
async def get_unscoped_session(
    *,
    session_factory: Optional[async_sessionmaker[AsyncSession]] = None,
) -> AsyncIterator[AsyncSession]:
    """Yield an ``AsyncSession`` **without** tenant scoping.

    For operations that are genuinely not tenant-scoped:
    * Health checks
    * Migration verification
    * Cross-tenant admin operations (super_admin with explicit audit)

    Using this for a tenant-scoped operation is a bug — the repository base class
    will still try to filter by org_id, and RLS will block the query if app.org_id
    is not set. This function exists so that the *absence* of scoping is explicit
    rather than accidental.
    """
    factory = session_factory or get_session_factory()

    async with factory() as session:
        async with session.begin():
            yield session
