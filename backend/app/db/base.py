"""Declarative base and the shared column mixins every PaperGen table carries.

Sources of truth for everything in this module:

* ``docs/docs/03_Database_Design.md`` §5 (per-table columns), §7 (data dictionary),
  §8 (ORM model design — ``Mapped[...]`` annotations, SQLAlchemy 2.x async).
* ``docs/docs/ADR/ADR-004-soft-deletes-and-append-only-audit.md`` — ``deleted_at``
  on every business entity; **no hard DELETE anywhere in application code**.
* ``docs/docs/ADR/ADR-006-fastapi-over-nestjs.md`` — SQLAlchemy 2.x + Alembic
  (this replaced Prisma; nothing in this tree may reintroduce a second ORM).

Naming convention
-----------------
``MetaData(naming_convention=...)`` is deliberate rather than cosmetic. Without it
Alembic autogenerate emits ``None`` for CHECK constraint names, and an unnamed
constraint cannot be dropped by a downgrade — the revision becomes irreversible
for no good reason. Every constraint in this schema therefore has a deterministic,
reproducible name derived from the table and columns.
"""

from __future__ import annotations

import datetime as _dt
import uuid as _uuid
from typing import Optional

from sqlalchemy import CheckConstraint, DateTime, Integer, MetaData, func, text
from sqlalchemy.dialects.postgresql import UUID
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column

# ---------------------------------------------------------------------------
# Constraint naming convention
# ---------------------------------------------------------------------------
# %(column_0_N_name)s expands to every column in the constraint joined by "_",
# so a composite unique index gets a name that actually describes it.
NAMING_CONVENTION: dict[str, str] = {
    "ix": "ix_%(table_name)s_%(column_0_N_name)s",
    "uq": "uq_%(table_name)s_%(column_0_N_name)s",
    "ck": "ck_%(table_name)s_%(constraint_name)s",
    "fk": "fk_%(table_name)s_%(column_0_name)s_%(referred_table_name)s",
    "pk": "pk_%(table_name)s",
}

# Column sort_order buckets. mapped_column(sort_order=...) is the only reliable
# way to keep mixin-contributed columns in a predictable position; without it the
# audit columns land wherever the MRO puts them and the generated DDL stops
# resembling doc 03 §5's column listing.
_SORT_PK = -100
_SORT_AUDIT = 100


class Base(DeclarativeBase):
    """Declarative base shared by every PaperGen model.

    ``Base.metadata`` is the single object Alembic's ``target_metadata`` points at,
    so a model that is not imported before autogenerate runs is invisible to it.
    ``app/models/__init__.py`` exists to guarantee that import happens.
    """

    metadata = MetaData(naming_convention=NAMING_CONVENTION)


class UUIDPrimaryKeyMixin:
    """``id uuid PRIMARY KEY DEFAULT gen_random_uuid()`` (doc 03 §5, "id generated UUID").

    The default is a **server** default, not a Python-side default, so a row
    inserted by raw SQL, a seed script, or a psql session gets the same treatment
    as one inserted through the ORM.

    ``gen_random_uuid()`` is core PostgreSQL from 13 onward; doc 03 §9 step 1
    enables ``pgcrypto`` regardless, which also supplies it.
    """

    id: Mapped[_uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        primary_key=True,
        server_default=text("gen_random_uuid()"),
        sort_order=_SORT_PK,
    )


class TimestampMixin:
    """``created_at`` / ``updated_at timestamptz NOT NULL DEFAULT now()`` (doc 03 §5).

    ``onupdate=func.now()`` makes SQLAlchemy add ``updated_at = now()`` to every
    UPDATE it emits. Note the scope of that promise: it holds for ORM flushes and
    for ``sqlalchemy.update()`` constructs against this table, and **not** for an
    UPDATE typed straight into psql. Doc 03 does not specify a trigger, so none is
    created here; if out-of-band writes ever need to bump the column, that is a
    ``BEFORE UPDATE`` trigger in a later revision, not a change to this mixin.
    """

    created_at: Mapped[_dt.datetime] = mapped_column(
        DateTime(timezone=True),
        nullable=False,
        server_default=func.now(),
        sort_order=_SORT_AUDIT,
    )
    updated_at: Mapped[_dt.datetime] = mapped_column(
        DateTime(timezone=True),
        nullable=False,
        server_default=func.now(),
        onupdate=func.now(),
        sort_order=_SORT_AUDIT + 1,
    )


class SoftDeleteMixin:
    """``deleted_at timestamptz NULL`` — ADR-004.

    Deletion sets this timestamp. Nothing in application code issues a hard
    ``DELETE`` against a table carrying this column; the read filter
    (``deleted_at IS NULL``) belongs in the repository base class so that no call
    site has to remember it — that plumbing is P1-T5, not this task.

    ADR-004 also records the consequence that shapes this schema: *"a soft-deleted
    row still occupies its unique key, so 'delete and recreate with the same name'
    fails unless the constraint is partial (WHERE deleted_at IS NULL)."* Every
    uniqueness rule in ``app/models`` is therefore a partial unique index rather
    than a plain UNIQUE constraint.
    """

    deleted_at: Mapped[Optional[_dt.datetime]] = mapped_column(
        DateTime(timezone=True),
        nullable=True,
        sort_order=_SORT_AUDIT + 2,
    )


class RowVersionMixin:
    """``row_version integer NOT NULL DEFAULT 1`` for optimistic locking (doc 03 §5).

    The column and its positive-value CHECK ship here. **Enforcement does not.**
    SQLAlchemy's native ``__mapper_args__ = {"version_id_col": ...}`` is
    deliberately *not* wired up, because turning it on changes the shape of every
    UPDATE this application emits (adding ``WHERE row_version = :n`` and raising
    ``StaleDataError``) and the component that owns that behaviour — the
    repository base class — is P1-T5. Wiring it here and again there would risk a
    double bump. P1-T5 should choose exactly one mechanism.
    """

    row_version: Mapped[int] = mapped_column(
        Integer,
        nullable=False,
        server_default=text("1"),
        sort_order=_SORT_AUDIT + 3,
    )


def row_version_positive() -> CheckConstraint:
    """CHECK (row_version > 0) — doc 03 §5 organizations, "row_version positive".

    The naming convention turns this into ``ck_<table>_row_version_positive``, so the
    constraint name carries no hand-written table prefix.

    doc 03 states this invariant only under ``organizations``, but every one of the
    four tenancy tables has the identical column with the identical default, and
    the constraint lists in §5 are plainly abbreviated (``branches`` lists no
    constraint for ``row_version`` at all while still declaring the column). It is
    applied uniformly and flagged in the P1-T3 hand-off notes as a deliberate
    consistency extension rather than a documented requirement.
    """

    return CheckConstraint("row_version > 0", name="row_version_positive")


def check_in(column: str, values: tuple[str, ...], *, name: str) -> CheckConstraint:
    """A named ``CHECK (col IN (...))`` over a controlled vocabulary.

    Every controlled vocabulary in this schema is a **constrained text column with
    a CHECK constraint, never a native PostgreSQL ENUM**. That is ADR-011's central
    point and it applies beyond ``role``: adding a value to a CHECK is a one-line
    migration, whereas ``ALTER TYPE ... ADD VALUE`` cannot be reversed cleanly and
    cannot remove a value at all.

    The constraint is always named so that ``downgrade()`` can drop it by name.
    """

    rendered = ", ".join(f"'{value}'" for value in values)
    return CheckConstraint(f"{column} IN ({rendered})", name=name)


__all__ = [
    "NAMING_CONVENTION",
    "Base",
    "RowVersionMixin",
    "SoftDeleteMixin",
    "TimestampMixin",
    "UUIDPrimaryKeyMixin",
    "check_in",
    "row_version_positive",
]
