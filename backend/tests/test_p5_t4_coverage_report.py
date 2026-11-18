"""P5-T4 reads final coverage facts from the generated version verbatim."""

from __future__ import annotations

from decimal import Decimal

from httpx import ASGITransport, AsyncClient
import pytest

from app.common.deps import RequestScope, get_db, get_scope
from app.main import app
from app.models.paper import GeneratedPaper, PaperVersion


@pytest.mark.db
async def test_coverage_report_returns_persisted_final_solver_output(
    db_session, tenant, curriculum
) -> None:
    paper = GeneratedPaper(
        org_id=tenant.org_id,
        created_by=tenant.user_id,
        curriculum_version_id=curriculum.curriculum_version_id,
        blueprint={"schema": "test"},
        title="Generated coverage proof",
        total_marks=Decimal("4.0"),
        duration_min=30,
        status="draft",
    )
    db_session.add(paper)
    await db_session.flush()
    persisted_coverage = [
        {
            "dimension": "difficulty",
            "target": "easy",
            "targetValue": "50",
            "actual": "50",
            "satisfied": True,
        },
        {
            "dimension": "chapter",
            "target": str(curriculum.chapter_id),
            "targetValue": "100",
            "actual": "100",
            "satisfied": True,
        },
    ]
    persisted_compromises = [
        {
            "tier": "bloom",
            "constraint": "bloom:apply",
            "target": "50",
            "actual": "25",
            "deviation": "25",
            "reason": "Exact Bloom balance was unavailable.",
        }
    ]
    version = PaperVersion(
        paper_id=paper.id,
        label="v1",
        content={
            "schemaVersion": 1,
            "sections": [],
            "assembly": {
                "solverOutcome": "feasible-with-compromise",
                "coverage": persisted_coverage,
                "compromises": persisted_compromises,
            },
        },
        answer_key={"questions": []},
    )
    db_session.add(version)
    await db_session.flush()

    scope = RequestScope(
        org_id=tenant.org_id,
        user_id=tenant.user_id,
        role="super_admin",
    )

    async def override_db():
        yield db_session

    app.dependency_overrides[get_scope] = lambda: scope
    app.dependency_overrides[get_db] = override_db
    try:
        async with AsyncClient(
            transport=ASGITransport(app=app), base_url="http://test"
        ) as client:
            response = await client.get(
                f"/api/v1/papers/{paper.id}/versions/{version.id}/coverage-report"
            )
    finally:
        app.dependency_overrides.clear()

    assert response.status_code == 200, response.text
    report = response.json()["data"]
    assert report["source"] == "persisted-final-exact-selection"
    assert report["solverOutcome"] == "feasible-with-compromise"
    assert report["coverage"] == persisted_coverage
    assert report["compromises"] == persisted_compromises

