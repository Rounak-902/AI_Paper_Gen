from __future__ import annotations

import uuid

import pytest
from sqlalchemy import select
from sqlalchemy.exc import IntegrityError

from app.common.deps import OrgContext
from app.common.errors import BusinessRuleError
from app.models.template import Template
from app.modules.templates import schemas, service


def _ctx(org_id: uuid.UUID, user_id: uuid.UUID) -> OrgContext:
    return OrgContext(
        org_id=org_id,
        user_id=user_id,
        role="coaching_admin",
        roles=("coaching_admin",),
    )


def _layout(*, institute: str, primary: str, footer: str) -> schemas.TemplateLayoutV1:
    return schemas.TemplateLayoutV1.model_validate(
        {
            "branding": {
                "primaryColor": primary,
                "secondaryColor": "#F59E0B",
                "instituteName": institute,
                "logo": {"url": None, "placement": "right", "maxHeightMm": 16},
                "watermark": {"text": "SAMPLE", "opacity": 0.12},
            },
            "typography": {
                "fontFamily": "Georgia",
                "bodySizePt": 12,
                "headingScale": 1.4,
                "lineHeight": 1.5,
            },
            "spacing": {"sectionGapMm": 9, "questionGapMm": 5},
            "footer": {
                "enabled": True,
                "text": footer,
                "pageNumbering": {
                    "enabled": True,
                    "format": "Sheet {page}/{pages}",
                    "alignment": "center",
                },
            },
        }
    )


@pytest.mark.db
async def test_clone_lifecycle_default_and_preview_are_real_database_behaviour(
    db_session, tenant
) -> None:
    ctx = _ctx(tenant.org_id, tenant.user_id)
    original_layout = _layout(
        institute="Original Institute", primary="#0F766E", footer="Original footer"
    )
    draft = await service.create_template(
        session=db_session,
        ctx=ctx,
        org_id=tenant.org_id,
        payload=schemas.TemplateCreateIn(name="Term Paper", layout=original_layout),
    )
    active = await service.activate_template(
        session=db_session,
        ctx=ctx,
        org_id=tenant.org_id,
        template_id=draft.id,
        payload=schemas.TemplateActionIn(row_version=draft.row_version),
    )
    assert active.status == "active"
    assert active.is_default is True

    changed_layout = _layout(
        institute="Northstar Academy", primary="#123456", footer="Confidential"
    )
    clone = await service.update_template(
        session=db_session,
        ctx=ctx,
        org_id=tenant.org_id,
        template_id=active.id,
        payload=schemas.TemplateUpdateIn(
            row_version=active.row_version,
            layout=changed_layout,
        ),
    )
    await db_session.commit()

    persisted = (
        await db_session.execute(
            select(Template).where(Template.id.in_((active.id, clone.id))).order_by(Template.id)
        )
    ).scalars().all()
    by_id = {row.id: row for row in persisted}
    assert clone.id != active.id
    assert clone.source_template_id == active.id
    assert clone.status == "draft"
    assert by_id[active.id].layout == original_layout.model_dump(mode="json", by_alias=True)
    assert by_id[clone.id].layout == changed_layout.model_dump(mode="json", by_alias=True)

    with pytest.raises(BusinessRuleError, match="draft to archived") as transition:
        await service.archive_template(
            session=db_session,
            ctx=ctx,
            org_id=tenant.org_id,
            template_id=clone.id,
            payload=schemas.TemplateActionIn(row_version=clone.row_version),
        )
    assert transition.value.code == "INVALID_TEMPLATE_TRANSITION"

    second = await service.create_template(
        session=db_session,
        ctx=ctx,
        org_id=tenant.org_id,
        payload=schemas.TemplateCreateIn(name="Second", layout=changed_layout),
    )
    second = await service.activate_template(
        session=db_session,
        ctx=ctx,
        org_id=tenant.org_id,
        template_id=second.id,
        payload=schemas.TemplateActionIn(row_version=second.row_version),
    )
    second = await service.set_default_template(
        session=db_session,
        ctx=ctx,
        org_id=tenant.org_id,
        template_id=second.id,
        payload=schemas.TemplateActionIn(row_version=second.row_version),
    )
    await db_session.commit()

    defaults = (
        await db_session.execute(
            select(Template.id, Template.is_default)
            .where(Template.org_id == tenant.org_id, Template.deleted_at.is_(None))
            .order_by(Template.name)
        )
    ).all()
    assert sum(1 for _, is_default in defaults if is_default) == 1
    assert dict(defaults)[active.id] is False
    assert dict(defaults)[second.id] is True

    with pytest.raises(BusinessRuleError, match="default cannot be archived") as archive:
        await service.archive_template(
            session=db_session,
            ctx=ctx,
            org_id=tenant.org_id,
            template_id=second.id,
            payload=schemas.TemplateActionIn(row_version=second.row_version),
        )
    assert archive.value.code == "DEFAULT_TEMPLATE_ARCHIVE_BLOCKED"
    persisted_default = await db_session.get(Template, second.id)
    assert persisted_default is not None
    assert persisted_default.status == "active"
    assert persisted_default.is_default is True

    preview = await service.preview_template(
        session=db_session,
        ctx=ctx,
        org_id=tenant.org_id,
        template_id=second.id,
    )
    assert preview.fidelity == "html-approximation"
    assert "Northstar Academy" in preview.html
    assert "#123456" in preview.html
    assert "Georgia" in preview.html
    assert "Confidential" in preview.html
    assert "Sheet 1/1" in preview.html
    assert "Which process converts light energy" in preview.html


def test_layout_schema_rejects_unversioned_or_invalid_page_numbering() -> None:
    with pytest.raises(ValueError):
        schemas.TemplateLayoutV1.model_validate({"schemaVersion": 2})
    with pytest.raises(ValueError):
        schemas.TemplateLayoutV1.model_validate(
            {"footer": {"pageNumbering": {"format": "No page token"}}}
        )


@pytest.mark.db
async def test_database_rejects_two_defaults_for_one_org(db_session, tenant) -> None:
    layout = _layout(institute="Race Proof", primary="#334455", footer="")
    db_session.add(
        Template(
            org_id=tenant.org_id,
            name="First default",
            layout=layout.model_dump(mode="json", by_alias=True),
            created_by=tenant.user_id,
            status="active",
            is_default=True,
        )
    )
    await db_session.flush()
    with pytest.raises(IntegrityError) as duplicate:
        async with db_session.begin_nested():
            db_session.add(
                Template(
                    org_id=tenant.org_id,
                    name="Concurrent second default",
                    layout=layout.model_dump(mode="json", by_alias=True),
                    created_by=tenant.user_id,
                    status="active",
                    is_default=True,
                )
            )
            await db_session.flush()
    assert "uq_templates_one_default_per_org" in str(duplicate.value.orig)
