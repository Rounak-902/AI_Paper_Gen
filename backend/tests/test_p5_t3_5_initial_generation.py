"""Initial paper submission contract and transactional outbox proof."""

from __future__ import annotations

import uuid
from decimal import Decimal

from httpx import ASGITransport, AsyncClient
import pytest
from sqlalchemy import select

from app.common.deps import RequestScope, get_db, get_scope
from app.main import app
from app.models.assembly import TaskOutbox
from app.models.paper import GeneratedPaper
from app.models.question import QuestionBank
from app.modules.feasibility.schemas import FeasibilityRequest
from app.modules.papers.schemas import GeneratePaperRequest


def test_generation_route_is_202_and_reuses_feasibility_blueprint_schema() -> None:
    operation = app.openapi()["paths"][
        "/api/v1/orgs/{org_id}/papers/generate"
    ]["post"]
    assert "202" in operation["responses"]
    assert GeneratePaperRequest.model_fields["blueprint"].annotation is FeasibilityRequest


@pytest.mark.db
async def test_real_endpoint_creates_paper_and_prepare_outbox_in_one_session(
    db_session, tenant, curriculum
) -> None:
    questions = [
        QuestionBank(
            id=uuid.UUID(f"54000000-0000-4000-8000-{index:012d}"),
            org_id=tenant.org_id,
            scope="org",
            source="textbook",
            board_id=curriculum.board_id,
            class_id=curriculum.class_id,
            subject_id=curriculum.subject_id,
            chapter_id=curriculum.chapter_id,
            qtype="short_answer",
            marks=Decimal("2.0"),
            difficulty="easy",
            bloom="understand",
            language="en",
            stem=f"Initial generation fixture {index}",
            answer={"text": f"Answer {index}"},
            rubric={"key_points": [f"Point {index}"]},
            learning_outcome_ids=[],
            content_hash=f"{5400 + index:064x}",
            status="approved",
            created_by=tenant.user_id,
        )
        for index in range(1, 3)
    ]
    db_session.add_all(questions)
    await db_session.flush()

    blueprint = {
        "boardId": str(curriculum.board_id),
        "classId": str(curriculum.class_id),
        "subjectId": str(curriculum.subject_id),
        "curriculumVersionId": str(curriculum.curriculum_version_id),
        "chapterIds": [str(curriculum.chapter_id)],
        "language": "en",
        "totalMarks": "4.0",
        "sections": [
            {
                "id": "A",
                "questionCount": 2,
                "marksPerQuestion": "2.0",
                "allowedQuestionTypes": ["short_answer"],
                "chapterIds": [str(curriculum.chapter_id)],
            }
        ],
        "difficultyMix": {
            "easy": {"targetPercent": 100, "tolerancePercentagePoints": 0}
        },
        "sourceMix": {
            "textbook": {"targetPercent": 100, "tolerancePercentagePoints": 0}
        },
        "bloomMix": {
            "understand": {"targetPercent": 100, "tolerancePercentagePoints": 0}
        },
        "chapterCoverage": [],
        "learningOutcomeCoverage": [],
        "generationMode": "mixed",
        "textbookFloorPercent": 70,
    }
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
            response = await client.post(
                f"/api/v1/orgs/{tenant.org_id}/papers/generate",
                json={
                    "title": "  Browser-ready paper  ",
                    "durationMinutes": 30,
                    "blueprint": blueprint,
                },
            )
    finally:
        app.dependency_overrides.clear()

    assert response.status_code == 202, response.text
    accepted = response.json()["data"]
    assert accepted["jobId"] == accepted["outboxId"]

    paper = await db_session.get(GeneratedPaper, uuid.UUID(accepted["paperId"]))
    outbox = await db_session.get(TaskOutbox, uuid.UUID(accepted["outboxId"]))
    assert paper is not None
    assert paper.title == "Browser-ready paper"
    assert paper.created_by == tenant.user_id
    assert paper.blueprint == FeasibilityRequest.model_validate(blueprint).model_dump(
        mode="json", by_alias=True
    )
    assert outbox is not None
    assert outbox.task_name == "papergen.workers.prepare_paper"
    assert outbox.payload["paper_id"] == accepted["paperId"]
    assert outbox.payload["org_id"] == str(tenant.org_id)


@pytest.mark.db
async def test_infeasible_submission_creates_neither_paper_nor_outbox(
    db_session, tenant, curriculum
) -> None:
    scope = RequestScope(
        org_id=tenant.org_id,
        user_id=tenant.user_id,
        role="super_admin",
    )

    async def override_db():
        yield db_session

    before_papers = len((await db_session.execute(select(GeneratedPaper))).scalars().all())
    before_outbox = len((await db_session.execute(select(TaskOutbox))).scalars().all())
    app.dependency_overrides[get_scope] = lambda: scope
    app.dependency_overrides[get_db] = override_db
    try:
        async with AsyncClient(
            transport=ASGITransport(app=app), base_url="http://test"
        ) as client:
            response = await client.post(
                f"/api/v1/orgs/{tenant.org_id}/papers/generate",
                json={
                    "title": "Impossible paper",
                    "durationMinutes": 30,
                    "blueprint": {
                        "boardId": str(curriculum.board_id),
                        "classId": str(curriculum.class_id),
                        "subjectId": str(curriculum.subject_id),
                        "curriculumVersionId": str(curriculum.curriculum_version_id),
                        "chapterIds": [str(curriculum.chapter_id)],
                        "language": "en",
                        "totalMarks": "4.0",
                        "sections": [{
                            "id": "A",
                            "questionCount": 2,
                            "marksPerQuestion": "2.0",
                            "allowedQuestionTypes": ["short_answer"],
                            "chapterIds": [str(curriculum.chapter_id)],
                        }],
                        "generationMode": "mixed",
                        "textbookFloorPercent": 70,
                    },
                },
            )
    finally:
        app.dependency_overrides.clear()

    assert response.status_code == 422, response.text
    assert response.json()["error"]["code"] == "BLUEPRINT_INFEASIBLE"
    assert len((await db_session.execute(select(GeneratedPaper))).scalars().all()) == before_papers
    assert len((await db_session.execute(select(TaskOutbox))).scalars().all()) == before_outbox
