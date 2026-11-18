"""Focused, database-free contract tests for the curriculum ingestion surface."""

from __future__ import annotations

import copy
import uuid

import pytest
from httpx import ASGITransport, AsyncClient
from pydantic import ValidationError as PydanticValidationError

from app.common.deps import OrgContext, get_org_context
from app.main import create_app
from app.modules.curriculum.import_schemas import (
    CurriculumImportApplyIn,
    CurriculumImportDocumentIn,
)
from app.modules.curriculum.import_service import (
    Planner,
    apply_import,
    preview_import,
    validate_document,
)


pytestmark = pytest.mark.asyncio


def _manifest() -> dict:
    return {
        "schemaVersion": 1,
        "board": {
            "code": "TEST-BOARD",
            "name": "Test Curriculum Board",
            "curriculumVersion": {
                "academicYear": "2026-27",
                "label": "Test curriculum 2026-27",
                "effectiveFrom": "2026-04-01",
                "sourceRef": "https://example.test/curriculum",
                "finalStatus": "draft",
            },
            "classes": [
                {
                    "name": "Grade 8",
                    "subjects": [
                        {
                            "name": "Mathematics",
                            "chapters": [
                                {
                                    "seq": 1,
                                    "title": "Rational numbers",
                                    "learningOutcomes": [
                                        {
                                            "code": "TEST.G8.MATH.01.LO1",
                                            "description": "Represent rational numbers.",
                                        }
                                    ],
                                }
                            ],
                        }
                    ],
                }
            ],
        },
    }


def _context(role: str) -> OrgContext:
    return OrgContext(
        org_id=uuid.uuid4(),
        user_id=uuid.uuid4(),
        role=role,
        roles=(role,),
    )


async def test_validation_summary_is_stable_and_complete() -> None:
    document = CurriculumImportDocumentIn.model_validate(_manifest())

    first = validate_document(document)
    second = validate_document(document)

    assert first.document_digest == second.document_digest
    assert first.document_digest.startswith("sha256:")
    assert first.board_code == "TEST-BOARD"
    assert first.version_key == "academicYear=2026-27"
    assert first.entity_totals == {
        "boards": 1,
        "classes": 1,
        "curriculum_versions": 1,
        "subjects": 1,
        "chapters": 1,
        "learning_outcomes": 1,
    }


async def test_semantic_duplicate_retains_the_exact_manifest_path() -> None:
    payload = copy.deepcopy(_manifest())
    outcomes = payload["board"]["classes"][0]["subjects"][0]["chapters"][0][
        "learningOutcomes"
    ]
    outcomes.append(copy.deepcopy(outcomes[0]))

    with pytest.raises(PydanticValidationError) as exc_info:
        CurriculumImportDocumentIn.model_validate(payload)

    assert "$.board.classes[0].subjects[0].chapters[0].learningOutcomes[1].code" in str(
        exc_info.value
    )


async def test_validate_endpoint_is_enveloped_and_requires_super_admin() -> None:
    app = create_app()
    app.dependency_overrides[get_org_context] = lambda: _context("teacher")
    try:
        async with AsyncClient(
            transport=ASGITransport(app=app), base_url="http://testserver"
        ) as client:
            denied = await client.post(
                "/api/v1/curriculum-imports/validate", json=_manifest()
            )
    finally:
        app.dependency_overrides.clear()

    assert denied.status_code == 403
    assert denied.json()["error"]["code"] == "FORBIDDEN"

    app.dependency_overrides[get_org_context] = lambda: _context("super_admin")
    try:
        async with AsyncClient(
            transport=ASGITransport(app=app), base_url="http://testserver"
        ) as client:
            accepted = await client.post(
                "/api/v1/curriculum-imports/validate", json=_manifest()
            )
    finally:
        app.dependency_overrides.clear()

    assert accepted.status_code == 200
    body = accepted.json()
    assert body["success"] is True
    assert body["data"]["valid"] is True
    assert body["data"]["boardCode"] == "TEST-BOARD"
    assert body["data"]["entityTotals"]["learning_outcomes"] == 1


async def test_openapi_exposes_the_three_import_operations() -> None:
    paths = create_app().openapi()["paths"]

    assert "post" in paths["/api/v1/curriculum-imports/validate"]
    assert "post" in paths["/api/v1/curriculum-imports/preview"]
    assert "post" in paths["/api/v1/curriculum-imports"]


async def test_preview_apply_and_rerun_are_idempotent(
    db_session, tenant, monkeypatch
) -> None:
    document = CurriculumImportDocumentIn.model_validate(_manifest())
    ctx = OrgContext(
        org_id=tenant.org_id,
        user_id=tenant.user_id,
        role="super_admin",
        roles=("super_admin",),
    )

    plan = await preview_import(session=db_session, ctx=ctx, document=document)

    assert plan.ready is True
    assert plan.atomic is True
    assert all(count.create == 1 for count in plan.counts.values())
    assert sum(count.skip for count in plan.counts.values()) == 0

    build_calls = 0
    original_build = Planner.build

    async def counted_build(self):
        nonlocal build_calls
        build_calls += 1
        return await original_build(self)

    monkeypatch.setattr(Planner, "build", counted_build)
    result = await apply_import(
        session=db_session,
        ctx=ctx,
        payload=CurriculumImportApplyIn(
            document=document,
            expected_plan_digest=plan.plan_digest,
        ),
    )
    await db_session.commit()

    assert result.committed is True
    assert result.board_code == "TEST-BOARD"
    assert result.board_id
    assert result.curriculum_version_id
    assert build_calls == 2

    rerun = await preview_import(session=db_session, ctx=ctx, document=document)
    assert rerun.ready is True
    assert sum(count.create for count in rerun.counts.values()) == 0
    assert all(count.skip == 1 for count in rerun.counts.values())


async def test_import_can_append_missing_rows_to_active_version(
    db_session, tenant
) -> None:
    initial_payload = _manifest()
    initial_payload["board"]["curriculumVersion"]["finalStatus"] = "active"
    initial_document = CurriculumImportDocumentIn.model_validate(initial_payload)
    ctx = OrgContext(
        org_id=tenant.org_id,
        user_id=tenant.user_id,
        role="super_admin",
        roles=("super_admin",),
    )

    initial_plan = await preview_import(
        session=db_session, ctx=ctx, document=initial_document
    )
    await apply_import(
        session=db_session,
        ctx=ctx,
        payload=CurriculumImportApplyIn(
            document=initial_document,
            expected_plan_digest=initial_plan.plan_digest,
        ),
    )
    await db_session.commit()

    append_payload = copy.deepcopy(initial_payload)
    append_payload["board"]["classes"] = [
        {
            "name": "Grade 7",
            "subjects": [
                {
                    "name": "Mathematics",
                    "chapters": [
                        {
                            "seq": 1,
                            "title": "Geometric Twins",
                            "learningOutcomes": [
                                {
                                    "code": "MATH-07-CH01-LO01",
                                    "description": "Identify congruent plane figures.",
                                }
                            ],
                        }
                    ],
                }
            ],
        }
    ]
    append_document = CurriculumImportDocumentIn.model_validate(append_payload)
    append_plan = await preview_import(
        session=db_session, ctx=ctx, document=append_document
    )

    assert append_plan.ready is True
    assert append_plan.counts["boards"].skip == 1
    assert append_plan.counts["curriculum_versions"].skip == 1
    assert append_plan.counts["classes"].create == 1
    assert append_plan.counts["subjects"].create == 1
    assert append_plan.counts["chapters"].create == 1
    assert append_plan.counts["learning_outcomes"].create == 1

    result = await apply_import(
        session=db_session,
        ctx=ctx,
        payload=CurriculumImportApplyIn(
            document=append_document,
            expected_plan_digest=append_plan.plan_digest,
        ),
    )
    await db_session.commit()

    assert result.committed is True
    rerun = await preview_import(
        session=db_session, ctx=ctx, document=append_document
    )
    assert rerun.ready is True
    assert sum(count.create for count in rerun.counts.values()) == 0
