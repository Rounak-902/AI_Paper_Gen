"""Programs router."""

import uuid

from fastapi import APIRouter, Depends, Path, Request, status
from sqlalchemy.ext.asyncio import AsyncSession

from app.common.deps import (
    OrgContext,
    get_db,
    get_org_context,
    require_roles,
)
from app.common.routing import EnvelopeRoute
from app.modules.programs import schemas, service
from app.modules.tenancy.router import PageParams, _paginate, client_ip

_ADMIN_ROLES = ("coaching_admin", "super_admin")

router = APIRouter(
    prefix="/api/v1/orgs/{org_id}/programs",
    tags=["Programs"],
    route_class=EnvelopeRoute,
    dependencies=[Depends(get_org_context)],
)


@router.post("", status_code=status.HTTP_201_CREATED)
async def create_program(
    payload: schemas.ProgramCreateIn,
    request: Request,
    org_id: uuid.UUID = Path(...),
    ctx: OrgContext = Depends(require_roles(*_ADMIN_ROLES)),
    session: AsyncSession = Depends(get_db),
) -> schemas.ProgramOut:
    return await service.create_program(
        session=session,
        ctx=ctx,
        org_id=org_id,
        payload=payload,
        ip=client_ip(request),
    )


@router.get("")
async def list_programs(
    org_id: uuid.UUID = Path(...),
    ctx: OrgContext = Depends(get_org_context),
    session: AsyncSession = Depends(get_db),
    page: PageParams = Depends(),
) -> list[schemas.ProgramOut]:
    items, _ = await service.list_programs(
        session=session,
        ctx=ctx,
        org_id=org_id,
        limit=page.probe_limit,
        offset=page.offset,
    )
    return _paginate(items, page)


@router.patch("/{program_id}")
async def patch_program(
    payload: schemas.ProgramUpdateIn,
    request: Request,
    org_id: uuid.UUID = Path(...),
    program_id: uuid.UUID = Path(...),
    ctx: OrgContext = Depends(require_roles(*_ADMIN_ROLES)),
    session: AsyncSession = Depends(get_db),
) -> schemas.ProgramOut:
    return await service.update_program(
        session=session,
        ctx=ctx,
        org_id=org_id,
        program_id=program_id,
        payload=payload,
        ip=client_ip(request),
    )


@router.get("/{program_id}/assignments")
async def get_program_assignments(
    org_id: uuid.UUID = Path(...),
    program_id: uuid.UUID = Path(...),
    ctx: OrgContext = Depends(require_roles(*_ADMIN_ROLES)),
    session: AsyncSession = Depends(get_db),
) -> schemas.ProgramAssignmentsOut:
    return await service.get_program_assignments(
        session=session,
        ctx=ctx,
        org_id=org_id,
        program_id=program_id,
    )


@router.post("/{program_id}/teachers")
async def assign_teachers(
    payload: schemas.AssignTeachersRequest,
    request: Request,
    org_id: uuid.UUID = Path(...),
    program_id: uuid.UUID = Path(...),
    ctx: OrgContext = Depends(require_roles(*_ADMIN_ROLES)),
    session: AsyncSession = Depends(get_db),
) -> schemas.DeletedOut:
    await service.assign_teachers(
        session=session,
        ctx=ctx,
        org_id=org_id,
        program_id=program_id,
        payload=payload,
        ip=client_ip(request),
    )
    return schemas.DeletedOut(id=program_id, deleted=False)


@router.post("/{program_id}/subjects")
async def assign_subjects(
    payload: schemas.AssignSubjectsRequest,
    request: Request,
    org_id: uuid.UUID = Path(...),
    program_id: uuid.UUID = Path(...),
    ctx: OrgContext = Depends(require_roles(*_ADMIN_ROLES)),
    session: AsyncSession = Depends(get_db),
) -> schemas.DeletedOut:
    await service.assign_subjects(
        session=session,
        ctx=ctx,
        org_id=org_id,
        program_id=program_id,
        payload=payload,
        ip=client_ip(request),
    )
    return schemas.DeletedOut(id=program_id, deleted=False)
