"""Alembic environment — async SQLAlchemy, single source of truth for the DSN.

Based on the ``async`` template shipped with Alembic 1.19.1 (``alembic init -t
async``), with three deliberate departures from that template:

1. **The engine is built by** :func:`app.db.engine.create_engine`, not by
   ``async_engine_from_config``. Two reasons. The DSN comes from
   ``app.core.config.Settings`` (P1-T2), so writing it into the Alembic config with
   ``set_main_option`` would be the only place a second copy exists — and worse,
   ConfigParser %-interpolates values on read, so a password containing a literal
   ``%`` raises ``InterpolationSyntaxError``. Building the engine directly also means
   migrations inherit the PgBouncer handling (NullPool + unique prepared-statement
   names) that the application uses, which matters because the configured Neon
   endpoint is the pooled one.

2. ``target_metadata`` is ``app.models.Base.metadata``, and ``app.models`` is imported
   for its side effect of registering every model on that metadata. A model missing
   from ``app/models/__init__.py`` is not merely skipped by autogenerate — it is seen
   as a table that should not exist, and autogenerate proposes dropping it.

3. ``compare_type=True`` so a changed column type shows up in autogenerate diffs.
   ``compare_server_default`` is left off: PostgreSQL normalizes defaults on the way
   in (``'active'`` becomes ``'active'::text``) and the resulting phantom diffs train
   people to ignore autogenerate output.
"""

from __future__ import annotations

import asyncio
from logging.config import fileConfig

from sqlalchemy.engine import Connection

from alembic import context

from app.core.config import get_settings
from app.db.engine import create_engine

# Importing app.models is what populates Base.metadata. Do not remove.
import app.models  # noqa: F401
from app.models import Base

config = context.config

if config.config_file_name is not None:
    fileConfig(config.config_file_name)

target_metadata = Base.metadata


def _database_url() -> str:
    """The one place this process learns the connection string.

    ``-x db_url=...`` is honoured so that CI and the local verification script can
    point a run at a scratch database without editing ``.env`` — the value still
    never gets written into the Alembic config object.
    """

    overrides = context.get_x_argument(as_dictionary=True)
    if overrides.get("db_url"):
        return overrides["db_url"]
    return get_settings().DATABASE_URL


def run_migrations_offline() -> None:
    """Emit SQL to stdout instead of running it (``alembic upgrade head --sql``).

    Useful for reviewing exactly what would run against production, and for handing
    DDL to a DBA who applies it out of band.
    """

    context.configure(
        url=_database_url(),
        target_metadata=target_metadata,
        literal_binds=True,
        dialect_opts={"paramstyle": "named"},
        compare_type=True,
    )

    with context.begin_transaction():
        context.run_migrations()


def do_run_migrations(connection: Connection) -> None:
    context.configure(
        connection=connection,
        target_metadata=target_metadata,
        compare_type=True,
    )

    with context.begin_transaction():
        context.run_migrations()


async def run_async_migrations() -> None:
    """Open an async connection and drive the sync migration API through it."""

    connectable = create_engine(url=_database_url())

    try:
        async with connectable.connect() as connection:
            await connection.run_sync(do_run_migrations)
    finally:
        await connectable.dispose()


def run_migrations_online() -> None:
    asyncio.run(run_async_migrations())


if context.is_offline_mode():
    run_migrations_offline()
else:
    run_migrations_online()
