"""Additive Program assignment read-back contract and database behavior."""

from __future__ import annotations

import uuid

import pytest
from httpx import ASGITransport, AsyncClient

from app.common.deps import OrgContext, get_db, get_org_context
from app.main import create_app
from app.models import Program, ProgramSubject, ProgramTeacher
from app.modules.programs import service as program_service


def _context(org_id: uuid.UUID, role: str = "super_admin") -> OrgContext:
    return OrgContext(
        org_id=org_id,
        user_id=uuid.uuid4(),
        role=role,
        roles=(role,),
    )


def test_assignment_readback_openapi_is_additive() -> None:
    spec = create_app().openapi()
    program_properties = spec["components"]["schemas"]["ProgramOut"]["properties"]

    # Lock the pre-existing P2 response shape: assignment read-back is a new route,
    # not an expansion of list/create/update ProgramOut payloads.
    assert set(program_properties) == {
        "id",
        "orgId",
        "branchId",
        "name",
        "examTarget",
        "academicYear",
        "status",
        "rowVersion",
        "createdAt",
        "updatedAt",
    }
    operation = spec["paths"][
        "/api/v1/orgs/{org_id}/programs/{program_id}/assignments"
    ]["get"]
    response_ref = operation["responses"]["200"]["content"]["application/json"][
        "schema"
    ]["$ref"]
    assert "SuccessEnvelope_ProgramAssignmentsOut" in response_ref


async def test_assignment_readback_route_is_enveloped_and_admin_only(monkeypatch) -> None:
    org_id = uuid.uuid4()
    program_id = uuid.uuid4()
    captured: dict[str, object] = {}

    async def override_db():
        yield object()

    async def fake_get_program_assignments(**kwargs):
        captured.update(kwargs)
        return {
            "programId": program_id,
            "teacherIds": [uuid.uuid4()],
            "subjectIds": [uuid.uuid4()],
        }

    app = create_app()
    app.dependency_overrides[get_db] = override_db
    monkeypatch.setattr(
        program_service, "get_program_assignments", fake_get_program_assignments
    )

    app.dependency_overrides[get_org_context] = lambda: _context(org_id, "teacher")
    async with AsyncClient(
        transport=ASGITransport(app=app), base_url="http://testserver"
    ) as client:
        denied = await client.get(
            f"/api/v1/orgs/{org_id}/programs/{program_id}/assignments"
        )
    assert denied.status_code == 403
    assert captured == {}

    app.dependency_overrides[get_org_context] = lambda: _context(org_id)
    async with AsyncClient(
        transport=ASGITransport(app=app), base_url="http://testserver"
    ) as client:
        accepted = await client.get(
            f"/api/v1/orgs/{org_id}/programs/{program_id}/assignments"
        )
    app.dependency_overrides.clear()

    assert accepted.status_code == 200
    assert accepted.json()["data"]["programId"] == str(program_id)
    assert captured["org_id"] == org_id
    assert captured["program_id"] == program_id


@pytest.mark.db
async def test_assignment_readback_returns_persisted_bridge_rows(
    db_session, tenant, curriculum
) -> None:
    program = Program(org_id=tenant.org_id, name=f"Readback {uuid.uuid4()}")
    db_session.add(program)
    await db_session.flush()
    db_session.add_all(
        [
            ProgramTeacher(program_id=program.id, user_id=tenant.user_id),
            ProgramSubject(program_id=program.id, subject_id=curriculum.subject_id),
        ]
    )
    await db_session.commit()

    result = await program_service.get_program_assignments(
        session=db_session,
        ctx=_context(tenant.org_id),
        org_id=tenant.org_id,
        program_id=program.id,
    )

    assert result.program_id == program.id
    assert result.teacher_ids == [tenant.user_id]
    assert result.subject_ids == [curriculum.subject_id]
