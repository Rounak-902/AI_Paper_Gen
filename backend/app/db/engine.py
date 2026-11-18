"""Async engine construction for PaperGen.

Scope note (deliberately narrow)
--------------------------------
This module builds an ``AsyncEngine`` from the P1-T2 ``Settings`` object and
nothing else. It is the minimum needed for Alembic's ``env.py`` and the P1-T3
round-trip test to talk to PostgreSQL without either of them owning a second copy
of the connection logic.

What is **not** here, on purpose:

* the request-scoped session dependency,
* ``SET LOCAL app.org_id`` per transaction,
* the repository base class and its ``deleted_at IS NULL`` / ``row_version``
  enforcement.

All of that is **P1-T5**, and doc 07 §3 puts it in ``src/infra/db.py``. Building it
here would preempt that task.

``DATABASE_URL`` is read from ``app.core.config.Settings`` — there is exactly one
source of truth for the connection string, and no DSN is hardcoded anywhere in
this file.
"""

from __future__ import annotations

from typing import Any, Optional
from uuid import uuid4

from sqlalchemy.ext.asyncio import AsyncEngine, create_async_engine
from sqlalchemy.pool import NullPool

from app.core.config import Settings, get_settings


def is_transaction_pooled(database_url: str) -> bool:
    """Does this DSN point at a transaction-pooling proxy (PgBouncer)?

    Neon's pooled endpoint is the same hostname with a ``-pooler`` suffix, and
    Supabase uses ``pgbouncer=true``. Both put PgBouncer in transaction-pooling
    mode in front of PostgreSQL, which matters because asyncpg names its prepared
    statements ``__asyncpg_stmt_1__``, ``__asyncpg_stmt_2__``, … in per-connection
    numeric order. Through a pool those names collide across clients and queries
    fail with ``prepared statement "__asyncpg_stmt_N__" already exists``.

    SQLAlchemy 2.0's own asyncpg dialect documents the fix (see the module
    docstring in ``sqlalchemy/dialects/postgresql/asyncpg.py``, which cites
    asyncpg issue 837 and sqlalchemy issue 6467): ``NullPool`` plus a
    ``prepared_statement_name_func`` that returns a unique name.
    """

    lowered = database_url.lower()
    return "-pooler." in lowered or "pgbouncer=true" in lowered


def build_engine_kwargs(
    settings: Settings,
    *,
    url: Optional[str] = None,
) -> dict[str, Any]:
    """Engine keyword arguments derived from settings, PgBouncer-aware.

    Returned separately from :func:`create_engine` so that Alembic's ``env.py``
    and the tests can inspect or override the decision instead of duplicating it.

    :param url: the DSN the engine will actually connect to, when it differs from
        ``settings.DATABASE_URL``. The pooling decision has to follow the *effective*
        target: running a migration or a test against ``localhost`` while ``.env``
        holds a Neon pooled DSN must not inherit Neon's ``NullPool`` treatment, and
        vice versa.
    """

    effective_url = url or settings.DATABASE_URL
    kwargs: dict[str, Any] = {"echo": settings.DB_ECHO, "future": True}

    if is_transaction_pooled(effective_url):
        # Client-side pooling on top of server-side transaction pooling buys
        # nothing and breaks prepared statements; let PgBouncer do the pooling.
        kwargs["poolclass"] = NullPool
        # Both keys are popped by the dialect's own DBAPI shim
        # (AsyncAdapt_asyncpg_dbapi.connect, asyncpg.py:938-943) before the rest of
        # connect_args is handed to asyncpg.connect, so neither reaches the driver
        # as an unknown kwarg. Cache size 0 matters as much as the name function:
        # under transaction pooling the next statement may land on a different
        # backend, where a cached statement handle is meaningless.
        kwargs["connect_args"] = {
            "prepared_statement_cache_size": 0,
            "prepared_statement_name_func": lambda: f"__asyncpg_{uuid4()}__",
        }
    else:
        kwargs["pool_size"] = settings.DB_POOL_SIZE
        kwargs["max_overflow"] = settings.DB_MAX_OVERFLOW
        kwargs["pool_pre_ping"] = True

    return kwargs


def create_engine(
    settings: Optional[Settings] = None,
    *,
    url: Optional[str] = None,
) -> AsyncEngine:
    """Build an ``AsyncEngine``.

    :param settings: defaults to the cached :func:`app.core.config.get_settings`.
    :param url: overrides ``settings.DATABASE_URL``. Used by the test suite and by
        Alembic's ``-x db_url=...`` to point at a scratch database; it is **not** a
        way to smuggle a hardcoded DSN into application code.
    """

    settings = settings or get_settings()
    effective_url = url or settings.DATABASE_URL
    return create_async_engine(
        effective_url,
        **build_engine_kwargs(settings, url=effective_url),
    )


__all__ = ["build_engine_kwargs", "create_engine", "is_transaction_pooled"]
