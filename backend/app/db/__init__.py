"""Database layer: declarative base, mixins, async engine, sessions, and repositories.

P1-T3 delivered the base, mixins, and engine.
P1-T5 added the session-context mechanism (``SET LOCAL app.org_id``), the repository
base class (soft-delete, optimistic locking, tenant scoping), the question-bank
repository with lineage rooting, and custom exceptions.
"""

from app.db.base import (
    NAMING_CONVENTION,
    Base,
    RowVersionMixin,
    SoftDeleteMixin,
    TimestampMixin,
    UUIDPrimaryKeyMixin,
    check_in,
    row_version_positive,
)
from app.db.engine import build_engine_kwargs, create_engine, is_transaction_pooled
from app.db.exceptions import (
    EntityNotFoundError,
    OptimisticLockError,
    TenantContextMissing,
)
from app.db.question_repository import QuestionBankRepository
from app.db.repository import BaseRepository
from app.db.session import (
    get_session_factory,
    get_tenant_session,
    get_unscoped_session,
    init_session_factory,
)

__all__ = [
    "NAMING_CONVENTION",
    "Base",
    "BaseRepository",
    "EntityNotFoundError",
    "OptimisticLockError",
    "QuestionBankRepository",
    "RowVersionMixin",
    "SoftDeleteMixin",
    "TenantContextMissing",
    "TimestampMixin",
    "UUIDPrimaryKeyMixin",
    "build_engine_kwargs",
    "check_in",
    "create_engine",
    "get_session_factory",
    "get_tenant_session",
    "get_unscoped_session",
    "init_session_factory",
    "is_transaction_pooled",
    "row_version_positive",
]
