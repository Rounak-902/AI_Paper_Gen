"""P5-T3 lock-preserving regeneration contract tests."""

from __future__ import annotations

import json
import uuid
from decimal import Decimal
from pathlib import Path

from httpx import ASGITransport, AsyncClient
import pytest
from pydantic import ValidationError as PydanticValidationError
from sqlalchemy import select

from app.common.deps import RequestScope, get_db, get_scope
from app.core.config import get_settings
from app.main import app
from app.models.assembly import PaperAssemblyRun, PaperAssemblySlot
from app.models.membership import Membership
from app.models.paper import GeneratedPaper, PaperVersion
from app.models.question import QuestionBank
from app.modules.ai.providers.factory import create_llm_provider
from app.modules.assembly.resumable import ResumablePaperAssemblyService
from app.modules.papers.schemas import RegenerationRequest


def test_regeneration_route_is_real_and_returns_202() -> None:
    operation = app.openapi()["paths"][
        "/api/v1/papers/{paper_id}/regenerations"
    ]["post"]
    assert "202" in operation["responses"]


def test_request_requires_disjoint_explicit_target_and_lock_snapshots() -> None:
    with pytest.raises(PydanticValidationError):
        RegenerationRequest(
            requestId=uuid.uuid4(),
            sourceVersionId=uuid.uuid4(),
            targetSlotIds=["A:1"],
            lockedSlotIds=["A:1"],
        )


def test_source_version_slot_extraction_preserves_snapshot_bytes() -> None:
    source = {
        "schemaVersion": 1,
        "sections": [
            {
                "id": "A",
                "questions": [
                    {
                        "id": str(uuid.uuid4()),
                        "slotId": "A:1",
                        "sectionId": "A",
                        "stem": "Keep exactly",
                        "answer": {"text": "Same bytes"},
                    }
                ],
            }
        ],
    }
    slots = ResumablePaperAssemblyService._source_version_slots(source)
    assert slots[0] == source["sections"][0]["questions"][0]
    slots[0]["answer"]["text"] = "mutated copy"
    assert source["sections"][0]["questions"][0]["answer"]["text"] == "Same bytes"


def test_backend_extension_uses_completed_checkpoints_and_existing_residual_path() -> None:
    source = (
        Path(__file__).resolve().parents[1]
        / "app"
        / "modules"
        / "assembly"
        / "resumable.py"
    ).read_text(encoding="utf-8")
    method = source.split("async def prepare_regeneration", 1)[1].split(
        "async def process_slot", 1
    )[0]
    assert 'status="completed"' in method
    assert 'status="pending"' in method
    assert "_enqueue_slot" in method
    assert "self.solver.solve" in method
    assert "_residual_resolve" in source


@pytest.mark.db
async def test_real_endpoint_assigns_only_target_and_locked_snapshot_survives(
    db_session, tenant, curriculum
) -> None:
    membership = Membership(
        user_id=tenant.user_id,
        org_id=tenant.org_id,
        role="super_admin",
        status="active",
    )
    questions = [
        QuestionBank(
            id=uuid.UUID(f"53000000-0000-4000-8000-{index:012d}"),
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
            stem=f"Verified regeneration fixture {index}",
            answer={"text": f"Answer {index}"},
            rubric={"key_points": [f"Point {index}"]},
            learning_outcome_ids=[],
            content_hash=f"{index:064x}",
            status="approved",
            created_by=tenant.user_id,
        )
        for index in range(1, 9)
    ]
    db_session.add_all([membership, *questions])
    await db_session.flush()

    blueprint = {
        "boardId": str(curriculum.board_id),
        "classId": str(curriculum.class_id),
        "subjectId": str(curriculum.subject_id),
        "curriculumVersionId": str(curriculum.curriculum_version_id),
        "chapterIds": [str(curriculum.chapter_id)],
        "language": "en",
        "totalMarks": "8.0",
        "sections": [
            {
                "id": "A",
                "questionCount": 2,
                "marksPerQuestion": "2.0",
                "allowedQuestionTypes": ["short_answer"],
                "chapterIds": [str(curriculum.chapter_id)],
            },
            {
                "id": "B",
                "questionCount": 2,
                "marksPerQuestion": "2.0",
                "allowedQuestionTypes": ["short_answer"],
                "chapterIds": [str(curriculum.chapter_id)],
            },
        ],
        "difficultyMix": {"easy": {"targetPercent": 100, "tolerancePercentagePoints": 0}},
        "sourceMix": {"textbook": {"targetPercent": 100, "tolerancePercentagePoints": 0}},
        "bloomMix": {"understand": {"targetPercent": 100, "tolerancePercentagePoints": 0}},
        "chapterCoverage": [],
        "learningOutcomeCoverage": [],
        "generationMode": "mixed",
        "textbookFloorPercent": 70,
    }
    paper = GeneratedPaper(
        org_id=tenant.org_id,
        created_by=tenant.user_id,
        blueprint=blueprint,
        curriculum_version_id=curriculum.curriculum_version_id,
        title="P5-T3 regeneration contract",
        duration_min=10,
    )
    db_session.add(paper)
    await db_session.flush()

    def snapshot(question: QuestionBank, slot_id: str, section_id: str) -> dict:
        return {
            "id": str(question.id),
            "slotId": slot_id,
            "sectionId": section_id,
            "source": question.source,
            "qtype": question.qtype,
            "marks": str(question.marks),
            "difficulty": question.difficulty,
            "bloom": question.bloom,
            "chapterId": str(question.chapter_id),
            "stem": question.stem,
            "answer": question.answer,
            "rubric": question.rubric,
        }

    source_snapshots = [
        snapshot(questions[0], "A:1", "A"),
        snapshot(questions[1], "A:2", "A"),
        snapshot(questions[2], "B:1", "B"),
        snapshot(questions[3], "B:2", "B"),
    ]
    source_by_slot = {item["slotId"]: item for item in source_snapshots}
    locked_snapshot = source_by_slot["A:1"]
    source_version = PaperVersion(
        paper_id=paper.id,
        label="Source",
        content={
            "schemaVersion": 1,
            "sections": [
                {"id": "A", "questions": source_snapshots[:2], "marks": "4.0"},
                {"id": "B", "questions": source_snapshots[2:], "marks": "4.0"},
            ],
        },
        answer_key={
            "questions": [
                {"questionId": item["id"], "slotId": item["slotId"], "answer": item["answer"], "rubric": item["rubric"]}
                for item in source_snapshots
            ]
        },
    )
    db_session.add(source_version)
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
        for scope_name, target_slot_ids in (
            ("section", ["A:2"]),
            ("whole-paper", ["A:2", "B:1", "B:2"]),
        ):
            async with AsyncClient(
                transport=ASGITransport(app=app), base_url="http://test"
            ) as client:
                response = await client.post(
                    f"/api/v1/papers/{paper.id}/regenerations",
                    json={
                        "requestId": str(uuid.uuid4()),
                        "sourceVersionId": str(source_version.id),
                        "targetSlotIds": target_slot_ids,
                        "lockedSlotIds": ["A:1"],
                    },
                )
            assert response.status_code == 202, response.text
            run_id = uuid.UUID(response.json()["data"]["runId"])
            slots = (
                await db_session.execute(
                    select(PaperAssemblySlot)
                    .where(PaperAssemblySlot.run_id == run_id)
                    .order_by(PaperAssemblySlot.section_id, PaperAssemblySlot.position)
                )
            ).scalars().all()
            slots_by_id = {slot.slot_id: slot for slot in slots}
            assert json.dumps(slots_by_id["A:1"].snapshot, sort_keys=True) == json.dumps(
                locked_snapshot, sort_keys=True
            )
            for slot_id, slot in slots_by_id.items():
                if slot_id in target_slot_ids:
                    assert slot.status == "pending"
                    assert slot.solver_question_id not in {item.id for item in questions[:4]}
                else:
                    assert slot.status == "completed"
                    assert json.dumps(slot.snapshot, sort_keys=True) == json.dumps(
                        source_by_slot[slot_id], sort_keys=True
                    )

            provider = create_llm_provider(get_settings())
            regenerated_ids: dict[str, str] = {}
            async with provider:
                service = ResumablePaperAssemblyService(
                    session=db_session,
                    settings=get_settings(),
                    org_id=tenant.org_id,
                    provider=provider,
                )
                for slot_id in target_slot_ids:
                    result = await service.process_slot(run_id=run_id, slot_id=slot_id)
                    regenerated_ids[slot_id] = result["question_id"]
                final = await service.finalize(run_id=run_id)

            regenerated = await db_session.get(
                PaperVersion, uuid.UUID(final["paper_version_id"])
            )
            assert regenerated is not None
            after_by_slot = {
                item["slotId"]: item
                for section in regenerated.content["sections"]
                for item in section["questions"]
            }
            assert json.dumps(after_by_slot["A:1"], sort_keys=True) == json.dumps(
                locked_snapshot, sort_keys=True
            ), scope_name
            for slot_id in target_slot_ids:
                assert after_by_slot[slot_id]["id"] == regenerated_ids[slot_id]
                assert after_by_slot[slot_id]["id"] != source_by_slot[slot_id]["id"]
            for slot_id in set(source_by_slot) - set(target_slot_ids):
                assert json.dumps(after_by_slot[slot_id], sort_keys=True) == json.dumps(
                    source_by_slot[slot_id], sort_keys=True
                ), scope_name
            run = await db_session.get(PaperAssemblyRun, run_id)
            assert run.solver_snapshot["regeneration"]["lockedSlotIds"] == ["A:1"]
            print(json.dumps({
                "scope": scope_name,
                "runId": str(run_id),
                "lockedBefore": source_by_slot["A:1"],
                "lockedAfter": after_by_slot["A:1"],
                "targetBeforeIds": {slot_id: source_by_slot[slot_id]["id"] for slot_id in target_slot_ids},
                "targetAfterIds": {slot_id: after_by_slot[slot_id]["id"] for slot_id in target_slot_ids},
            }, sort_keys=True))
    finally:
        app.dependency_overrides.clear()
