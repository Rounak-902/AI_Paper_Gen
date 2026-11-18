"""Custom exceptions for the database/repository layer (P1-T5).

These are raised by the repository base class and the session-context mechanism.
They are application errors, not HTTP errors — the mapping to HTTP status codes
belongs in the error handlers (P1-T6), not here.

Sources:
* ``TenantContextMissing`` — doc 07 §2.9: "Every authenticated request resolves
  an organization context from the JWT. Every database query carries ``org_id``."
  A missing org context is a programming bug (the guard chain failed to run),
  not an end-user error.
* ``OptimisticLockError`` — doc 07 §12.2: "``row_version`` check in UPDATE WHERE
  clause; 409 on mismatch." The doc cites ``ConflictError(code="CONCURRENT_MODIFICATION",
  eh_ref="EH-18")``.
* ``EntityNotFoundError`` — the repository returns None on a miss, but callers
  that expect exactly one row should get a clear error rather than an AttributeError
  on None.
"""


class TenantContextMissing(RuntimeError):
    """Raised when a tenant-scoped operation is attempted without an org context.

    This is a **500-level error**, not a 403: the guard chain should have resolved
    the org context before the code reached a repository. If this fires in
    production, the dependency chain has a hole.
    """

    def __init__(self, detail: str = "No tenant context (org_id) was provided") -> None:
        super().__init__(detail)
        self.detail = detail


class IdentityContextMissing(TenantContextMissing):
    """Raised when an identity-scoped operation is attempted without a user id (P1-T8).

    The sibling of :class:`TenantContextMissing` for ``app.user_id``, raised by
    ``app/db/session.py``'s ``get_identity_session``. Same severity and the same
    reasoning: the caller of an identity session has already been through
    ``get_current_user``, so a missing user id means the guard chain was bypassed,
    which is a 500 and not a 403.

    Deliberately a **subclass** rather than a peer. ``app/common/errors.py``
    registers a handler keyed on ``TenantContextMissing``, and Starlette resolves
    handlers by walking ``type(exc).__mro__``, so this inherits the existing 500
    mapping with no edit to the error handlers. A peer class would have needed one
    and would have been a silently-unhandled exception until somebody noticed.
    """

    def __init__(
        self, detail: str = "No identity context (user_id) was provided"
    ) -> None:
        super().__init__(detail)


class OptimisticLockError(Exception):
    """Raised when a write fails because ``row_version`` does not match.

    Doc 07 §12.2: maps to HTTP 409 (Conflict) with ``eh_ref="EH-18"``.

    Attributes:
        entity: the table/model name, for diagnostics.
        entity_id: the row that was being updated.
        expected_version: the version the caller thought the row had.
    """

    def __init__(
        self,
        *,
        entity: str,
        entity_id: object,
        expected_version: int,
    ) -> None:
        super().__init__(
            f"Concurrent modification on {entity} id={entity_id}: "
            f"expected row_version={expected_version}, row was already changed"
        )
        self.entity = entity
        self.entity_id = entity_id
        self.expected_version = expected_version


class EntityNotFoundError(Exception):
    """Raised when a scoped lookup returns no rows.

    Attributes:
        entity: the table/model name.
        entity_id: the id that was looked up.
    """

    def __init__(self, *, entity: str, entity_id: object) -> None:
        super().__init__(f"{entity} id={entity_id} not found")
        self.entity = entity
        self.entity_id = entity_id
