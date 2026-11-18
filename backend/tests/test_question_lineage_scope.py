"""Teacher subject-scope regression coverage for question lineage reads."""

from __future__ import annotations

import uuid
from types import SimpleNamespace

import pytest

from app.common.deps import RequestScope
from app.common.errors import NotFoundError
from app.modules.questions import service as question_service
from app.modules.questions.repository import QuestionRepository


async def test_lineage_rejects_a_question_outside_teacher_subject_scope(
    monkeypatch,
) -> None:
    org_id = uuid.uuid4()
    question_id = uuid.uuid4()
    subject_id = uuid.uuid4()
    calls: list[tuple[str, object]] = []

    class FakeRepository:
        def __init__(self, session, repository_org_id):
            assert session is fake_session
            assert repository_org_id == org_id

        async def get_question(self, requested_id, *, subject_ids=None):
            calls.append(("get_question", subject_ids))
            assert requested_id == question_id
            return None

        async def get_lineage(self, root_id, *, subject_ids=None):  # pragma: no cover
            calls.append(("get_lineage", subject_ids))
            return []

    fake_session = object()
    monkeypatch.setattr(question_service, "QuestionRepository", FakeRepository)
    scope = RequestScope(
        org_id=org_id,
        user_id=uuid.uuid4(),
        role="teacher",
        subject_ids=(subject_id,),
    )

    with pytest.raises(NotFoundError, match="Question not found"):
        await question_service.get_lineage(
            session=fake_session,
            scope=scope,
            org_id=org_id,
            question_id=question_id,
        )

    assert calls == [("get_question", (subject_id,))]


async def test_lineage_applies_teacher_scope_to_every_returned_version(
    monkeypatch,
) -> None:
    org_id = uuid.uuid4()
    question_id = uuid.uuid4()
    root_id = uuid.uuid4()
    subject_id = uuid.uuid4()
    calls: list[tuple[str, object]] = []

    class FakeRepository:
        def __init__(self, session, repository_org_id):
            assert session is fake_session
            assert repository_org_id == org_id

        async def get_question(self, requested_id, *, subject_ids=None):
            calls.append(("get_question", subject_ids))
            assert requested_id == question_id
            return SimpleNamespace(id=question_id, root_question_id=root_id)

        async def get_lineage(self, requested_root_id, *, subject_ids=None):
            calls.append(("get_lineage", subject_ids))
            assert requested_root_id == root_id
            return []

    fake_session = object()
    monkeypatch.setattr(question_service, "QuestionRepository", FakeRepository)
    scope = RequestScope(
        org_id=org_id,
        user_id=uuid.uuid4(),
        role="teacher",
        subject_ids=(subject_id,),
    )

    assert await question_service.get_lineage(
        session=fake_session,
        scope=scope,
        org_id=org_id,
        question_id=question_id,
    ) == []
    assert calls == [
        ("get_question", (subject_id,)),
        ("get_lineage", (subject_id,)),
    ]


async def test_repository_lineage_fails_closed_for_teacher_without_assignments() -> None:
    class NoExecuteSession:
        async def execute(self, _statement):  # pragma: no cover
            raise AssertionError("empty teacher scope must not execute a lineage query")

    repo = QuestionRepository(NoExecuteSession(), uuid.uuid4())
    assert await repo.get_lineage(uuid.uuid4(), subject_ids=()) == []
