"""SQLAlchemy models, Alembic migrations, RLS policies, and seeds (``packages/db``).

doc 07 §3 places the database layer at ``backend/packages/db/``. Following the
precedent already set by ``packages/config`` in P1-T1/P1-T2, this package is a thin
re-export shim over the implementation in ``app/`` so that there is exactly one
definition of every model. The Alembic migration tree lives beneath this package at
``packages/db/migrations/``.
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
from app.models import (
    BRANCH_STATUSES,
    MEMBERSHIP_ROLES,
    MEMBERSHIP_STATUSES,
    ORG_STATUSES,
    ORG_TYPES,
    RESERVED_MEMBERSHIP_ROLES,
    USER_STATUSES,
    Branch,
    Membership,
    Organization,
    User,
)

__all__ = [
    "BRANCH_STATUSES",
    "MEMBERSHIP_ROLES",
    "MEMBERSHIP_STATUSES",
    "NAMING_CONVENTION",
    "ORG_STATUSES",
    "ORG_TYPES",
    "RESERVED_MEMBERSHIP_ROLES",
    "USER_STATUSES",
    "Base",
    "Branch",
    "Membership",
    "Organization",
    "RowVersionMixin",
    "SoftDeleteMixin",
    "TimestampMixin",
    "UUIDPrimaryKeyMixin",
    "User",
    "build_engine_kwargs",
    "check_in",
    "create_engine",
    "is_transaction_pooled",
    "row_version_positive",
]
