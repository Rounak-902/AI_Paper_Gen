"""Template lifecycle, clone-on-write, defaults, and HTML preview."""

from __future__ import annotations

import copy
import uuid
from typing import Any, Optional

from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from app.common.audit_log import record_audit
from app.common.deps import OrgContext
from app.common.errors import BusinessRuleError, ConflictError, NotFoundError
from app.db.exceptions import OptimisticLockError
from app.modules.programs.service import assert_path_org
from app.modules.templates import schemas
from app.modules.templates.preview import render_preview_html
from app.modules.templates.repository import TemplateRepository


def _layout_dict(layout: schemas.TemplateLayoutV1) -> dict[str, Any]:
    return layout.model_dump(mode="json", by_alias=True)


def _out(row: Any) -> schemas.TemplateOut:
    return schemas.TemplateOut.model_validate(row)


def _not_found(template_id: uuid.UUID) -> NotFoundError:
    return NotFoundError(
        "Template not found.",
        log_detail=f"template {template_id} absent or outside tenant scope",
    )


async def create_template(
    *, session: AsyncSession, ctx: OrgContext, org_id: uuid.UUID,
    payload: schemas.TemplateCreateIn, ip: Optional[str] = None,
) -> schemas.TemplateOut:
    assert_path_org(ctx, org_id)
    repo = TemplateRepository(session, ctx.org_id)
    try:
        row = await repo.create(
            name=payload.name.strip(),
            layout=_layout_dict(payload.layout),
            created_by=ctx.user_id,
            status="draft",
            is_default=False,
        )
    except IntegrityError as exc:
        raise ConflictError("A template with that name already exists in this organization.") from exc
    record_audit(session, org_id=ctx.org_id, actor_id=ctx.user_id, action="template.created", entity="templates", entity_id=row.id, meta={"name": row.name}, ip=ip)
    await session.refresh(row)
    return _out(row)


async def list_templates(
    *, session: AsyncSession, ctx: OrgContext, org_id: uuid.UUID,
    limit: int, offset: int, status: Optional[str] = None,
) -> tuple[list[schemas.TemplateOut], int]:
    assert_path_org(ctx, org_id)
    rows = await TemplateRepository(session, ctx.org_id).list_templates(limit=limit, offset=offset, status=status)
    return [_out(row) for row in rows], len(rows)


async def get_template(
    *, session: AsyncSession, ctx: OrgContext, org_id: uuid.UUID, template_id: uuid.UUID,
) -> schemas.TemplateOut:
    assert_path_org(ctx, org_id)
    row = await TemplateRepository(session, ctx.org_id).get_by_id(template_id)
    if row is None:
        raise _not_found(template_id)
    return _out(row)


async def _clone_name(repo: TemplateRepository, original: str) -> str:
    base = f"{original} (Draft)"
    candidate = base
    sequence = 2
    while await repo.name_exists(candidate):
        candidate = f"{original} (Draft {sequence})"
        sequence += 1
    return candidate


async def update_template(
    *, session: AsyncSession, ctx: OrgContext, org_id: uuid.UUID, template_id: uuid.UUID,
    payload: schemas.TemplateUpdateIn, ip: Optional[str] = None,
) -> schemas.TemplateOut:
    assert_path_org(ctx, org_id)
    repo = TemplateRepository(session, ctx.org_id)
    row = await repo.get_by_id(template_id)
    if row is None:
        raise _not_found(template_id)
    if row.status == "archived":
        raise BusinessRuleError("Archived templates cannot be edited.", code="INVALID_TEMPLATE_TRANSITION")
    if row.row_version != payload.row_version:
        raise OptimisticLockError(
            entity="templates",
            entity_id=row.id,
            expected_version=payload.row_version,
        )

    name = payload.name.strip() if payload.name is not None else row.name
    layout = _layout_dict(payload.layout) if payload.layout is not None else copy.deepcopy(row.layout)
    try:
        if row.status == "active":
            clone = await repo.create(
                name=name if name != row.name else await _clone_name(repo, row.name),
                layout=layout,
                created_by=ctx.user_id,
                status="draft",
                is_default=False,
                source_template_id=row.id,
            )
            record_audit(session, org_id=ctx.org_id, actor_id=ctx.user_id, action="template.cloned_for_edit", entity="templates", entity_id=clone.id, meta={"sourceTemplateId": str(row.id)}, ip=ip)
            await session.refresh(clone)
            return _out(clone)

        updated = await repo.update(row.id, payload.row_version, name=name, layout=layout)
    except IntegrityError as exc:
        raise ConflictError("A template with that name already exists in this organization.") from exc
    record_audit(session, org_id=ctx.org_id, actor_id=ctx.user_id, action="template.updated", entity="templates", entity_id=updated.id, ip=ip)
    return _out(updated)


async def activate_template(
    *, session: AsyncSession, ctx: OrgContext, org_id: uuid.UUID, template_id: uuid.UUID,
    payload: schemas.TemplateActionIn, ip: Optional[str] = None,
) -> schemas.TemplateOut:
    assert_path_org(ctx, org_id)
    repo = TemplateRepository(session, ctx.org_id)
    await repo.lock_default_assignment()
    row = await repo.get_by_id(template_id)
    if row is None:
        raise _not_found(template_id)
    if row.status != "draft":
        raise BusinessRuleError(
            f"Template cannot transition from {row.status} to active.",
            code="INVALID_TEMPLATE_TRANSITION",
        )
    updated = await repo.update(
        row.id,
        payload.row_version,
        status="active",
        is_default=not await repo.has_default(),
    )
    record_audit(session, org_id=ctx.org_id, actor_id=ctx.user_id, action="template.activated", entity="templates", entity_id=updated.id, ip=ip)
    return _out(updated)


async def set_default_template(
    *, session: AsyncSession, ctx: OrgContext, org_id: uuid.UUID, template_id: uuid.UUID,
    payload: schemas.TemplateActionIn, ip: Optional[str] = None,
) -> schemas.TemplateOut:
    assert_path_org(ctx, org_id)
    repo = TemplateRepository(session, ctx.org_id)
    await repo.lock_default_assignment()
    row = await repo.get_by_id(template_id)
    if row is None:
        raise _not_found(template_id)
    if row.status != "active":
        raise BusinessRuleError("Only an active template can be the organization default.", code="TEMPLATE_DEFAULT_REQUIRES_ACTIVE")
    await repo.clear_defaults(except_id=row.id)
    updated = await repo.update(row.id, payload.row_version, is_default=True)
    record_audit(session, org_id=ctx.org_id, actor_id=ctx.user_id, action="template.default_changed", entity="templates", entity_id=updated.id, ip=ip)
    return _out(updated)


async def archive_template(
    *, session: AsyncSession, ctx: OrgContext, org_id: uuid.UUID, template_id: uuid.UUID,
    payload: schemas.TemplateActionIn, ip: Optional[str] = None,
) -> schemas.TemplateOut:
    assert_path_org(ctx, org_id)
    repo = TemplateRepository(session, ctx.org_id)
    await repo.lock_default_assignment()
    row = await repo.get_by_id(template_id)
    if row is None:
        raise _not_found(template_id)
    if row.status != "active":
        raise BusinessRuleError(
            f"Template cannot transition from {row.status} to archived.",
            code="INVALID_TEMPLATE_TRANSITION",
        )
    if row.is_default:
        raise BusinessRuleError(
            "The organization default cannot be archived. Set another active template as default first.",
            code="DEFAULT_TEMPLATE_ARCHIVE_BLOCKED",
        )
    updated = await repo.update(row.id, payload.row_version, status="archived", is_default=False)
    record_audit(session, org_id=ctx.org_id, actor_id=ctx.user_id, action="template.archived", entity="templates", entity_id=updated.id, ip=ip)
    return _out(updated)


async def preview_template(
    *, session: AsyncSession, ctx: OrgContext, org_id: uuid.UUID, template_id: uuid.UUID,
) -> schemas.TemplatePreviewOut:
    assert_path_org(ctx, org_id)
    row = await TemplateRepository(session, ctx.org_id).get_by_id(template_id)
    if row is None:
        raise _not_found(template_id)
    # Revalidate persisted JSON before it reaches HTML construction. This also makes
    # any pre-v1 legacy row fail as a clear validation error rather than render oddly.
    layout = schemas.TemplateLayoutV1.model_validate(row.layout)
    return schemas.TemplatePreviewOut(template_id=row.id, html=render_preview_html(_layout_dict(layout)))
