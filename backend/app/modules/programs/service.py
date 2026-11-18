"""Programs service."""

from __future__ import annotations

import logging
import uuid
from typing import Any, Optional

from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from app.common.audit_log import record_audit
from app.common.deps import OrgContext
from app.common.errors import (
    AuthorizationError,
    ConflictError,
    NotFoundError,
    ValidationError,
)
from app.db.exceptions import EntityNotFoundError, OptimisticLockError
from app.db.session import get_tenant_session
from app.modules.programs import schemas
from app.modules.programs.repository import ProgramRepository

logger = logging.getLogger("papergen.programs.service")


def assert_path_org(ctx: OrgContext, path_org_id: uuid.UUID) -> None:
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


def _program_out(program: Any) -> schemas.ProgramOut:
    return schemas.ProgramOut(
        id=program.id,
        org_id=program.org_id,
        branch_id=program.branch_id,
        name=program.name,
        exam_target=program.exam_target,
        academic_year=program.academic_year,
        status=program.status,
        row_version=program.row_version,
        created_at=program.created_at,
        updated_at=program.updated_at,
    )


async def resolve_teacher_subject_ids(
    *, org_id: uuid.UUID, user_id: uuid.UUID, role: str
) -> Optional[tuple[uuid.UUID, ...]]:
    """Resolve a teacher's subject scope from their program assignments.

    Returns ``None`` if the role is ``coaching_admin`` or ``super_admin`` (org-wide
    access). If the role is ``teacher``, returns a tuple of assigned subject IDs.
    If the teacher has no assignments, returns an empty tuple (fail-closed).
    """
    if role in ("coaching_admin", "super_admin"):
        return None

    # This runs in its own session before `get_db` is called in the guard chain.
    async with get_tenant_session(org_id) as session:
        repo = ProgramRepository(session, org_id)
        subject_ids = await repo.get_teacher_subject_ids(user_id)

    return subject_ids


async def create_program(
    *,
    session: AsyncSession,
    ctx: OrgContext,
    org_id: uuid.UUID,
    payload: schemas.ProgramCreateIn,
    ip: Optional[str] = None,
) -> schemas.ProgramOut:
    """Create a new program."""
    assert_path_org(ctx, org_id)

    repo = ProgramRepository(session, ctx.org_id)
    create_kwargs: dict[str, Any] = {"name": payload.name}
    if payload.branch_id is not None:
        create_kwargs["branch_id"] = payload.branch_id
    if payload.exam_target is not None:
        create_kwargs["exam_target"] = payload.exam_target
    if payload.academic_year is not None:
        create_kwargs["academic_year"] = payload.academic_year

    try:
        program = await repo.create(**create_kwargs)
    except IntegrityError as exc:
        raise ConflictError(
            "A program with that name already exists in this branch.",
            log_detail=f"uq_programs_org_id_branch_id_name violated: {exc.orig}",
        ) from exc

    record_audit(
        session,
        org_id=ctx.org_id,
        actor_id=ctx.user_id,
        action="program.created",
        entity="programs",
        entity_id=program.id,
        meta={
            "name": payload.name,
            "branchId": str(payload.branch_id) if payload.branch_id else None,
        },
        ip=ip,
    )
    await session.refresh(program)
    return _program_out(program)


async def list_programs(
    *,
    session: AsyncSession,
    ctx: OrgContext,
    org_id: uuid.UUID,
    limit: int,
    offset: int,
) -> tuple[list[schemas.ProgramOut], int]:
    """List active programs in the org."""
    assert_path_org(ctx, org_id)
    repo = ProgramRepository(session, ctx.org_id)
    programs = await repo.list_programs(limit=limit, offset=offset)
    items = [_program_out(p) for p in programs]
    return items, len(items)


async def update_program(
    *,
    session: AsyncSession,
    ctx: OrgContext,
    org_id: uuid.UUID,
    program_id: uuid.UUID,
    payload: schemas.ProgramUpdateIn,
    ip: Optional[str] = None,
) -> schemas.ProgramOut:
    """Update program metadata."""
    assert_path_org(ctx, org_id)

    changes: dict[str, Any] = {}
    if payload.name is not None:
        changes["name"] = payload.name
    if payload.exam_target is not None:
        changes["exam_target"] = payload.exam_target
    if payload.academic_year is not None:
        changes["academic_year"] = payload.academic_year
    if payload.status is not None:
        changes["status"] = payload.status

    if not changes:
        raise ValidationError(
            "Provide at least one field to update.",
            log_detail=f"empty PATCH body for program {program_id}",
        )

    repo = ProgramRepository(session, ctx.org_id)
    before = await repo.get_by_id(program_id)
    if before is None:
        raise NotFoundError("Program not found.")

    try:
        program = await repo.update(program_id, payload.row_version, **changes)
    except OptimisticLockError as exc:
        raise ConflictError(
            "This record was modified by someone else. Reload and try again."
        ) from exc
    except EntityNotFoundError as exc:
        raise NotFoundError("Program not found.") from exc
    except IntegrityError as exc:
        raise ConflictError(
            "A program with that name already exists in this branch.",
            log_detail=f"uq_programs_org_id_branch_id_name violated: {exc.orig}",
        ) from exc

    record_audit(
        session,
        org_id=ctx.org_id,
        actor_id=ctx.user_id,
        action="program.updated",
        entity="programs",
        entity_id=program_id,
        meta={
            "changed": sorted(changes.keys()),
        },
        ip=ip,
    )
    return _program_out(program)


async def get_program_assignments(
    *,
    session: AsyncSession,
    ctx: OrgContext,
    org_id: uuid.UUID,
    program_id: uuid.UUID,
) -> schemas.ProgramAssignmentsOut:
    """Read the complete teacher and subject assignment sets for a program."""
    assert_path_org(ctx, org_id)
    repo = ProgramRepository(session, ctx.org_id)

    program = await repo.get_by_id(program_id)
    if program is None:
        raise NotFoundError("Program not found.")

    teacher_ids, subject_ids = await repo.get_assignment_ids(program_id)
    return schemas.ProgramAssignmentsOut(
        program_id=program_id,
        teacher_ids=list(teacher_ids),
        subject_ids=list(subject_ids),
    )


async def assign_teachers(
    *,
    session: AsyncSession,
    ctx: OrgContext,
    org_id: uuid.UUID,
    program_id: uuid.UUID,
    payload: schemas.AssignTeachersRequest,
    ip: Optional[str] = None,
) -> None:
    """Full replacement of teachers assigned to this program."""
    assert_path_org(ctx, org_id)
    repo = ProgramRepository(session, ctx.org_id)

    program = await repo.get_by_id(program_id)
    if program is None:
        raise NotFoundError("Program not found.")

    await repo.assign_teachers(program_id, payload.teacher_ids)

    record_audit(
        session,
        org_id=ctx.org_id,
        actor_id=ctx.user_id,
        action="program.teachers_assigned",
        entity="programs",
        entity_id=program_id,
        meta={"teacherIds": [str(t) for t in payload.teacher_ids]},
        ip=ip,
    )


async def assign_subjects(
    *,
    session: AsyncSession,
    ctx: OrgContext,
    org_id: uuid.UUID,
    program_id: uuid.UUID,
    payload: schemas.AssignSubjectsRequest,
    ip: Optional[str] = None,
) -> None:
    """Full replacement of subjects assigned to this program."""
    assert_path_org(ctx, org_id)
    repo = ProgramRepository(session, ctx.org_id)

    program = await repo.get_by_id(program_id)
    if program is None:
        raise NotFoundError("Program not found.")

    await repo.assign_subjects(program_id, payload.subject_ids)

    record_audit(
        session,
        org_id=ctx.org_id,
        actor_id=ctx.user_id,
        action="program.subjects_assigned",
        entity="programs",
        entity_id=program_id,
        meta={"subjectIds": [str(s) for s in payload.subject_ids]},
        ip=ip,
    )
