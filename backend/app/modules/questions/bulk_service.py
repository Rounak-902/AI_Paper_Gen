"""Bulk status-transition service for question bank (P2-T5, revised).

Design: scope-aware session for mixed batches
----------------------------------------------
The bulk endpoint uses ``get_global_write_db``, which sets BOTH ``app.org_id``
AND ``app.user_id`` in the transaction-local GUC context.  This is a strict
superset of ``get_db`` (which sets only ``app.org_id``):

* Org-scoped questions (``org_id IS NOT NULL``):  the ``app.org_id`` GUC is
  sufficient for the UPDATE WITH CHECK policy — behaviour is identical to
  ``get_db``.
* Global questions (``org_id IS NULL``):  migration 0031's
  ``global_question_super_admin_update`` policy requires ``app.user_id`` to
  be set AND ``app_is_active_super_admin()`` to return true.  Without
  ``app.user_id`` the DB would silently deny the UPDATE.  With it and a
  non-super_admin caller the DB raises an RLS violation.

Application-layer gate for global questions
-------------------------------------------
Rather than relying on an opaque DB error for the coaching_admin case, the
service explicitly checks each question's scope before dispatching:

1. Peek at the target question (SELECT — always works for both org and global
   via the USING policy's ``org_id IS NULL OR org_id = app.org_id`` clause).
2. If ``org_id IS NULL`` (global question) AND caller is not ``super_admin``:
   return a clear per-row error: "GLOBAL_QUESTION_BULK_ACTION_FORBIDDEN".
   This is a 200 response with an error row, not an HTTP 403.  A coaching_admin
   gets exactly one clear message per question, not an RLS denial that leaks
   nothing useful.
3. If global AND super_admin: proceed.  The ``get_global_write_db`` session
   already has ``app.user_id`` set, so the DB UPDATE will pass RLS.

Mixed batches
-------------
A single bulk call may include both org-scoped and global questions.  Org
questions proceed regardless of the caller's role (as long as they pass the
base ``coaching_admin/super_admin`` gate at the router level).  Global
questions additionally require ``super_admin``.

BR-8 on bulk approve
--------------------
Approving a question that is already ``approved`` raises a ``ValidationError``
("Only draft questions can be approved") from the repository.  This becomes a
per-row error.  The entire batch continues — one bad question does not abort
others.

Partial success
---------------
Every question is processed independently.  All errors are per-row (status
``"error"``), never HTTP-level exceptions that would abort the batch.
"""

from __future__ import annotations

import logging
import uuid
from typing import Optional

from sqlalchemy.ext.asyncio import AsyncSession

from app.common.audit_log import record_audit
from app.common.deps import RequestScope
from app.common.errors import AuthorizationError, ConflictError, NotFoundError, ValidationError
from app.db.exceptions import OptimisticLockError
from app.modules.questions import service as question_service
from app.modules.questions import schemas as question_schemas
from app.modules.questions.import_schemas import (
    BulkAction,
    BulkActionIn,
    BulkActionResultOut,
    BulkActionRowResultOut,
)
from app.modules.questions.repository import QuestionRepository
from app.modules.questions.service import assert_path_org

logger = logging.getLogger("papergen.questions.bulk")

_ADMIN_ROLES = frozenset({"coaching_admin", "super_admin"})
_SUPER_ADMIN_ONLY = frozenset({"super_admin"})


async def bulk_action(
    *,
    session: AsyncSession,
    scope: RequestScope,
    org_id: uuid.UUID,
    payload: BulkActionIn,
    ip: Optional[str] = None,
) -> BulkActionResultOut:
    """Execute a bulk approve / reject / archive on a list of question IDs.

    The ``session`` MUST come from ``get_global_write_db`` (not ``get_db``),
    because the batch may contain global-scope questions that require
    ``app.user_id`` to be set in the transaction GUC context.  Org-scoped
    questions work identically through ``get_global_write_db`` because it also
    sets ``app.org_id``.

    Authorization
    -------------
    Base: ``coaching_admin`` or ``super_admin`` (enforced at the router level
    via ``require_roles``).

    Per-question additional gate: if the target question has ``org_id IS NULL``
    (global scope), only ``super_admin`` may act on it.  A ``coaching_admin``
    gets a clear ``"GLOBAL_QUESTION_BULK_ACTION_FORBIDDEN"`` per-row error,
    not a confusing DB denial.

    Mixed batches
    -------------
    Org-scoped and global questions may appear in the same batch.  Each
    question is processed independently; a global-question denial does not
    abort org-scoped items that follow it.
    """
    assert_path_org(scope, org_id)
    if scope.role not in _ADMIN_ROLES:
        raise AuthorizationError(
            "Only coaching_admin or super_admin may perform bulk question operations."
        )

    action = payload.action
    results: list[BulkActionRowResultOut] = []

    for question_id in payload.question_ids:
        row_version = payload.row_versions.get(str(question_id))
        if row_version is None:
            results.append(
                BulkActionRowResultOut(
                    question_id=question_id,
                    status="error",
                    error_reason=f"row_versions missing entry for {question_id}",
                )
            )
            continue

        row_result = await _process_one_bulk_row(
            session=session,
            scope=scope,
            org_id=org_id,
            action=action,
            question_id=question_id,
            row_version=row_version,
            ip=ip,
        )
        results.append(row_result)

    ok_count = sum(1 for r in results if r.status == "ok")
    error_count = sum(1 for r in results if r.status == "error")

    logger.info(
        "bulk_action_complete org_id=%s action=%s ok=%d errors=%d",
        org_id,
        action,
        ok_count,
        error_count,
    )

    return BulkActionResultOut(
        action=action,
        ok_count=ok_count,
        error_count=error_count,
        results=results,
    )


async def _process_one_bulk_row(
    *,
    session: AsyncSession,
    scope: RequestScope,
    org_id: uuid.UUID,
    action: BulkAction,
    question_id: uuid.UUID,
    row_version: int,
    ip: Optional[str],
) -> BulkActionRowResultOut:
    """Attempt a single status transition, returning an outcome row.

    Scope-awareness: peeks at the question's ``org_id`` before dispatching.
    If ``org_id IS NULL`` (global question) and caller is not super_admin,
    returns a clear per-row error instead of letting the DB raise an opaque
    RLS violation.

    Never raises — all exceptions are caught and returned as ``error`` rows.
    """
    try:
        # --- Step 1: Peek at the question ---
        repo = QuestionRepository(session, org_id)
        try:
            q = await repo.get_question_unscoped(question_id)
        except Exception:
            q = None
        if q is None:
            return BulkActionRowResultOut(
                question_id=question_id,
                status="error",
                error_reason="question not found",
            )

        # --- Step 2: Global-question super_admin gate (application-layer) ---
        # This fires BEFORE any DB write, giving a clear, actionable message
        # instead of an opaque RLS denial.
        is_global = q.org_id is None
        if is_global and scope.role not in _SUPER_ADMIN_ONLY:
            logger.info(
                "bulk_action_global_forbidden question_id=%s role=%s action=%s",
                question_id,
                scope.role,
                action,
            )
            return BulkActionRowResultOut(
                question_id=question_id,
                status="error",
                error_reason=(
                    "GLOBAL_QUESTION_BULK_ACTION_FORBIDDEN: only super_admin may "
                    "bulk-approve/reject/archive global-scope questions. "
                    "Use the individual endpoint instead, or switch to super_admin role."
                ),
            )

        # --- Step 3: Row-version check ---
        if q.row_version != row_version:
            return BulkActionRowResultOut(
                question_id=question_id,
                status="error",
                error_reason=(
                    f"optimistic lock conflict: expected row_version={row_version}, "
                    f"got {q.row_version}"
                ),
            )

        # --- Step 4: Status guard ---
        # Only draft questions can be bulk-acted on.  Already-approved,
        # rejected, or archived questions are per-row errors (non-fatal).
        if q.status != "draft":
            return BulkActionRowResultOut(
                question_id=question_id,
                status="error",
                error_reason=(
                    f"only draft questions can be bulk {action!r} "
                    f"(current status: {q.status!r})"
                ),
            )

        # --- Step 5: Dispatch ---
        if action == "approve":
            return await _do_approve(
                session=session,
                scope=scope,
                org_id=org_id,
                question_id=question_id,
                row_version=row_version,
                ip=ip,
                q=q,
            )
        elif action in ("reject", "archive"):
            target_status = "rejected" if action == "reject" else "archived"
            return await _do_status_update(
                session=session,
                scope=scope,
                org_id=org_id,
                question_id=question_id,
                row_version=row_version,
                target_status=target_status,
                audit_action=f"question.{target_status}",
                ip=ip,
            )
        else:
            return BulkActionRowResultOut(
                question_id=question_id,
                status="error",
                error_reason=f"unknown action: {action!r}",
            )

    except Exception as exc:
        logger.warning(
            "bulk_action_row_error question_id=%s action=%s: %s",
            question_id,
            action,
            exc,
            exc_info=True,
        )
        return BulkActionRowResultOut(
            question_id=question_id,
            status="error",
            error_reason=f"{type(exc).__name__}: {exc}",
        )


async def _do_approve(
    *,
    session: AsyncSession,
    scope: RequestScope,
    org_id: uuid.UUID,
    question_id: uuid.UUID,
    row_version: int,
    ip: Optional[str],
    q: object,
) -> BulkActionRowResultOut:
    """Approve via the existing service path."""
    approve_payload = question_schemas.QuestionApproveIn(row_version=row_version)
    try:
        result = await question_service.approve_question(
            session=session,
            scope=scope,
            org_id=org_id,
            question_id=question_id,
            payload=approve_payload,
            ip=ip,
        )
        return BulkActionRowResultOut(
            question_id=question_id,
            status="ok",
            new_status=result.status,
        )
    except NotFoundError as exc:
        return BulkActionRowResultOut(
            question_id=question_id,
            status="error",
            error_reason=f"not found: {exc}",
        )
    except ConflictError as exc:
        return BulkActionRowResultOut(
            question_id=question_id,
            status="error",
            error_reason=f"optimistic lock conflict: {exc}",
        )
    except ValidationError as exc:
        # e.g. "Only draft questions can be approved" — BR-8
        return BulkActionRowResultOut(
            question_id=question_id,
            status="error",
            error_reason=str(exc),
        )
    except AuthorizationError as exc:
        return BulkActionRowResultOut(
            question_id=question_id,
            status="error",
            error_reason=f"authorization: {exc}",
        )


async def _do_status_update(
    *,
    session: AsyncSession,
    scope: RequestScope,
    org_id: uuid.UUID,
    question_id: uuid.UUID,
    row_version: int,
    target_status: str,
    audit_action: str,
    ip: Optional[str],
) -> BulkActionRowResultOut:
    """Reject or archive via direct repository UPDATE.

    The pre-flight checks (question existence, status='draft') have already been
    performed in _process_one_bulk_row.  This function performs the UPDATE using
    ``transition_status_unscoped()`` — NOT BaseRepository.update() — because
    BaseRepository.update() hard-filters ``WHERE org_id = :org_id``, which silently
    excludes global questions (org_id IS NULL).  transition_status_unscoped() uses
    ``WHERE (org_id = :org_id OR org_id IS NULL)`` and keeps ``row_version = :v``
    in the WHERE clause for optimistic locking.
    """
    repo = QuestionRepository(session, org_id)
    try:
        updated = await repo.transition_status_unscoped(
            question_id, row_version, target_status
        )
    except OptimisticLockError as exc:
        return BulkActionRowResultOut(
            question_id=question_id,
            status="error",
            error_reason=f"optimistic lock conflict: {exc}",
        )
    except Exception as exc:
        return BulkActionRowResultOut(
            question_id=question_id,
            status="error",
            error_reason=f"{type(exc).__name__}: {exc}",
        )

    record_audit(
        session,
        org_id=org_id,
        actor_id=scope.user_id,
        action=audit_action,
        entity="question_bank",
        entity_id=question_id,
        meta={"bulk": True},
        ip=ip,
    )
    await session.refresh(updated)

    return BulkActionRowResultOut(
        question_id=question_id,
        status="ok",
        new_status=updated.status,
    )
