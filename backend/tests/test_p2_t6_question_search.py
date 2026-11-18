"""P2-T6 faceted question search, FTS ranking, visibility, and pagination."""

from __future__ import annotations

import decimal
import uuid

import pytest
from httpx import ASGITransport, AsyncClient
from pydantic import ValidationError as PydanticValidationError

from app.common.deps import RequestScope, get_db, get_scope
from app.main import create_app
from app.models import Chapter, Organization, QuestionBank, Subject
from app.modules.questions import service as question_service
from app.modules.questions.repository import QuestionRepository
from app.modules.questions.schemas import QuestionSearchParams


def _question(
    *,
    org_id: uuid.UUID | None,
    subject_id: uuid.UUID | None,
    chapter_id: uuid.UUID | None,
    board_id: uuid.UUID | None,
    class_id: uuid.UUID | None,
    stem: str,
    status: str = "approved",
    scope: str = "org",
    qtype: str = "mcq",
    difficulty: str = "medium",
    bloom: str = "apply",
    source: str = "textbook",
    is_current: bool = True,
    question_id: uuid.UUID | None = None,
    root_question_id: uuid.UUID | None = None,
    previous_version_id: uuid.UUID | None = None,
    version_number: int = 1,
    answer: object | None = None,
    rubric: object | None = None,
) -> QuestionBank:
    question_id = question_id or uuid.uuid4()
    return QuestionBank(
        id=question_id,
        root_question_id=root_question_id or question_id,
        previous_version_id=previous_version_id,
        version_number=version_number,
        is_current=is_current,
        org_id=org_id,
        scope=scope,
        source=source,
        board_id=board_id,
        class_id=class_id,
        subject_id=subject_id,
        chapter_id=chapter_id,
        qtype=qtype,
        marks=decimal.Decimal("1.0"),
        difficulty=difficulty,
        bloom=bloom,
        language="en",
        stem=stem,
        options=[
            {"text": "A", "is_correct": True},
            {"text": "B", "is_correct": False},
        ],
        answer=answer if answer is not None else {"index": 0},
        rubric=rubric,
        learning_outcome_ids=[],
        competency_ids=[],
        meta={},
        content_hash=uuid.uuid4().hex,
        status=status,
    )


def test_search_parameter_contract_defaults_and_validation() -> None:
    params = QuestionSearchParams()
    assert params.status == "approved"
    assert params.current_only is True
    assert params.page == 1
    assert params.page_size == 20
    assert params.offset == 0

    second_page = QuestionSearchParams(page=2, page_size=7, q="   photosynthesis   ")
    assert second_page.q == "photosynthesis"
    assert second_page.offset == 7

    assert QuestionSearchParams(q="   ").q is None
    with pytest.raises(PydanticValidationError):
        QuestionSearchParams(bloom="invent")
    with pytest.raises(PydanticValidationError):
        QuestionSearchParams(page_size=101)


def test_openapi_exposes_every_p2_t6_facet_and_enveloped_list() -> None:
    operation = create_app().openapi()["paths"][
        "/api/v1/orgs/{org_id}/questions"
    ]["get"]
    parameters = {parameter["name"] for parameter in operation["parameters"]}
    assert {
        "q",
        "board_id",
        "class_id",
        "subject_id",
        "chapter_id",
        "qtype",
        "difficulty",
        "bloom",
        "source",
        "status",
        "page",
        "pageSize",
        "current_only",
    } <= parameters

    response_schema = operation["responses"]["200"]["content"][
        "application/json"
    ]["schema"]
    assert "SuccessEnvelope_list_QuestionOut" in response_schema["$ref"]


async def test_list_route_puts_pagination_in_envelope_meta(monkeypatch) -> None:
    org_id = uuid.uuid4()
    captured: dict[str, object] = {}

    async def override_scope() -> RequestScope:
        return RequestScope(
            org_id=org_id,
            user_id=uuid.uuid4(),
            role="coaching_admin",
            subject_ids=None,
        )

    async def override_db():
        yield object()

    async def fake_list_questions(**kwargs):
        captured.update(kwargs)
        return [], 5

    app = create_app()
    app.dependency_overrides[get_scope] = override_scope
    app.dependency_overrides[get_db] = override_db
    monkeypatch.setattr(question_service, "list_questions", fake_list_questions)

    async with AsyncClient(
        transport=ASGITransport(app=app), base_url="http://test"
    ) as client:
        response = await client.get(
            f"/api/v1/orgs/{org_id}/questions",
            params={"page": 3, "pageSize": 2, "status": "draft", "bloom": "apply"},
        )
        invalid = await client.get(
            f"/api/v1/orgs/{org_id}/questions",
            params={"not_a_search_parameter": "value"},
        )

    assert response.status_code == 200
    body = response.json()
    assert body["data"] == []
    assert body["meta"]["pagination"] == {
        "page": 3,
        "pageSize": 2,
        "total": 5,
        "hasNext": True,
    }
    assert captured["params"].status == "draft"
    assert captured["params"].bloom == "apply"
    assert captured["params"].offset == 4
    assert invalid.status_code == 422


@pytest.mark.db
async def test_facets_fts_ranking_status_and_exact_count(
    db_session, tenant, curriculum
) -> None:
    term = f"p2t6rank{uuid.uuid4().hex[:10]}"
    high = _question(
        org_id=tenant.org_id,
        subject_id=curriculum.subject_id,
        chapter_id=curriculum.chapter_id,
        board_id=curriculum.board_id,
        class_id=curriculum.class_id,
        stem=f"{term} {term} {term} chlorophyll {term}",
        rubric={"key_points": [term, term]},
    )
    low = _question(
        org_id=tenant.org_id,
        subject_id=curriculum.subject_id,
        chapter_id=curriculum.chapter_id,
        board_id=curriculum.board_id,
        class_id=curriculum.class_id,
        stem=f"Explain {term} once",
    )
    wrong_bloom = _question(
        org_id=tenant.org_id,
        subject_id=curriculum.subject_id,
        chapter_id=curriculum.chapter_id,
        board_id=curriculum.board_id,
        class_id=curriculum.class_id,
        stem=f"{term} decoy",
        bloom="analyze",
    )
    draft = _question(
        org_id=tenant.org_id,
        subject_id=curriculum.subject_id,
        chapter_id=curriculum.chapter_id,
        board_id=curriculum.board_id,
        class_id=curriculum.class_id,
        stem=f"{term} draft",
        status="draft",
    )
    answer_only = _question(
        org_id=tenant.org_id,
        subject_id=curriculum.subject_id,
        chapter_id=curriculum.chapter_id,
        board_id=curriculum.board_id,
        class_id=curriculum.class_id,
        stem="This stem deliberately omits the target lexeme",
        answer={"text": f"The answer contains {term}"},
    )
    db_session.add_all([high, low, wrong_bloom, draft, answer_only])
    await db_session.flush()

    repo = QuestionRepository(db_session, tenant.org_id)
    first_page = await repo.search_questions(
        query_text=term,
        board_id=curriculum.board_id,
        class_id=curriculum.class_id,
        subject_id=curriculum.subject_id,
        chapter_id=curriculum.chapter_id,
        qtype="mcq",
        difficulty="medium",
        bloom="apply",
        source="textbook",
        status="approved",
        limit=1,
    )
    assert first_page.total == 3
    assert [question.id for question in first_page.questions] == [high.id]

    full_page = await repo.search_questions(
        query_text=term,
        board_id=curriculum.board_id,
        class_id=curriculum.class_id,
        subject_id=curriculum.subject_id,
        chapter_id=curriculum.chapter_id,
        qtype="mcq",
        difficulty="medium",
        bloom="apply",
        source="textbook",
        status="approved",
        limit=10,
    )
    ids = [question.id for question in full_page.questions]
    assert ids[0] == high.id
    assert set(ids) == {high.id, low.id, answer_only.id}
    assert wrong_bloom.id not in ids
    assert draft.id not in ids

    draft_page = await repo.search_questions(query_text=term, status="draft")
    assert draft_page.total == 1
    assert draft_page.questions[0].id == draft.id


@pytest.mark.db
async def test_current_teacher_and_global_org_visibility_share_the_search_path(
    db_session, tenant, curriculum
) -> None:
    term = f"p2t6scope{uuid.uuid4().hex[:10]}"

    second_subject = Subject(
        class_id=curriculum.class_id,
        curriculum_version_id=curriculum.curriculum_version_id,
        name=f"Outside teacher scope {term}",
    )
    db_session.add(second_subject)
    await db_session.flush()
    second_chapter = Chapter(
        subject_id=second_subject.id,
        curriculum_version_id=curriculum.curriculum_version_id,
        seq=99,
        title=f"Outside scope {term}",
    )
    other_org = Organization(name=f"Other tenant {term}")
    db_session.add_all([second_chapter, other_org])
    await db_session.flush()

    org_question = _question(
        org_id=tenant.org_id,
        subject_id=curriculum.subject_id,
        chapter_id=curriculum.chapter_id,
        board_id=curriculum.board_id,
        class_id=curriculum.class_id,
        stem=f"{term} organization",
    )
    global_question = _question(
        org_id=None,
        scope="global",
        subject_id=curriculum.subject_id,
        chapter_id=curriculum.chapter_id,
        board_id=curriculum.board_id,
        class_id=curriculum.class_id,
        stem=f"{term} global",
    )
    outside_subject = _question(
        org_id=tenant.org_id,
        subject_id=second_subject.id,
        chapter_id=second_chapter.id,
        board_id=curriculum.board_id,
        class_id=curriculum.class_id,
        stem=f"{term} outside subject",
    )
    other_tenant = _question(
        org_id=other_org.id,
        subject_id=curriculum.subject_id,
        chapter_id=curriculum.chapter_id,
        board_id=curriculum.board_id,
        class_id=curriculum.class_id,
        stem=f"{term} other tenant",
    )

    root_id = uuid.uuid4()
    old_version = _question(
        question_id=root_id,
        root_question_id=root_id,
        org_id=tenant.org_id,
        subject_id=curriculum.subject_id,
        chapter_id=curriculum.chapter_id,
        board_id=curriculum.board_id,
        class_id=curriculum.class_id,
        stem=f"{term} superseded version",
        is_current=False,
    )
    new_id = uuid.uuid4()
    current_version = _question(
        question_id=new_id,
        root_question_id=root_id,
        previous_version_id=root_id,
        version_number=2,
        org_id=tenant.org_id,
        subject_id=curriculum.subject_id,
        chapter_id=curriculum.chapter_id,
        board_id=curriculum.board_id,
        class_id=curriculum.class_id,
        stem=f"{term} current version",
    )
    db_session.add_all(
        [
            org_question,
            global_question,
            outside_subject,
            other_tenant,
            old_version,
            current_version,
        ]
    )
    await db_session.flush()

    repo = QuestionRepository(db_session, tenant.org_id)
    admin = await repo.search_questions(query_text=term, limit=20)
    admin_ids = {question.id for question in admin.questions}
    assert {org_question.id, global_question.id, outside_subject.id, current_version.id} <= admin_ids
    assert other_tenant.id not in admin_ids
    assert old_version.id not in admin_ids

    teacher = await repo.search_questions(
        query_text=term,
        subject_ids=(curriculum.subject_id,),
        limit=20,
    )
    teacher_ids = {question.id for question in teacher.questions}
    assert {org_question.id, global_question.id, current_version.id} <= teacher_ids
    assert outside_subject.id not in teacher_ids

    no_assignments = await repo.search_questions(
        query_text=term,
        subject_ids=(),
        limit=20,
    )
    assert no_assignments.total == 0
    assert no_assignments.questions == ()

    history = await repo.search_questions(
        query_text=term,
        current_only=False,
        limit=20,
    )
    history_ids = {question.id for question in history.questions}
    assert {old_version.id, current_version.id} <= history_ids
