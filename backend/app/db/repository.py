"""Generic repository base class (P1-T5).

Sources:
* doc 07 §2.4: "Data access is isolated behind repository classes. Services never
  write raw SQL or touch the SQLAlchemy session directly. This centralizes tenant
  scoping (``org_id`` predicate), soft-delete filters, pagination, and optimistic
  locking in one layer."
* doc 07 §12.1–12.2: the code examples for ``find_many``, ``update``, ``soft_delete``.
* ADR-004: "Every read path filters ``deleted_at IS NULL``, applied centrally in the
  repository base class rather than remembered per query."
* doc 07 §2.9: "Every database query carries ``org_id``. PostgreSQL RLS provides a
  second line of defense."

Design decisions
----------------
1. **Optimistic locking via explicit WHERE, not SQLAlchemy version_id_col.**
   ``RowVersionMixin``'s docstring (``app/db/base.py``) says P1-T5 should choose
   exactly one mechanism to avoid a double bump. The explicit-WHERE approach matches
   doc 07 §12.2's code example and gives us control over the error type
   (``OptimisticLockError`` rather than SQLAlchemy's ``StaleDataError``).

2. **``org_id`` as constructor parameter, not read from session variable.**
   The repository applies its own ``WHERE org_id = :org_id`` as the application-level
   first line of defense. RLS (which reads ``current_setting('app.org_id')``) is the
   second line. Both are set from the same value — the session mechanism and the
   repository are initialised from the same ``org_id`` — but they are independent
   enforcement points.

3. **Generic type parameter ``ModelT``.**
   Subclasses specify their model: ``class QuestionBankRepository(BaseRepository[QuestionBank])``.
   The base class uses ``ModelT`` for type-safe return values without casting.

4. **``include_deleted=False`` on every read method.**
   ADR-004: the default is always to filter deleted rows. A caller that needs them
   (e.g. an admin undelete operation) must opt in explicitly.

5. **No hard DELETE.**
   ADR-004: "hard deletion exists only as an explicit maintenance operation (tenant
   offboarding, GDPR erasure) and is itself audited." The base class has no ``delete``
   method — only ``soft_delete``.
"""

from __future__ import annotations

import datetime as _dt
import uuid
from typing import Any, Generic, Optional, Sequence, Type, TypeVar

from sqlalchemy import Select, func, select, update
from sqlalchemy.ext.asyncio import AsyncSession

from app.db.exceptions import EntityNotFoundError, OptimisticLockError

# ---------------------------------------------------------------------------
# Type variable for the ORM model
# ---------------------------------------------------------------------------
ModelT = TypeVar("ModelT")


class BaseRepository(Generic[ModelT]):
    """Repository base class enforcing tenant isolation, soft-delete, and optimistic locking.

    Every module-specific repository inherits from this. Services call repository
    methods, never SQLAlchemy directly.

    :param model: the SQLAlchemy model class (e.g. ``QuestionBank``).
    :param session: the ``AsyncSession`` from ``get_tenant_session``.
    :param org_id: the tenant UUID — applied as a WHERE predicate on every query.
    """

    #: Subclasses set this to their model class. It is also accepted as a
    #: constructor argument for cases where the class attribute is not set
    #: (e.g. tests with a generic repository).
    model_class: Type[ModelT]

    def __init__(
        self,
        session: AsyncSession,
        org_id: uuid.UUID,
        *,
        model: Optional[Type[ModelT]] = None,
    ) -> None:
        self._session = session
        self._org_id = org_id
        if model is not None:
            self.model_class = model

    # ------------------------------------------------------------------
    # Internal helpers
    # ------------------------------------------------------------------

    def _org_id_column(self) -> Any:
        """Return the model's ``org_id`` column, or None if it doesn't have one.

        Tables without a direct ``org_id`` (child tables that reach it through a
        parent FK) should override this to return None and handle scoping in their
        own query methods. The base class skips the org predicate for those tables
        — RLS is still the backstop.
        """
        col = getattr(self.model_class, "org_id", None)
        return col

    def _base_query(self, *, include_deleted: bool = False) -> Select:
        """Build the base SELECT with soft-delete and tenant filters applied."""
        stmt = select(self.model_class)

        # Tenant scoping (first line of defense)
        org_col = self._org_id_column()
        if org_col is not None:
            stmt = stmt.where(org_col == self._org_id)

        # Soft-delete filter (ADR-004)
        if not include_deleted:
            deleted_at = getattr(self.model_class, "deleted_at", None)
            if deleted_at is not None:
                stmt = stmt.where(deleted_at.is_(None))

        return stmt

    # ------------------------------------------------------------------
    # Read methods
    # ------------------------------------------------------------------

    async def get_by_id(
        self,
        id: uuid.UUID,
        *,
        include_deleted: bool = False,
    ) -> Optional[ModelT]:
        """Fetch a single row by primary key, scoped to the current tenant.

        Returns ``None`` if the row does not exist, belongs to a different tenant,
        or is soft-deleted (unless ``include_deleted=True``).
        """
        stmt = self._base_query(include_deleted=include_deleted).where(
            self.model_class.id == id  # type: ignore[attr-defined]
        )
        result = await self._session.execute(stmt)
        return result.scalar_one_or_none()

    async def get_by_id_or_raise(
        self,
        id: uuid.UUID,
        *,
        include_deleted: bool = False,
    ) -> ModelT:
        """Like ``get_by_id`` but raises ``EntityNotFoundError`` on a miss."""
        row = await self.get_by_id(id, include_deleted=include_deleted)
        if row is None:
            raise EntityNotFoundError(
                entity=self.model_class.__tablename__,  # type: ignore[attr-defined]
                entity_id=id,
            )
        return row

    async def find_many(
        self,
        *extra_filters: Any,
        include_deleted: bool = False,
        order_by: Optional[Any] = None,
        limit: Optional[int] = None,
        offset: Optional[int] = None,
    ) -> Sequence[ModelT]:
        """List rows matching additional filters, scoped to the current tenant.

        :param extra_filters: additional SQLAlchemy WHERE clauses.
        :param include_deleted: if True, include soft-deleted rows.
        :param order_by: a column or list of columns to sort by.
        :param limit: maximum number of rows.
        :param offset: number of rows to skip.
        """
        stmt = self._base_query(include_deleted=include_deleted)

        for f in extra_filters:
            stmt = stmt.where(f)

        if order_by is not None:
            if isinstance(order_by, (list, tuple)):
                stmt = stmt.order_by(*order_by)
            else:
                stmt = stmt.order_by(order_by)

        if limit is not None:
            stmt = stmt.limit(limit)
        if offset is not None:
            stmt = stmt.offset(offset)

        result = await self._session.execute(stmt)
        return result.scalars().all()

    # ------------------------------------------------------------------
    # Write methods
    # ------------------------------------------------------------------

    async def create(self, **kwargs: Any) -> ModelT:
        """Insert a new row, scoped to the current tenant.

        The caller does NOT need to pass ``org_id`` — it is injected automatically
        if the model has an ``org_id`` column.

        Returns the created instance with its database-assigned ``id``.
        """
        # Inject org_id if the model has the column and the caller didn't set it
        org_col = self._org_id_column()
        if org_col is not None and "org_id" not in kwargs:
            kwargs["org_id"] = self._org_id

        instance = self.model_class(**kwargs)  # type: ignore[call-arg]
        self._session.add(instance)
        await self._session.flush()  # assigns id and server defaults
        return instance

    async def update(
        self,
        id: uuid.UUID,
        expected_version: int,
        **kwargs: Any,
    ) -> ModelT:
        """Update a row with optimistic locking.

        :param id: the row's primary key.
        :param expected_version: the ``row_version`` the caller believes is current.
        :param kwargs: the columns to update.
        :raises OptimisticLockError: if ``row_version`` does not match.
        :raises EntityNotFoundError: if the row does not exist or is soft-deleted.
        """
        # Build the UPDATE with org_id + row_version + deleted_at guards
        where_clauses = [
            self.model_class.id == id,  # type: ignore[attr-defined]
        ]

        org_col = self._org_id_column()
        if org_col is not None:
            where_clauses.append(org_col == self._org_id)

        # Only update non-deleted rows
        deleted_at = getattr(self.model_class, "deleted_at", None)
        if deleted_at is not None:
            where_clauses.append(deleted_at.is_(None))

        # Optimistic lock
        row_version_col = getattr(self.model_class, "row_version", None)
        if row_version_col is not None:
            where_clauses.append(row_version_col == expected_version)
            kwargs["row_version"] = expected_version + 1

        # Always bump updated_at
        updated_at = getattr(self.model_class, "updated_at", None)
        if updated_at is not None:
            kwargs["updated_at"] = func.now()

        stmt = (
            update(self.model_class)
            .where(*where_clauses)
            .values(**kwargs)
            .returning(self.model_class)  # type: ignore[arg-type]
        )

        result = await self._session.execute(stmt)
        row = result.scalar_one_or_none()

        if row is None:
            # Distinguish between "not found" and "version mismatch".
            # Check if the row exists at all (ignoring version).
            exists = await self.get_by_id(id)
            if exists is None:
                raise EntityNotFoundError(
                    entity=self.model_class.__tablename__,  # type: ignore[attr-defined]
                    entity_id=id,
                )
            raise OptimisticLockError(
                entity=self.model_class.__tablename__,  # type: ignore[attr-defined]
                entity_id=id,
                expected_version=expected_version,
            )

        return row

    async def soft_delete(self, id: uuid.UUID) -> None:
        """Soft-delete a row by setting ``deleted_at``.

        ADR-004: no hard DELETE in application code. The row stays in the table
        with ``deleted_at`` set; normal reads exclude it.

        :raises EntityNotFoundError: if the row does not exist or is already deleted.
        """
        deleted_at = getattr(self.model_class, "deleted_at", None)
        if deleted_at is None:
            raise TypeError(
                f"{self.model_class.__name__} does not have a deleted_at column "
                f"and cannot be soft-deleted"
            )

        where_clauses = [
            self.model_class.id == id,  # type: ignore[attr-defined]
            deleted_at.is_(None),  # only delete if not already deleted
        ]

        org_col = self._org_id_column()
        if org_col is not None:
            where_clauses.append(org_col == self._org_id)

        stmt = (
            update(self.model_class)
            .where(*where_clauses)
            .values(deleted_at=func.now())
        )

        result = await self._session.execute(stmt)
        if result.rowcount == 0:
            raise EntityNotFoundError(
                entity=self.model_class.__tablename__,  # type: ignore[attr-defined]
                entity_id=id,
            )
