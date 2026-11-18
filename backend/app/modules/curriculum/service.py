"""Curriculum business rules for the global, versioned reference catalog.

Rows in this module are shared across tenants.  ``OrgContext`` is still required
for authentication, authorization and audit attribution, but it is deliberately
not a filtering key.  Global mutations are role-gated in :mod:`router`.
"""

from __future__ import annotations

import uuid
from typing import Any, Optional

from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from app.common.audit_log import ACTIONS, record_audit
from app.common.deps import OrgContext
from app.common.errors import (
    BusinessRuleError,
    ConflictError,
    NotFoundError,
    ValidationError,
)
from app.modules.curriculum import schemas
from app.modules.curriculum.repository import (
    BoardRepository,
    ChapterRepository,
    ClassRepository,
    CurriculumVersionRepository,
    OutcomeRepository,
    SubjectRepository,
)

# ---------------------------------------------------------------------------
# DTO conversion
# ---------------------------------------------------------------------------
def _board_out(row: Any) -> schemas.BoardOut:
    return schemas.BoardOut(
        id=row.id,
        code=row.code,
        name=row.name,
        row_version=row.row_version,
        created_at=row.created_at,
        updated_at=row.updated_at,
    )


def _class_out(row: Any) -> schemas.ClassOut:
    return schemas.ClassOut(
        id=row.id,
        board_id=row.board_id,
        name=row.name,
        row_version=row.row_version,
        created_at=row.created_at,
        updated_at=row.updated_at,
    )


def _subject_out(row: Any) -> schemas.SubjectOut:
    return schemas.SubjectOut(
        id=row.id,
        class_id=row.class_id,
        curriculum_version_id=row.curriculum_version_id,
        name=row.name,
        row_version=row.row_version,
        created_at=row.created_at,
        updated_at=row.updated_at,
    )


def _chapter_out(row: Any) -> schemas.ChapterOut:
    return schemas.ChapterOut(
        id=row.id,
        subject_id=row.subject_id,
        curriculum_version_id=row.curriculum_version_id,
        seq=row.seq,
        title=row.title,
        derived_from_chapter_id=row.derived_from_chapter_id,
        row_version=row.row_version,
        created_at=row.created_at,
        updated_at=row.updated_at,
    )


def _outcome_out(row: Any) -> schemas.OutcomeOut:
    return schemas.OutcomeOut(
        id=row.id,
        chapter_id=row.chapter_id,
        code=row.code,
        description=row.description,
        row_version=row.row_version,
        created_at=row.created_at,
        updated_at=row.updated_at,
    )


def _version_out(row: Any) -> schemas.CurriculumVersionOut:
    return schemas.CurriculumVersionOut(
        id=row.id,
        board_id=row.board_id,
        academic_year=row.academic_year,
        label=row.label,
        status=row.status,
        effective_from=row.effective_from,
        source_ref=row.source_ref,
        row_version=row.row_version,
        created_at=row.created_at,
        updated_at=row.updated_at,
    )


# ---------------------------------------------------------------------------
# Shared helpers
# ---------------------------------------------------------------------------
async def _require_board(repo: BoardRepository, board_id: uuid.UUID) -> Any:
    row = await repo.get_by_id(board_id)
    if row is None:
        raise NotFoundError(
            "Board not found.", log_detail=f"global board {board_id} is absent or deleted"
        )
    return row


async def _require_class(repo: ClassRepository, class_id: uuid.UUID) -> Any:
    row = await repo.get_by_id(class_id)
    if row is None:
        raise NotFoundError(
            "Class not found.", log_detail=f"global class {class_id} is absent or deleted"
        )
    return row


async def _require_subject(repo: SubjectRepository, subject_id: uuid.UUID) -> Any:
    row = await repo.get_by_id(subject_id)
    if row is None:
        raise NotFoundError(
            "Subject not found.",
            log_detail=f"global subject {subject_id} is absent or deleted",
        )
    return row


async def _require_chapter(repo: ChapterRepository, chapter_id: uuid.UUID) -> Any:
    row = await repo.get_by_id(chapter_id)
    if row is None:
        raise NotFoundError(
            "Chapter not found.",
            log_detail=f"global chapter {chapter_id} is absent or deleted",
        )
    return row


async def _require_version(
    repo: CurriculumVersionRepository, version_id: uuid.UUID
) -> Any:
    row = await repo.get_by_id(version_id)
    if row is None:
        raise NotFoundError(
            "Curriculum version not found.",
            log_detail=f"global curriculum version {version_id} is absent or deleted",
        )
    return row


def _require_changes(payload: Any, *, entity: str) -> dict[str, Any]:
    changes = {
        name: getattr(payload, name)
        for name in payload.model_fields_set
        if name != "row_version"
    }
    if not changes:
        raise ValidationError(
            "Provide at least one field to update.",
            log_detail=f"empty PATCH body for {entity}",
        )
    return changes


def _reject_nulls(
    changes: dict[str, Any], *, fields: tuple[str, ...], entity: str
) -> None:
    invalid = [field for field in fields if field in changes and changes[field] is None]
    if invalid:
        raise ValidationError(
            "Required fields cannot be set to null.",
            log_detail=f"{entity} PATCH set non-null fields to null: {invalid}",
        )


async def _update(
    repo: Any,
    entity_id: uuid.UUID,
    expected_version: int,
    changes: dict[str, Any],
    *,
    entity: str,
) -> Any:
    try:
        return await repo.update(entity_id, expected_version, **changes)
    except IntegrityError as exc:
        raise ConflictError(
            f"That {entity} conflicts with an existing record.",
            log_detail=f"database constraint rejected {entity} {entity_id}: {exc.orig}",
        ) from exc


async def _delete(
    repo: Any,
    entity_id: uuid.UUID,
    expected_version: int,
) -> None:
    # Curriculum repositories add this narrow atomic wrapper around BaseRepository
    # update semantics.  It keeps DELETE's optimistic lock in the same statement as
    # the tombstone and does not modify the protected base class.
    await repo.soft_delete_with_version(entity_id, expected_version)


def _audit(
    session: AsyncSession,
    ctx: OrgContext,
    *,
    action: str,
    entity: str,
    entity_id: uuid.UUID,
    meta: Optional[dict[str, Any]] = None,
    ip: Optional[str] = None,
) -> None:
    """Stage an audit row in the active-org transaction.

    Curriculum is platform-global, but ``app_user`` cannot insert an audit row with
    ``org_id IS NULL`` under the existing RLS policy.  Until platform-scope sessions
    exist, the verified active organization records where the super-admin performed
    the global action.  This is an attribution compromise, not row ownership.
    """

    record_audit(
        session,
        org_id=ctx.org_id,
        actor_id=ctx.user_id,
        action=action,
        entity=entity,
        entity_id=entity_id,
        meta=meta,
        ip=ip,
    )


async def _subject_board_id(
    session: AsyncSession, ctx: OrgContext, subject: Any
) -> uuid.UUID:
    school_class = await _require_class(
        ClassRepository(session, ctx.org_id), subject.class_id
    )
    return school_class.board_id


async def _lock_board(
    session: AsyncSession,
    ctx: OrgContext,
    board_id: uuid.UUID,
) -> None:
    """Take the module's transaction-scoped mutation lock for one board."""

    await BoardRepository(session, ctx.org_id).lock_board(board_id)


async def _require_mutable_version(
    repo: CurriculumVersionRepository,
    version_id: Optional[uuid.UUID],
    *,
    entity: str,
    allow_active_append: bool = False,
) -> Any:
    """Require a version-owned entity to belong to an appendable snapshot.

    Normal CRUD mutations remain draft-only.  The atomic manifest importer may
    explicitly append missing rows to the active snapshot; it never updates or
    deletes existing version-owned rows.
    """

    if version_id is None:
        raise BusinessRuleError(
            "Legacy unversioned curriculum is read-only.",
            code="CURRICULUM_VERSION_REQUIRED",
            log_detail=f"mutation of unversioned {entity} refused",
        )
    version = await _require_version(repo, version_id)
    if version.status != "draft" and not (
        allow_active_append and version.status == "active"
    ):
        raise BusinessRuleError(
            "Published curriculum snapshots are immutable.",
            code="CURRICULUM_SNAPSHOT_IMMUTABLE",
            log_detail=(
                f"mutation of {entity} refused because curriculum version "
                f"{version.id} is {version.status}"
            ),
        )
    return version


async def _lock_and_reload_subject(
    session: AsyncSession,
    ctx: OrgContext,
    subject_id: uuid.UUID,
) -> tuple[Any, Any]:
    """Resolve a subject's board, lock it, then re-read the parent chain."""

    subject_repo = SubjectRepository(session, ctx.org_id)
    class_repo = ClassRepository(session, ctx.org_id)
    subject = await _require_subject(subject_repo, subject_id)
    school_class = await _require_class(class_repo, subject.class_id)
    await _lock_board(session, ctx, school_class.board_id)
    subject = await _require_subject(subject_repo, subject_id)
    school_class = await _require_class(class_repo, subject.class_id)
    return subject, school_class


async def _lock_and_reload_chapter(
    session: AsyncSession,
    ctx: OrgContext,
    chapter_id: uuid.UUID,
) -> tuple[Any, Any, Any]:
    """Resolve a chapter's board, lock it, then re-read the parent chain."""

    chapter_repo = ChapterRepository(session, ctx.org_id)
    chapter = await _require_chapter(chapter_repo, chapter_id)
    original_subject_id = chapter.subject_id
    subject, school_class = await _lock_and_reload_subject(
        session, ctx, original_subject_id
    )
    chapter = await _require_chapter(chapter_repo, chapter_id)
    if chapter.subject_id != original_subject_id:
        raise ConflictError(
            "Chapter placement changed while the request was in progress.",
            log_detail=(
                f"chapter {chapter_id} moved from subject {original_subject_id} "
                f"to {chapter.subject_id} outside the curriculum API"
            ),
        )
    return chapter, subject, school_class


async def _validate_chapter_version_and_lineage(
    *,
    session: AsyncSession,
    ctx: OrgContext,
    subject: Any,
    curriculum_version_id: Optional[uuid.UUID],
    derived_from_chapter_id: Optional[uuid.UUID],
    chapter_id: Optional[uuid.UUID] = None,
    allow_active_append: bool = False,
) -> None:
    version_repo = CurriculumVersionRepository(session, ctx.org_id)
    chapter_repo = ChapterRepository(session, ctx.org_id)
    subject_repo = SubjectRepository(session, ctx.org_id)
    target_board_id = await _subject_board_id(session, ctx, subject)

    if curriculum_version_id != subject.curriculum_version_id:
        raise ValidationError(
            "Chapter and subject must use the same curriculum version.",
            log_detail=(
                f"subject {subject.id} uses {subject.curriculum_version_id}; "
                f"chapter requested {curriculum_version_id}"
            ),
        )
    target_version = await _require_mutable_version(
        version_repo,
        curriculum_version_id,
        entity=f"chapter in subject {subject.id}",
        allow_active_append=allow_active_append,
    )
    if target_version.board_id != target_board_id:
        raise ValidationError(
            "Curriculum version does not belong to the subject's board.",
            log_detail=(
                f"version {target_version.id} board {target_version.board_id}; "
                f"subject {subject.id} board {target_board_id}"
            ),
        )

    if derived_from_chapter_id is None:
        return
    if chapter_id is not None and derived_from_chapter_id == chapter_id:
        raise ValidationError(
            "A chapter cannot derive from itself.",
            log_detail=f"chapter {chapter_id} direct self-lineage refused",
        )

    ancestor = await _require_chapter(chapter_repo, derived_from_chapter_id)
    ancestor_subject = await _require_subject(subject_repo, ancestor.subject_id)
    ancestor_board_id = await _subject_board_id(session, ctx, ancestor_subject)
    if ancestor_board_id != target_board_id:
        raise ValidationError(
            "A derived chapter must remain within the same board.",
            log_detail=(
                f"ancestor {ancestor.id} board {ancestor_board_id}; "
                f"target subject {subject.id} board {target_board_id}"
            ),
        )
    if (
        ancestor_subject.class_id != subject.class_id
        or ancestor_subject.name != subject.name
    ):
        raise ValidationError(
            "A derived chapter must remain in the same class and logical subject.",
            log_detail=(
                f"ancestor subject {ancestor_subject.id} is "
                f"class={ancestor_subject.class_id}, name={ancestor_subject.name!r}; "
                f"target subject {subject.id} is "
                f"class={subject.class_id}, name={subject.name!r}"
            ),
        )
    if ancestor.curriculum_version_id is None:
        raise ValidationError(
            "Chapter lineage requires versioned source and target chapters.",
            log_detail=f"ancestor chapter {ancestor.id} is unversioned",
        )
    if curriculum_version_id == ancestor.curriculum_version_id:
        raise ValidationError(
            "A derived chapter must point to a different curriculum version.",
            log_detail=(
                f"ancestor {ancestor.id} and target both use version "
                f"{curriculum_version_id}"
            ),
        )
    ancestor_version = await _require_version(
        version_repo, ancestor.curriculum_version_id
    )
    if ancestor_version.status != "active":
        raise ValidationError(
            "A derived chapter must point to the active prior snapshot.",
            log_detail=(
                f"ancestor chapter {ancestor.id} uses version {ancestor_version.id} "
                f"with status {ancestor_version.status}"
            ),
        )
    if chapter_id is not None and await chapter_repo.would_create_cycle(
        chapter_id, derived_from_chapter_id
    ):
        raise ValidationError(
            "Chapter lineage cannot contain a cycle.",
            log_detail=(
                f"setting chapter {chapter_id} ancestor to "
                f"{derived_from_chapter_id} creates a cycle"
            ),
        )


# ---------------------------------------------------------------------------
# Boards
# ---------------------------------------------------------------------------
async def create_board(
    *, session: AsyncSession, ctx: OrgContext, payload: schemas.BoardCreateIn,
    ip: Optional[str] = None,
) -> schemas.BoardOut:
    repo = BoardRepository(session, ctx.org_id)
    if await repo.code_exists(payload.code):
        raise ConflictError("A board with that code already exists.")
    try:
        row = await repo.create(code=payload.code, name=payload.name)
    except IntegrityError as exc:
        raise ConflictError("A board with that code already exists.") from exc
    _audit(session, ctx, action=ACTIONS.BOARD_CREATED, entity="boards", entity_id=row.id,
           meta={"code": row.code, "name": row.name}, ip=ip)
    await session.refresh(row)
    return _board_out(row)


async def get_board(*, session: AsyncSession, ctx: OrgContext, board_id: uuid.UUID) -> schemas.BoardOut:
    return _board_out(await _require_board(BoardRepository(session, ctx.org_id), board_id))


async def list_boards(*, session: AsyncSession, ctx: OrgContext, limit: int, offset: int) -> tuple[list[schemas.BoardOut], int]:
    rows = await BoardRepository(session, ctx.org_id).list_boards(limit=limit, offset=offset)
    items = [_board_out(row) for row in rows]
    return items, len(items)


async def update_board(
    *, session: AsyncSession, ctx: OrgContext, board_id: uuid.UUID,
    payload: schemas.BoardUpdateIn, ip: Optional[str] = None,
) -> schemas.BoardOut:
    repo = BoardRepository(session, ctx.org_id)
    before = await _require_board(repo, board_id)
    await _lock_board(session, ctx, board_id)
    before = await _require_board(repo, board_id)
    old_code = before.code
    changes = _require_changes(payload, entity=f"board {board_id}")
    _reject_nulls(changes, fields=("code", "name"), entity=f"board {board_id}")
    if "code" in changes and await repo.code_exists(changes["code"], exclude_id=board_id):
        raise ConflictError("A board with that code already exists.")
    row = await _update(repo, board_id, payload.row_version, changes, entity="board")
    _audit(session, ctx, action=ACTIONS.BOARD_UPDATED, entity="boards", entity_id=board_id,
           meta={"changed": sorted(changes), "oldCode": old_code, "newCode": row.code}, ip=ip)
    return _board_out(row)


async def delete_board(
    *, session: AsyncSession, ctx: OrgContext, board_id: uuid.UUID, row_version: int,
    ip: Optional[str] = None,
) -> None:
    repo = BoardRepository(session, ctx.org_id)
    row = await _require_board(repo, board_id)
    await _lock_board(session, ctx, board_id)
    row = await _require_board(repo, board_id)
    if await repo.has_dependencies(board_id):
        raise ConflictError("Board cannot be deleted while live curriculum depends on it.")
    await _delete(repo, board_id, row_version)
    _audit(session, ctx, action=ACTIONS.BOARD_DELETED, entity="boards", entity_id=board_id,
           meta={"code": row.code}, ip=ip)


# ---------------------------------------------------------------------------
# Classes
# ---------------------------------------------------------------------------
async def create_class(
    *, session: AsyncSession, ctx: OrgContext, payload: schemas.ClassCreateIn,
    ip: Optional[str] = None,
) -> schemas.ClassOut:
    board_repo = BoardRepository(session, ctx.org_id)
    await _require_board(board_repo, payload.board_id)
    await _lock_board(session, ctx, payload.board_id)
    await _require_board(board_repo, payload.board_id)
    repo = ClassRepository(session, ctx.org_id)
    if await repo.name_exists(payload.board_id, payload.name):
        raise ConflictError("That class already exists for this board.")
    try:
        row = await repo.create(board_id=payload.board_id, name=payload.name)
    except IntegrityError as exc:
        raise ConflictError("That class already exists for this board.") from exc
    _audit(session, ctx, action=ACTIONS.CLASS_CREATED, entity="classes", entity_id=row.id,
           meta={"boardId": str(row.board_id), "name": row.name}, ip=ip)
    await session.refresh(row)
    return _class_out(row)


async def get_class(*, session: AsyncSession, ctx: OrgContext, class_id: uuid.UUID) -> schemas.ClassOut:
    return _class_out(await _require_class(ClassRepository(session, ctx.org_id), class_id))


async def list_classes(
    *, session: AsyncSession, ctx: OrgContext, board_id: uuid.UUID, limit: int, offset: int,
) -> tuple[list[schemas.ClassOut], int]:
    await _require_board(BoardRepository(session, ctx.org_id), board_id)
    rows = await ClassRepository(session, ctx.org_id).list_for_board(board_id, limit=limit, offset=offset)
    items = [_class_out(row) for row in rows]
    return items, len(items)


async def update_class(
    *, session: AsyncSession, ctx: OrgContext, class_id: uuid.UUID,
    payload: schemas.ClassUpdateIn, ip: Optional[str] = None,
) -> schemas.ClassOut:
    repo = ClassRepository(session, ctx.org_id)
    before = await _require_class(repo, class_id)
    await _lock_board(session, ctx, before.board_id)
    before = await _require_class(repo, class_id)
    changes = _require_changes(payload, entity=f"class {class_id}")
    _reject_nulls(changes, fields=("name",), entity=f"class {class_id}")
    if "name" in changes and await repo.name_exists(before.board_id, changes["name"], exclude_id=class_id):
        raise ConflictError("That class already exists for this board.")
    row = await _update(repo, class_id, payload.row_version, changes, entity="class")
    _audit(session, ctx, action=ACTIONS.CLASS_UPDATED, entity="classes", entity_id=class_id,
           meta={"changed": sorted(changes)}, ip=ip)
    return _class_out(row)


async def delete_class(
    *, session: AsyncSession, ctx: OrgContext, class_id: uuid.UUID, row_version: int,
    ip: Optional[str] = None,
) -> None:
    repo = ClassRepository(session, ctx.org_id)
    row = await _require_class(repo, class_id)
    await _lock_board(session, ctx, row.board_id)
    row = await _require_class(repo, class_id)
    if await repo.has_dependencies(class_id):
        raise ConflictError("Class cannot be deleted while live subjects depend on it.")
    await _delete(repo, class_id, row_version)
    _audit(session, ctx, action=ACTIONS.CLASS_DELETED, entity="classes", entity_id=class_id,
           meta={"name": row.name}, ip=ip)


# ---------------------------------------------------------------------------
# Curriculum versions
# ---------------------------------------------------------------------------
async def create_curriculum_version(
    *, session: AsyncSession, ctx: OrgContext,
    payload: schemas.CurriculumVersionCreateIn, ip: Optional[str] = None,
) -> schemas.CurriculumVersionOut:
    board_repo = BoardRepository(session, ctx.org_id)
    await _require_board(board_repo, payload.board_id)
    await _lock_board(session, ctx, payload.board_id)
    await _require_board(board_repo, payload.board_id)
    repo = CurriculumVersionRepository(session, ctx.org_id)
    if await repo.academic_year_exists(payload.board_id, payload.academic_year):
        raise ConflictError("That board already has a curriculum version for this academic year.")
    values = payload.model_dump(exclude_none=True)
    try:
        row = await repo.create(**values)
    except IntegrityError as exc:
        raise ConflictError("That board already has a curriculum version for this academic year.") from exc
    _audit(session, ctx, action=ACTIONS.CURRICULUM_VERSION_CREATED,
           entity="curriculum_versions", entity_id=row.id,
           meta={"boardId": str(row.board_id), "academicYear": row.academic_year}, ip=ip)
    await session.refresh(row)
    return _version_out(row)


async def get_curriculum_version(
    *, session: AsyncSession, ctx: OrgContext, curriculum_version_id: uuid.UUID,
) -> schemas.CurriculumVersionOut:
    row = await _require_version(CurriculumVersionRepository(session, ctx.org_id), curriculum_version_id)
    return _version_out(row)


async def list_curriculum_versions(
    *, session: AsyncSession, ctx: OrgContext, board_id: uuid.UUID,
    status: Optional[str], limit: int, offset: int,
) -> tuple[list[schemas.CurriculumVersionOut], int]:
    await _require_board(BoardRepository(session, ctx.org_id), board_id)
    rows = await CurriculumVersionRepository(session, ctx.org_id).list_for_board(
        board_id, status=status, limit=limit, offset=offset
    )
    items = [_version_out(row) for row in rows]
    return items, len(items)


async def update_curriculum_version(
    *, session: AsyncSession, ctx: OrgContext, curriculum_version_id: uuid.UUID,
    payload: schemas.CurriculumVersionUpdateIn, ip: Optional[str] = None,
) -> schemas.CurriculumVersionOut:
    repo = CurriculumVersionRepository(session, ctx.org_id)
    before = await _require_version(repo, curriculum_version_id)
    await _lock_board(session, ctx, before.board_id)
    before = await _require_version(repo, curriculum_version_id)
    old_status = before.status
    changes = _require_changes(payload, entity=f"curriculum version {curriculum_version_id}")
    _reject_nulls(
        changes,
        fields=("status",),
        entity=f"curriculum version {curriculum_version_id}",
    )
    if old_status != "draft" and set(changes) != {"status"}:
        raise BusinessRuleError(
            "Published curriculum version metadata is immutable.",
            code="CURRICULUM_SNAPSHOT_IMMUTABLE",
            log_detail=(
                f"version {curriculum_version_id} is {old_status}; attempted fields "
                f"{sorted(changes)}"
            ),
        )
    if "status" in changes:
        allowed = {"draft": "active", "active": "superseded"}
        if allowed.get(old_status) != changes["status"]:
            raise ValidationError(
                "Curriculum status must progress draft to active to superseded.",
                log_detail=f"refused transition {old_status!r} -> {changes['status']!r}",
            )
    row = await _update(repo, curriculum_version_id, payload.row_version, changes,
                        entity="curriculum version")
    action = ACTIONS.CURRICULUM_VERSION_UPDATED
    if changes.get("status") == "active":
        action = ACTIONS.CURRICULUM_VERSION_PUBLISHED
    elif changes.get("status") == "superseded":
        action = ACTIONS.CURRICULUM_VERSION_SUPERSEDED
    _audit(session, ctx, action=action, entity="curriculum_versions",
           entity_id=curriculum_version_id,
           meta={"changed": sorted(changes), "oldStatus": old_status, "newStatus": row.status}, ip=ip)
    return _version_out(row)


async def delete_curriculum_version(
    *, session: AsyncSession, ctx: OrgContext, curriculum_version_id: uuid.UUID,
    row_version: int, ip: Optional[str] = None,
) -> None:
    repo = CurriculumVersionRepository(session, ctx.org_id)
    row = await _require_version(repo, curriculum_version_id)
    await _lock_board(session, ctx, row.board_id)
    row = await _require_version(repo, curriculum_version_id)
    if row.status != "draft":
        raise ConflictError("Only an unused draft curriculum version can be deleted.")
    if await repo.has_dependencies(curriculum_version_id):
        raise ConflictError("Curriculum version cannot be deleted while live curriculum depends on it.")
    await _delete(repo, curriculum_version_id, row_version)
    _audit(session, ctx, action=ACTIONS.CURRICULUM_VERSION_DELETED,
           entity="curriculum_versions", entity_id=curriculum_version_id,
           meta={"academicYear": row.academic_year}, ip=ip)


# ---------------------------------------------------------------------------
# Subjects
# ---------------------------------------------------------------------------
async def create_subject(
    *, session: AsyncSession, ctx: OrgContext, payload: schemas.SubjectCreateIn,
    ip: Optional[str] = None,
    allow_active_append: bool = False,
) -> schemas.SubjectOut:
    class_repo = ClassRepository(session, ctx.org_id)
    version_repo = CurriculumVersionRepository(session, ctx.org_id)
    school_class = await _require_class(class_repo, payload.class_id)
    await _lock_board(session, ctx, school_class.board_id)
    school_class = await _require_class(class_repo, payload.class_id)
    version = await _require_mutable_version(
        version_repo,
        payload.curriculum_version_id,
        entity=f"subject under class {payload.class_id}",
        allow_active_append=allow_active_append,
    )
    if version.board_id != school_class.board_id:
        raise ValidationError(
            "Curriculum version does not belong to the class's board.",
            log_detail=f"class board {school_class.board_id}; version board {version.board_id}",
        )
    repo = SubjectRepository(session, ctx.org_id)
    if await repo.name_exists(
        payload.class_id, payload.name,
        curriculum_version_id=payload.curriculum_version_id,
    ):
        raise ConflictError("That subject already exists in this curriculum snapshot.")
    try:
        row = await repo.create(**payload.model_dump())
    except IntegrityError as exc:
        raise ConflictError("That subject already exists in this curriculum snapshot.") from exc
    _audit(session, ctx, action=ACTIONS.SUBJECT_CREATED, entity="subjects", entity_id=row.id,
           meta={"classId": str(row.class_id), "curriculumVersionId": str(row.curriculum_version_id) if row.curriculum_version_id else None,
                 "name": row.name}, ip=ip)
    await session.refresh(row)
    return _subject_out(row)


async def get_subject(*, session: AsyncSession, ctx: OrgContext, subject_id: uuid.UUID) -> schemas.SubjectOut:
    return _subject_out(await _require_subject(SubjectRepository(session, ctx.org_id), subject_id))


async def list_subjects(
    *, session: AsyncSession, ctx: OrgContext, class_id: uuid.UUID,
    curriculum_version_id: Optional[uuid.UUID], limit: int, offset: int,
) -> tuple[list[schemas.SubjectOut], int]:
    await _require_class(ClassRepository(session, ctx.org_id), class_id)
    rows = await SubjectRepository(session, ctx.org_id).list_for_class(
        class_id, curriculum_version_id=curriculum_version_id, limit=limit, offset=offset
    )
    items = [_subject_out(row) for row in rows]
    return items, len(items)


async def update_subject(
    *, session: AsyncSession, ctx: OrgContext, subject_id: uuid.UUID,
    payload: schemas.SubjectUpdateIn, ip: Optional[str] = None,
) -> schemas.SubjectOut:
    repo = SubjectRepository(session, ctx.org_id)
    before, _ = await _lock_and_reload_subject(session, ctx, subject_id)
    await _require_mutable_version(
        CurriculumVersionRepository(session, ctx.org_id),
        before.curriculum_version_id,
        entity=f"subject {subject_id}",
    )
    changes = _require_changes(payload, entity=f"subject {subject_id}")
    _reject_nulls(changes, fields=("name",), entity=f"subject {subject_id}")
    if (
        "name" in changes
        and changes["name"] != before.name
        and await repo.has_lineage_links(subject_id)
    ):
        raise BusinessRuleError(
            "A subject with confirmed chapter lineage cannot be renamed.",
            code="CURRICULUM_LINEAGE_IDENTITY_IMMUTABLE",
            log_detail=f"subject {subject_id} has derived chapters",
        )
    if "name" in changes and await repo.name_exists(
        before.class_id, changes["name"],
        curriculum_version_id=before.curriculum_version_id, exclude_id=subject_id,
    ):
        raise ConflictError("That subject already exists in this curriculum snapshot.")
    row = await _update(repo, subject_id, payload.row_version, changes, entity="subject")
    _audit(session, ctx, action=ACTIONS.SUBJECT_UPDATED, entity="subjects", entity_id=subject_id,
           meta={"changed": sorted(changes)}, ip=ip)
    return _subject_out(row)


async def delete_subject(
    *, session: AsyncSession, ctx: OrgContext, subject_id: uuid.UUID, row_version: int,
    ip: Optional[str] = None,
) -> None:
    repo = SubjectRepository(session, ctx.org_id)
    row, _ = await _lock_and_reload_subject(session, ctx, subject_id)
    await _require_mutable_version(
        CurriculumVersionRepository(session, ctx.org_id),
        row.curriculum_version_id,
        entity=f"subject {subject_id}",
    )
    if await repo.has_dependencies(subject_id):
        raise ConflictError("Subject cannot be deleted while live records depend on it.")
    await _delete(repo, subject_id, row_version)
    _audit(session, ctx, action=ACTIONS.SUBJECT_DELETED, entity="subjects", entity_id=subject_id,
           meta={"name": row.name}, ip=ip)


# ---------------------------------------------------------------------------
# Chapters and lineage
# ---------------------------------------------------------------------------
async def create_chapter(
    *, session: AsyncSession, ctx: OrgContext, payload: schemas.ChapterCreateIn,
    ip: Optional[str] = None,
    allow_active_append: bool = False,
) -> schemas.ChapterOut:
    subject, _ = await _lock_and_reload_subject(session, ctx, payload.subject_id)
    version_id = payload.curriculum_version_id
    if version_id is None and subject.curriculum_version_id is not None:
        version_id = subject.curriculum_version_id
    await _validate_chapter_version_and_lineage(
        session=session, ctx=ctx, subject=subject,
        curriculum_version_id=version_id,
        derived_from_chapter_id=payload.derived_from_chapter_id,
        allow_active_append=allow_active_append,
    )
    repo = ChapterRepository(session, ctx.org_id)
    if await repo.seq_exists(payload.subject_id, payload.seq):
        raise ConflictError("That chapter sequence already exists for this subject.")
    values = payload.model_dump()
    values["curriculum_version_id"] = version_id
    try:
        row = await repo.create(**values)
    except IntegrityError as exc:
        raise ConflictError("That chapter conflicts with an existing curriculum record.") from exc
    _audit(session, ctx, action=ACTIONS.CHAPTER_CREATED, entity="chapters", entity_id=row.id,
           meta={"subjectId": str(row.subject_id), "seq": row.seq,
                 "derivedFromChapterId": str(row.derived_from_chapter_id) if row.derived_from_chapter_id else None}, ip=ip)
    await session.refresh(row)
    return _chapter_out(row)


async def get_chapter(*, session: AsyncSession, ctx: OrgContext, chapter_id: uuid.UUID) -> schemas.ChapterOut:
    return _chapter_out(await _require_chapter(ChapterRepository(session, ctx.org_id), chapter_id))


async def list_chapters(
    *, session: AsyncSession, ctx: OrgContext, subject_id: uuid.UUID,
    curriculum_version_id: Optional[uuid.UUID], limit: int, offset: int,
) -> tuple[list[schemas.ChapterOut], int]:
    await _require_subject(SubjectRepository(session, ctx.org_id), subject_id)
    rows = await ChapterRepository(session, ctx.org_id).list_for_subject(
        subject_id, curriculum_version_id=curriculum_version_id, limit=limit, offset=offset
    )
    items = [_chapter_out(row) for row in rows]
    return items, len(items)


async def get_chapter_lineage(
    *, session: AsyncSession, ctx: OrgContext, chapter_id: uuid.UUID,
) -> schemas.ChapterLineageOut:
    repo = ChapterRepository(session, ctx.org_id)
    await _require_chapter(repo, chapter_id)
    rows = await repo.get_lineage(chapter_id)
    return schemas.ChapterLineageOut(
        chapter_id=chapter_id,
        lineage=[_chapter_out(row) for row in rows],
    )


async def update_chapter(
    *, session: AsyncSession, ctx: OrgContext, chapter_id: uuid.UUID,
    payload: schemas.ChapterUpdateIn, ip: Optional[str] = None,
) -> schemas.ChapterOut:
    repo = ChapterRepository(session, ctx.org_id)
    before, subject, _ = await _lock_and_reload_chapter(session, ctx, chapter_id)
    await _require_mutable_version(
        CurriculumVersionRepository(session, ctx.org_id),
        before.curriculum_version_id,
        entity=f"chapter {chapter_id}",
    )
    changes = _require_changes(payload, entity=f"chapter {chapter_id}")
    _reject_nulls(changes, fields=("seq", "title"), entity=f"chapter {chapter_id}")
    if "seq" in changes and await repo.seq_exists(
        before.subject_id, changes["seq"], exclude_id=chapter_id
    ):
        raise ConflictError("That chapter sequence already exists for this subject.")
    if "derived_from_chapter_id" in changes:
        await _validate_chapter_version_and_lineage(
            session=session, ctx=ctx, subject=subject,
            curriculum_version_id=before.curriculum_version_id,
            derived_from_chapter_id=changes["derived_from_chapter_id"],
            chapter_id=chapter_id,
        )
    row = await _update(repo, chapter_id, payload.row_version, changes, entity="chapter")
    _audit(session, ctx, action=ACTIONS.CHAPTER_UPDATED, entity="chapters", entity_id=chapter_id,
           meta={"changed": sorted(changes)}, ip=ip)
    return _chapter_out(row)


async def delete_chapter(
    *, session: AsyncSession, ctx: OrgContext, chapter_id: uuid.UUID, row_version: int,
    ip: Optional[str] = None,
) -> None:
    repo = ChapterRepository(session, ctx.org_id)
    row, _, _ = await _lock_and_reload_chapter(session, ctx, chapter_id)
    await _require_mutable_version(
        CurriculumVersionRepository(session, ctx.org_id),
        row.curriculum_version_id,
        entity=f"chapter {chapter_id}",
    )
    if await repo.has_dependencies(chapter_id):
        raise ConflictError("Chapter cannot be deleted while live records or lineage depend on it.")
    await _delete(repo, chapter_id, row_version)
    _audit(session, ctx, action=ACTIONS.CHAPTER_DELETED, entity="chapters", entity_id=chapter_id,
           meta={"title": row.title}, ip=ip)


# ---------------------------------------------------------------------------
# Learning outcomes
# ---------------------------------------------------------------------------
async def create_outcome(
    *, session: AsyncSession, ctx: OrgContext, payload: schemas.OutcomeCreateIn,
    ip: Optional[str] = None,
    allow_active_append: bool = False,
) -> schemas.OutcomeOut:
    chapter, _, _ = await _lock_and_reload_chapter(
        session, ctx, payload.chapter_id
    )
    await _require_mutable_version(
        CurriculumVersionRepository(session, ctx.org_id),
        chapter.curriculum_version_id,
        entity=f"learning outcome under chapter {payload.chapter_id}",
        allow_active_append=allow_active_append,
    )
    repo = OutcomeRepository(session, ctx.org_id)
    if payload.code is not None and await repo.code_exists(payload.chapter_id, payload.code):
        raise ConflictError("That outcome code already exists for this chapter.")
    try:
        row = await repo.create(**payload.model_dump())
    except IntegrityError as exc:
        raise ConflictError("That outcome code already exists for this chapter.") from exc
    _audit(session, ctx, action=ACTIONS.OUTCOME_CREATED, entity="learning_outcomes", entity_id=row.id,
           meta={"chapterId": str(row.chapter_id), "code": row.code}, ip=ip)
    await session.refresh(row)
    return _outcome_out(row)


async def get_outcome(*, session: AsyncSession, ctx: OrgContext, outcome_id: uuid.UUID) -> schemas.OutcomeOut:
    repo = OutcomeRepository(session, ctx.org_id)
    row = await repo.get_by_id(outcome_id)
    if row is None:
        raise NotFoundError("Learning outcome not found.")
    return _outcome_out(row)


async def list_outcomes(
    *, session: AsyncSession, ctx: OrgContext, chapter_id: uuid.UUID,
    limit: int, offset: int,
) -> tuple[list[schemas.OutcomeOut], int]:
    await _require_chapter(ChapterRepository(session, ctx.org_id), chapter_id)
    rows = await OutcomeRepository(session, ctx.org_id).list_for_chapter(
        chapter_id, limit=limit, offset=offset
    )
    items = [_outcome_out(row) for row in rows]
    return items, len(items)


async def update_outcome(
    *, session: AsyncSession, ctx: OrgContext, outcome_id: uuid.UUID,
    payload: schemas.OutcomeUpdateIn, ip: Optional[str] = None,
) -> schemas.OutcomeOut:
    repo = OutcomeRepository(session, ctx.org_id)
    before = await repo.get_by_id(outcome_id)
    if before is None:
        raise NotFoundError("Learning outcome not found.")
    chapter, _, _ = await _lock_and_reload_chapter(
        session, ctx, before.chapter_id
    )
    before = await repo.get_by_id(outcome_id)
    if before is None:
        raise NotFoundError("Learning outcome not found.")
    await _require_mutable_version(
        CurriculumVersionRepository(session, ctx.org_id),
        chapter.curriculum_version_id,
        entity=f"learning outcome {outcome_id}",
    )
    changes = _require_changes(payload, entity=f"learning outcome {outcome_id}")
    _reject_nulls(
        changes,
        fields=("description",),
        entity=f"learning outcome {outcome_id}",
    )
    if "code" in changes and changes["code"] is not None and await repo.code_exists(
        before.chapter_id, changes["code"], exclude_id=outcome_id
    ):
        raise ConflictError("That outcome code already exists for this chapter.")
    row = await _update(repo, outcome_id, payload.row_version, changes, entity="learning outcome")
    _audit(session, ctx, action=ACTIONS.OUTCOME_UPDATED, entity="learning_outcomes", entity_id=outcome_id,
           meta={"changed": sorted(changes)}, ip=ip)
    return _outcome_out(row)


async def delete_outcome(
    *, session: AsyncSession, ctx: OrgContext, outcome_id: uuid.UUID, row_version: int,
    ip: Optional[str] = None,
) -> None:
    repo = OutcomeRepository(session, ctx.org_id)
    row = await repo.get_by_id(outcome_id)
    if row is None:
        raise NotFoundError("Learning outcome not found.")
    chapter, _, _ = await _lock_and_reload_chapter(session, ctx, row.chapter_id)
    row = await repo.get_by_id(outcome_id)
    if row is None:
        raise NotFoundError("Learning outcome not found.")
    await _require_mutable_version(
        CurriculumVersionRepository(session, ctx.org_id),
        chapter.curriculum_version_id,
        entity=f"learning outcome {outcome_id}",
    )
    await _delete(repo, outcome_id, row_version)
    _audit(session, ctx, action=ACTIONS.OUTCOME_DELETED, entity="learning_outcomes", entity_id=outcome_id,
           meta={"code": row.code}, ip=ip)


__all__ = [name for name in globals() if name.startswith(("create_", "get_", "list_", "update_", "delete_"))]
