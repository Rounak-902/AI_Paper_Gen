"""Question service (P2-T3 CRUD/versioning + P2-T4 dedup + P2-T6 search).

Orchestrates CRUD, BR-8 fork-on-edit, role-based scope enforcement, and the
P2-T4 duplicate response contract.
Services never write raw SQL or touch the SQLAlchemy session directly
(doc 07 §2.4).

Fork-on-edit decision
---------------------
``update()`` inspects the current question's status:

* ``draft``   → update in place via ``repository.update_question()``
* ``approved`` → fork via ``repository.fork()`` → new draft row in the lineage

The approved row's content stays untouched (the trigger enforces this at DB
level too). Only its ``is_current`` flag flips to ``False`` in the same flush.

Scope narrowing (P2-T2 carry-forward)
--------------------------------------
``RequestScope.subject_ids`` is populated by ``get_scope`` in ``deps.py`` for
teacher-role callers. This service passes it through to every repository
read that performs visibility filtering. Admins and super_admins carry
``subject_ids=None``, which disables the filter.
"""

from __future__ import annotations

import logging
import uuid
from typing import Any, Optional

from sqlalchemy.exc import IntegrityError, ProgrammingError
from sqlalchemy.ext.asyncio import AsyncSession

from app.common.audit_log import record_audit
from app.common.deps import RequestScope
from app.common.envelope import ErrorDetail
from app.common.errors import (
    AuthorizationError,
    ConflictError,
    NotFoundError,
    ValidationError,
)
from app.core.config import get_settings
from app.db.exceptions import EntityNotFoundError, OptimisticLockError
from app.modules.questions import schemas
from app.modules.questions.repository import (
    DuplicateQuestionPair,
    ExactDuplicateQuestionError,
    NearDuplicateMatch,
    QuestionLexicalScore,
    QuestionRepository,
)

logger = logging.getLogger("papergen.questions.service")


class QuestionExactDuplicateConflict(ConflictError):
    """409 for a first-version insert colliding with an existing question."""

    code = "QUESTION_EXACT_DUPLICATE"
    eh_ref = None


class GlobalQuestionCreationForbidden(AuthorizationError):
    """403 for an actor that cannot use the platform-global write path."""

    code = "GLOBAL_QUESTION_CREATION_FORBIDDEN"


def _is_question_bank_rls_denial(exc: ProgrammingError) -> bool:
    """Recognise only PostgreSQL's question-bank RLS denial.

    SQLAlchemy's asyncpg dialect wraps ``insufficient_privilege`` (42501) as a
    ``ProgrammingError``.  SQLSTATE by itself is broader than RLS (it also covers
    ordinary GRANT failures), so both the stable state and PostgreSQL's table-specific
    row-policy text are required.  All other programming errors keep propagating to
    the global 500 handler instead of being mislabeled as authorization failures.
    """

    original = exc.orig
    sqlstate = getattr(original, "sqlstate", None) or getattr(original, "pgcode", None)
    message = str(original).lower()
    return (
        sqlstate == "42501"
        and "row-level security policy" in message
        and "question_bank" in message
    )


def assert_path_org(ctx: RequestScope, path_org_id: uuid.UUID) -> None:
    """Verify the path org_id matches the token's active org.

    Accepts RequestScope (which has org_id, user_id, role like OrgContext).
    """
    if path_org_id != ctx.org_id:
        logger.warning(
            "path_org_mismatch user_id=%s token_org=%s path_org=%s",
            ctx.user_id,
            ctx.org_id,
            path_org_id,
        )
        raise AuthorizationError(
            "You do not have access to this resource.",
            log_detail=(
                f"path org {path_org_id} does not match token activeOrgId "
                f"{ctx.org_id} for user {ctx.user_id}"
            ),
        )


def _question_out(q: Any) -> schemas.QuestionOut:
    return schemas.QuestionOut.model_validate(q)


def _question_create_out(
    q: Any,
    matches: tuple[NearDuplicateMatch, ...],
    threshold: float,
) -> schemas.QuestionCreateOut:
    warning_list: list[schemas.QuestionCreateWarning] = []
    if matches:
        candidates = [
            schemas.NearDuplicateCandidate(
                existing_question_id=match.question.id,
                root_question_id=match.question.root_question_id,
                qtype=match.question.qtype,
                scope=match.question.scope,
                stem=match.question.stem,
                similarity=round(match.similarity, 6),
            )
            for match in matches
        ]
        count = len(candidates)
        warning_list.append(
            schemas.QuestionCreateWarning(
                message=(
                    f"{count} similar existing question"
                    f"{'s' if count != 1 else ''} found; review before approval."
                ),
                threshold=threshold,
                candidates=candidates,
            )
        )

    return schemas.QuestionCreateOut(
        **schemas.QuestionOut.model_validate(q).model_dump(),
        warnings=warning_list,
    )


def _resolve_scope_and_org(
    scope: RequestScope,
    requested_scope: Optional[str],
) -> tuple[str, Optional[uuid.UUID]]:
    """Determine the scope and org_id for a new question.

    * super_admin may create 'global' questions (org_id=None).
    * All other roles create 'org' questions with their org_id.
    * If the caller does not specify scope, defaults to 'org'.
    """
    if requested_scope == "global":
        if scope.role != "super_admin":
            raise GlobalQuestionCreationForbidden(
                "Only super_admin may create global questions.",
                log_detail=f"role={scope.role} attempted to create a global question",
            )
        return "global", None
    return "org", scope.org_id


async def create_question(
    *,
    session: AsyncSession,
    scope: RequestScope,
    org_id: uuid.UUID,
    payload: schemas.QuestionCreateIn,
    ip: Optional[str] = None,
) -> schemas.QuestionCreateOut:
    """Create a first-version question."""
    assert_path_org(scope, org_id)

    q_scope, q_org_id = _resolve_scope_and_org(scope, payload.scope)
    near_duplicate_threshold = get_settings().DEDUP_REVIEW_THRESHOLD

    repo = QuestionRepository(session, scope.org_id)
    try:
        create_result = await repo.create_question(
            qtype=payload.qtype,
            marks=payload.marks,
            difficulty=payload.difficulty,
            language=payload.language,
            stem=payload.stem,
            source=payload.source,
            scope=q_scope,
            org_id=q_org_id,
            created_by=scope.user_id,
            bloom=payload.bloom,
            board_id=payload.board_id,
            class_id=payload.class_id,
            subject_id=payload.subject_id,
            chapter_id=payload.chapter_id,
            options=payload.options,
            answer=payload.answer,
            rubric=payload.rubric,
            learning_outcome_ids=payload.learning_outcome_ids or [],
            competency_ids=payload.competency_ids or [],
            meta=payload.meta or {},
            prompt_version_id=payload.prompt_version_id,
            near_duplicate_threshold=near_duplicate_threshold,
        )
        question = create_result.question
    except ExactDuplicateQuestionError as exc:
        existing = exc.existing_question
        logger.info(
            "question exact duplicate rejected existing_question_id=%s "
            "content_hash=%s",
            existing.id,
            existing.content_hash,
        )
        raise QuestionExactDuplicateConflict(
            (
                "An exact duplicate question already exists "
                f"(question_id={existing.id})."
            ),
            details=[
                ErrorDetail(
                    field="existingQuestionId",
                    issue=str(existing.id),
                ),
                ErrorDetail(
                    field="existingRootQuestionId",
                    issue=str(existing.root_question_id or existing.id),
                ),
            ],
            log_detail=(
                f"content_hash={existing.content_hash} collided with "
                f"question_id={existing.id}"
            ),
        ) from exc
    except IntegrityError as exc:
        logger.warning("question create integrity error: %s", exc)
        raise ConflictError(
            "Could not create question (constraint violation).",
            log_detail=f"question create constraint violation: {exc.orig}",
        ) from exc
    except ProgrammingError as exc:
        if q_scope != "global" or not _is_question_bank_rls_denial(exc):
            raise
        logger.warning(
            "global question creation denied by database policy user_id=%s org_id=%s",
            scope.user_id,
            scope.org_id,
        )
        raise GlobalQuestionCreationForbidden(
            "Global question creation was denied by the platform authorization policy.",
            log_detail=(
                "question_bank RLS rejected scope=global for "
                f"user_id={scope.user_id} org_id={scope.org_id}"
            ),
        ) from exc

    record_audit(
        session,
        org_id=scope.org_id,
        actor_id=scope.user_id,
        action="question.created",
        entity="question_bank",
        entity_id=question.id,
        meta={
            "qtype": payload.qtype,
            "scope": q_scope,
            "nearDuplicateCount": len(create_result.near_duplicates),
            "nearDuplicateThreshold": near_duplicate_threshold,
        },
        ip=ip,
    )
    await session.refresh(question)
    return _question_create_out(
        question,
        create_result.near_duplicates,
        near_duplicate_threshold,
    )


async def get_question(
    *,
    session: AsyncSession,
    scope: RequestScope,
    org_id: uuid.UUID,
    question_id: uuid.UUID,
) -> schemas.QuestionOut:
    """Fetch a single question, respecting teacher scope narrowing."""
    assert_path_org(scope, org_id)
    repo = QuestionRepository(session, scope.org_id)
    q = await repo.get_question(question_id, subject_ids=scope.subject_ids)
    if q is None:
        raise NotFoundError("Question not found.")
    return _question_out(q)


async def list_questions(
    *,
    session: AsyncSession,
    scope: RequestScope,
    org_id: uuid.UUID,
    params: schemas.QuestionSearchParams,
) -> tuple[list[schemas.QuestionOut], int]:
    """List questions with optional full-text search and facet filters."""
    assert_path_org(scope, org_id)
    repo = QuestionRepository(session, scope.org_id)

    result = await repo.search_questions(
        query_text=params.q,
        subject_ids=scope.subject_ids,
        board_id=params.board_id,
        class_id=params.class_id,
        subject_id=params.subject_id,
        chapter_id=params.chapter_id,
        qtype=params.qtype,
        difficulty=params.difficulty,
        bloom=params.bloom,
        source=params.source,
        status=params.status,
        scope=params.scope,
        current_only=params.current_only,
        limit=params.page_size,
        offset=params.offset,
    )
    return [_question_out(question) for question in result.questions], result.total


async def retrieval_metadata_candidates(
    *,
    session: AsyncSession,
    scope: RequestScope,
    org_id: uuid.UUID,
    board_id: uuid.UUID,
    class_id: uuid.UUID,
    source_subject_ids: tuple[uuid.UUID, ...],
    chapter_ids: tuple[uuid.UUID, ...],
    learning_outcome_ids: tuple[uuid.UUID, ...],
    qtype: str,
    marks: Any,
    difficulty: str,
    bloom: Optional[str],
    allowed_sources: Optional[tuple[str, ...]],
    question_scope: Optional[str],
    required_meta_key: Optional[str] = None,
    excluded_question_ids: tuple[uuid.UUID, ...] = (),
) -> tuple[schemas.QuestionOut, ...]:
    """Expose P2's scope/facet read path to the retrieval module.

    This is intentionally a read-only service boundary.  Retrieval does not import
    the Questions repository directly, and teacher narrowing therefore has exactly
    the same ``RequestScope.subject_ids`` semantics as every P2 question read.
    ``search_questions(query_text=None)`` is used because metadata and lexical
    scoring are separate pipeline stages in P3.
    """

    assert_path_org(scope, org_id)
    repo = QuestionRepository(session, scope.org_id)
    excluded = set(excluded_question_ids)
    candidates: dict[uuid.UUID, schemas.QuestionOut] = {}
    result = await repo.search_questions(
        query_text=None,
        # The retrieval adapter has already authorized the requested current
        # subject, then expanded this tuple only to its confirmed chapter-lineage
        # ancestors. Passing it through P2's canonical subject scope predicate
        # keeps inherited questions visible without opening any unrelated subject.
        subject_ids=source_subject_ids,
        board_id=board_id,
        class_id=class_id,
        chapter_ids=chapter_ids,
        learning_outcome_ids=(learning_outcome_ids or None),
        qtype=qtype,
        marks=marks,
        difficulty=difficulty,
        bloom=bloom,
        sources=allowed_sources,
        required_meta_key=required_meta_key,
        status="approved",
        scope=question_scope,
        current_only=True,
        limit=None,
        calculate_total=False,
    )
    for question in result.questions:
        if question.id not in excluded:
            candidates[question.id] = _question_out(question)

    # P2's fallback order is recency; UUID makes ties deterministic.  Hybrid rank
    # replaces this order later, so it is only a stable hand-off between stages.
    return tuple(
        sorted(
            candidates.values(),
            key=lambda question: (question.created_at, str(question.id)),
            reverse=True,
        )
    )


async def retrieval_lexical_scores(
    *,
    session: AsyncSession,
    scope: RequestScope,
    org_id: uuid.UUID,
    question_ids: tuple[uuid.UUID, ...],
    query_text: Optional[str],
) -> tuple[QuestionLexicalScore, ...]:
    """Return P2-T6 FTS and P2-T4 trigram scores for visible candidates."""

    assert_path_org(scope, org_id)
    repo = QuestionRepository(session, scope.org_id)
    return await repo.score_lexical_candidates(
        question_ids=question_ids,
        query_text=query_text,
        subject_ids=scope.subject_ids,
    )


async def retrieval_duplicate_pairs(
    *,
    session: AsyncSession,
    scope: RequestScope,
    org_id: uuid.UUID,
    question_ids: tuple[uuid.UUID, ...],
    near_duplicate_threshold: float,
) -> tuple[DuplicateQuestionPair, ...]:
    """Return P2-T4 exact/trigram duplicate edges for visible candidates."""

    assert_path_org(scope, org_id)
    repo = QuestionRepository(session, scope.org_id)
    return await repo.find_duplicate_candidate_pairs(
        question_ids=question_ids,
        near_duplicate_threshold=near_duplicate_threshold,
        subject_ids=scope.subject_ids,
    )


async def update_question(
    *,
    session: AsyncSession,
    scope: RequestScope,
    org_id: uuid.UUID,
    question_id: uuid.UUID,
    payload: schemas.QuestionUpdateIn,
    ip: Optional[str] = None,
) -> schemas.QuestionOut:
    """Update a question — in-place for drafts, fork for approved.

    BR-8: editing an approved question INSERTs a new version (fork) and flips
    the old version's ``is_current`` to False. The new version enters as draft.
    """
    assert_path_org(scope, org_id)
    repo = QuestionRepository(session, scope.org_id)

    # Fetch without subject scope (the question may belong to a subject the
    # teacher is about to edit; the scope check happens at list/read, not update)
    q = await repo.get_question_unscoped(question_id)
    if q is None:
        raise NotFoundError("Question not found.")

    # Build the set of changes from the PATCH payload
    changes: dict[str, Any] = {}
    for field in (
        "stem", "options", "answer", "rubric", "marks",
        "difficulty", "bloom", "language",
        "board_id", "class_id", "subject_id", "chapter_id",
        "learning_outcome_ids", "competency_ids", "meta",
    ):
        val = getattr(payload, field, None)
        if val is not None:
            changes[field] = val

    if not changes:
        raise ValidationError(
            "Provide at least one field to update.",
            log_detail=f"empty PATCH body for question {question_id}",
        )

    try:
        if q.status == "approved":
            # BR-8: fork on edit
            new_q = await repo.fork(
                q,
                caller_version=payload.row_version,
                created_by=scope.user_id,
                **changes,
            )
            record_audit(
                session,
                org_id=scope.org_id,
                actor_id=scope.user_id,
                action="question.forked",
                entity="question_bank",
                entity_id=new_q.id,
                meta={
                    "previousVersionId": str(q.id),
                    "rootQuestionId": str(new_q.root_question_id),
                    "versionNumber": new_q.version_number,
                    "changedFields": sorted(changes.keys()),
                },
                ip=ip,
            )
            await session.refresh(new_q)
            return _question_out(new_q)
        else:
            # Draft: in-place update
            updated_q = await repo.update_question(
                question_id, payload.row_version, **changes
            )
            record_audit(
                session,
                org_id=scope.org_id,
                actor_id=scope.user_id,
                action="question.updated",
                entity="question_bank",
                entity_id=question_id,
                meta={"changedFields": sorted(changes.keys())},
                ip=ip,
            )
            await session.refresh(updated_q)
            return _question_out(updated_q)

    except OptimisticLockError as exc:
        raise ConflictError(
            "This record was modified by someone else. Reload and try again."
        ) from exc
    except IntegrityError as exc:
        raise ConflictError(
            "Could not save changes (constraint violation).",
            log_detail=str(exc.orig),
        ) from exc


async def approve_question(
    *,
    session: AsyncSession,
    scope: RequestScope,
    org_id: uuid.UUID,
    question_id: uuid.UUID,
    payload: schemas.QuestionApproveIn,
    ip: Optional[str] = None,
) -> schemas.QuestionOut:
    """Approve a draft question (coaching_admin only)."""
    assert_path_org(scope, org_id)
    if scope.role not in ("coaching_admin", "super_admin"):
        raise AuthorizationError("Only coaching_admin or super_admin may approve questions.")

    repo = QuestionRepository(session, scope.org_id)
    try:
        q = await repo.approve_question(question_id, payload.row_version)
    except EntityNotFoundError:
        raise NotFoundError("Question not found.")
    except OptimisticLockError as exc:
        raise ConflictError(
            "This record was modified by someone else. Reload and try again."
        ) from exc
    except ValueError as exc:
        raise ValidationError(str(exc)) from exc

    record_audit(
        session,
        org_id=scope.org_id,
        actor_id=scope.user_id,
        action="question.approved",
        entity="question_bank",
        entity_id=question_id,
        meta={},
        ip=ip,
    )
    await session.refresh(q)
    return _question_out(q)


async def delete_question(
    *,
    session: AsyncSession,
    scope: RequestScope,
    org_id: uuid.UUID,
    question_id: uuid.UUID,
    row_version: int,
    ip: Optional[str] = None,
) -> None:
    """Soft-delete a question (ADR-004)."""
    assert_path_org(scope, org_id)
    if scope.role not in ("coaching_admin", "super_admin"):
        raise AuthorizationError("Only coaching_admin or super_admin may delete questions.")

    repo = QuestionRepository(session, scope.org_id)
    try:
        await repo.soft_delete_question(question_id, row_version)
    except EntityNotFoundError:
        raise NotFoundError("Question not found.")
    except OptimisticLockError as exc:
        raise ConflictError(
            "This record was modified by someone else. Reload and try again."
        ) from exc

    record_audit(
        session,
        org_id=scope.org_id,
        actor_id=scope.user_id,
        action="question.deleted",
        entity="question_bank",
        entity_id=question_id,
        meta={},
        ip=ip,
    )


async def get_lineage(
    *,
    session: AsyncSession,
    scope: RequestScope,
    org_id: uuid.UUID,
    question_id: uuid.UUID,
) -> list[schemas.QuestionOut]:
    """Return the full version history for a question's lineage."""
    assert_path_org(scope, org_id)
    repo = QuestionRepository(session, scope.org_id)

    # Resolve the requested row through the same teacher-scope filter as direct
    # question detail.  Lineage is a read surface, not an internal write helper.
    q = await repo.get_question(question_id, subject_ids=scope.subject_ids)
    if q is None:
        raise NotFoundError("Question not found.")

    root_id = q.root_question_id or q.id
    versions = await repo.get_lineage(root_id, subject_ids=scope.subject_ids)
    return [_question_out(v) for v in versions]
