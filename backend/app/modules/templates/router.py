"""Templates API using the existing P1 guard chain and envelope route."""

import uuid
from typing import Literal, Optional

from fastapi import APIRouter, Depends, Path, Query, Request, status
from sqlalchemy.ext.asyncio import AsyncSession

from app.common.deps import OrgContext, get_db, get_org_context, require_roles
from app.common.routing import EnvelopeRoute
from app.modules.templates import schemas, service
from app.modules.tenancy.router import PageParams, _paginate, client_ip

_ADMIN_ROLES = ("coaching_admin", "super_admin")

router = APIRouter(
    prefix="/api/v1/orgs/{org_id}/templates",
    tags=["Templates"],
    route_class=EnvelopeRoute,
    dependencies=[Depends(get_org_context)],
)


@router.post("", status_code=status.HTTP_201_CREATED)
async def create_template(payload: schemas.TemplateCreateIn, request: Request, org_id: uuid.UUID = Path(...), ctx: OrgContext = Depends(require_roles(*_ADMIN_ROLES)), session: AsyncSession = Depends(get_db)) -> schemas.TemplateOut:
    return await service.create_template(session=session, ctx=ctx, org_id=org_id, payload=payload, ip=client_ip(request))


@router.get("")
async def list_templates(org_id: uuid.UUID = Path(...), status_filter: Optional[Literal["draft", "active", "archived"]] = Query(default=None, alias="status"), ctx: OrgContext = Depends(get_org_context), session: AsyncSession = Depends(get_db), page: PageParams = Depends()) -> list[schemas.TemplateOut]:
    items, _ = await service.list_templates(session=session, ctx=ctx, org_id=org_id, limit=page.probe_limit, offset=page.offset, status=status_filter)
    return _paginate(items, page)


@router.get("/{template_id}")
async def get_template(org_id: uuid.UUID = Path(...), template_id: uuid.UUID = Path(...), ctx: OrgContext = Depends(get_org_context), session: AsyncSession = Depends(get_db)) -> schemas.TemplateOut:
    return await service.get_template(session=session, ctx=ctx, org_id=org_id, template_id=template_id)


@router.patch("/{template_id}")
async def update_template(payload: schemas.TemplateUpdateIn, request: Request, org_id: uuid.UUID = Path(...), template_id: uuid.UUID = Path(...), ctx: OrgContext = Depends(require_roles(*_ADMIN_ROLES)), session: AsyncSession = Depends(get_db)) -> schemas.TemplateOut:
    return await service.update_template(session=session, ctx=ctx, org_id=org_id, template_id=template_id, payload=payload, ip=client_ip(request))


@router.post("/{template_id}/activate")
async def activate_template(payload: schemas.TemplateActionIn, request: Request, org_id: uuid.UUID = Path(...), template_id: uuid.UUID = Path(...), ctx: OrgContext = Depends(require_roles(*_ADMIN_ROLES)), session: AsyncSession = Depends(get_db)) -> schemas.TemplateOut:
    return await service.activate_template(session=session, ctx=ctx, org_id=org_id, template_id=template_id, payload=payload, ip=client_ip(request))


@router.post("/{template_id}/archive")
async def archive_template(payload: schemas.TemplateActionIn, request: Request, org_id: uuid.UUID = Path(...), template_id: uuid.UUID = Path(...), ctx: OrgContext = Depends(require_roles(*_ADMIN_ROLES)), session: AsyncSession = Depends(get_db)) -> schemas.TemplateOut:
    return await service.archive_template(session=session, ctx=ctx, org_id=org_id, template_id=template_id, payload=payload, ip=client_ip(request))


@router.post("/{template_id}/default")
async def set_default_template(payload: schemas.TemplateActionIn, request: Request, org_id: uuid.UUID = Path(...), template_id: uuid.UUID = Path(...), ctx: OrgContext = Depends(require_roles(*_ADMIN_ROLES)), session: AsyncSession = Depends(get_db)) -> schemas.TemplateOut:
    return await service.set_default_template(session=session, ctx=ctx, org_id=org_id, template_id=template_id, payload=payload, ip=client_ip(request))


@router.post("/{template_id}/preview")
async def preview_template(org_id: uuid.UUID = Path(...), template_id: uuid.UUID = Path(...), ctx: OrgContext = Depends(get_org_context), session: AsyncSession = Depends(get_db)) -> schemas.TemplatePreviewOut:
    return await service.preview_template(session=session, ctx=ctx, org_id=org_id, template_id=template_id)
