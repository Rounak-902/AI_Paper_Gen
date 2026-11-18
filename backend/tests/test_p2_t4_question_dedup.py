"""P2-T4 question-bank deduplication against real PostgreSQL.

These tests deliberately exercise the module's public repository/service paths rather
than reimplementing their SQL.  The important boundary is BR-8: exact rejection belongs
to first-version creation, while an approved question may fork to an identical hash when
the edit changes metadata that is not part of the P2-T3 hash contract.
"""

from __future__ import annotations

import hashlib
import json
import uuid
from decimal import Decimal
from typing import Any

import pytest
import sqlalchemy as sa

from app.common.deps import RequestScope
from app.models import Organization, QuestionBank, Subject
from app.modules.questions import schemas, service
from app.modules.questions.repository import (
    ExactDuplicateQuestionError,
    QuestionRepository,
    _compute_content_hash,
)

pytestmark = pytest.mark.db


_DEFAULT = object()


async def _create_question(
    repo: QuestionRepository,
    tenant: Any,
    curriculum: Any,
    *,
    stem: str,
    qtype: str = "short_answer",
    options: Any = None,
    answer: Any = None,
    rubric: Any = None,
    marks: Decimal = Decimal("2.0"),
    difficulty: str = "medium",
    scope: str = "org",
    org_id: object = _DEFAULT,
    subject_id: object = _DEFAULT,
    chapter_id: object = _DEFAULT,
):
    """Create through the one first-version path P2-T4 is meant to guard."""

    resolved_org_id = tenant.org_id if org_id is _DEFAULT else org_id
    resolved_subject_id = (
        curriculum.subject_id if subject_id is _DEFAULT else subject_id
    )
    resolved_chapter_id = (
        curriculum.chapter_id if chapter_id is _DEFAULT else chapter_id
    )
    return await repo.create_question(
        qtype=qtype,
        marks=marks,
        difficulty=difficulty,
        bloom="understand",
        language="en",
        stem=stem,
        source="institute_bank",
        scope=scope,
        org_id=resolved_org_id,  # type: ignore[arg-type]
        created_by=tenant.user_id,
        board_id=curriculum.board_id,
        class_id=curriculum.class_id,
        subject_id=resolved_subject_id,  # type: ignore[arg-type]
        chapter_id=resolved_chapter_id,  # type: ignore[arg-type]
        options=options,
        answer=answer,
        rubric=rubric,
    )


def _request_scope(tenant: Any) -> RequestScope:
    return RequestScope(
        org_id=tenant.org_id,
        user_id=tenant.user_id,
        role="coaching_admin",
    )


def _short_answer_payload(curriculum: Any, stem: str) -> schemas.QuestionCreateIn:
    return schemas.QuestionCreateIn(
        qtype="short_answer",
        marks=Decimal("2.0"),
        difficulty="medium",
        bloom="understand",
        language="en",
        source="institute_bank",
        board_id=curriculum.board_id,
        class_id=curriculum.class_id,
        subject_id=curriculum.subject_id,
        chapter_id=curriculum.chapter_id,
        stem=stem,
        answer={"text": "A deterministic P2-T4 verification answer."},
        rubric={"key_points": ["P2-T4"]},
    )


def test_p2_t3_content_hash_contract_remains_byte_for_byte_unchanged():
    """P2-T4 enforces the existing hash; it does not redefine normalization."""

    content = {
        "qtype": "short_answer",
        "stem": "  Explain  osmosis.  ",
        "options": {"second": 2, "first": 1},
        "answer": {"text": "Water movement"},
        "rubric": {"key_points": ["membrane", "gradient"]},
    }
    canonical = json.dumps(
        content,
        sort_keys=True,
        ensure_ascii=False,
        default=str,
    )
    expected = hashlib.sha256(canonical.encode("utf-8")).hexdigest()

    actual = _compute_content_hash(**content)
    assert actual == expected
    assert actual == _compute_content_hash(
        content["qtype"],
        content["stem"],
        {"first": 1, "second": 2},
        content["answer"],
        content["rubric"],
    ), "JSON object key order is canonicalized"
    assert actual != _compute_content_hash(
        content["qtype"],
        " explain osmosis. ",
        content["options"],
        content["answer"],
        content["rubric"],
    ), "case and whitespace are intentionally not normalized by the P2-T3 hash"
    assert actual != _compute_content_hash(
        "long_answer",
        content["stem"],
        content["options"],
        content["answer"],
        content["rubric"],
    ), "qtype is part of the existing hash"
    assert actual != _compute_content_hash(
        content["qtype"],
        content["stem"],
        content["options"],
        content["answer"],
        {"key_points": ["changed"]},
    ), "rubric is part of the existing hash"


async def test_exact_duplicate_rejection_names_the_existing_question(
    db_session, tenant, curriculum
):
    repo = QuestionRepository(db_session, tenant.org_id)
    stem = f"P2-T4 exact collision {uuid.uuid4()}"
    content = {
        "stem": stem,
        "answer": {"text": "Exact answer"},
        "rubric": {"key_points": ["exact"]},
    }
    first = await _create_question(repo, tenant, curriculum, **content)

    with pytest.raises(ExactDuplicateQuestionError) as exc_info:
        await _create_question(repo, tenant, curriculum, **content)

    assert exc_info.value.existing_question.id == first.question.id
    assert (
        exc_info.value.existing_question.content_hash
        == first.question.content_hash
    )
    stored_count = await db_session.scalar(
        sa.select(sa.func.count())
        .select_from(QuestionBank)
        .where(QuestionBank.content_hash == first.question.content_hash)
    )
    assert stored_count == 1, "the rejected request must not insert a second row"


async def test_similarity_at_or_above_point_80_warns_and_still_persists(
    db_session, tenant, curriculum
):
    token = uuid.uuid4().hex
    original_stem = (
        f"P2T4 {token}: A passenger train travels 180 kilometres in exactly "
        "three hours at constant speed. Calculate its average speed and show the formula."
    )
    similar_stem = (
        f"P2T4 {token}: A passenger train travels 180 kilometres in precisely "
        "three hours at constant speed. Calculate its average speed and show the formula."
    )
    measured = float(
        await db_session.scalar(
            sa.select(sa.func.similarity(original_stem, similar_stem))
        )
    )
    assert 0.80 <= measured < 1.0, (
        "the fixture must exercise the documented warning band, got "
        f"similarity={measured}"
    )

    first = await service.create_question(
        session=db_session,
        scope=_request_scope(tenant),
        org_id=tenant.org_id,
        payload=_short_answer_payload(curriculum, original_stem),
    )
    second = await service.create_question(
        session=db_session,
        scope=_request_scope(tenant),
        org_id=tenant.org_id,
        payload=_short_answer_payload(curriculum, similar_stem),
    )

    assert first.warnings == []
    assert len(second.warnings) == 1
    warning = second.warnings[0]
    assert warning.code == "NEAR_DUPLICATE"
    assert warning.threshold == pytest.approx(0.80)
    candidate = next(
        item
        for item in warning.candidates
        if item.existing_question_id == first.id
    )
    assert candidate.similarity == pytest.approx(measured, abs=1e-6)
    assert await db_session.scalar(
        sa.select(sa.func.count())
        .select_from(QuestionBank)
        .where(QuestionBank.id.in_([first.id, second.id]))
    ) == 2, "a near duplicate is advisory and both rows must persist"


async def test_similarity_below_point_80_does_not_warn(
    db_session, tenant, curriculum
):
    token = uuid.uuid4().hex
    first_stem = f"P2T4 {token}: Calculate the roots of a quadratic equation."
    unrelated_stem = f"P2T4 {token}: Describe rainfall patterns in the Amazon basin."
    measured = float(
        await db_session.scalar(sa.select(sa.func.similarity(first_stem, unrelated_stem)))
    )
    assert measured < 0.80, f"below-threshold fixture drifted to {measured}"

    first = await service.create_question(
        session=db_session,
        scope=_request_scope(tenant),
        org_id=tenant.org_id,
        payload=_short_answer_payload(curriculum, first_stem),
    )
    second = await service.create_question(
        session=db_session,
        scope=_request_scope(tenant),
        org_id=tenant.org_id,
        payload=_short_answer_payload(curriculum, unrelated_stem),
    )

    assert first.warnings == []
    assert second.warnings == []


async def test_dedup_scope_is_same_subject_and_does_not_cross_organizations(
    db_session, tenant, curriculum
):
    other_subject = Subject(
        class_id=curriculum.class_id,
        curriculum_version_id=curriculum.curriculum_version_id,
        name=f"P2-T4 second subject {uuid.uuid4().hex}",
    )
    other_org = Organization(name=f"P2-T4 other org {uuid.uuid4().hex}")
    db_session.add_all([other_subject, other_org])
    await db_session.flush()

    stem = f"P2-T4 subject and org scope {uuid.uuid4()}"
    content = {
        "stem": stem,
        "answer": {"text": "Scoped answer"},
        "rubric": {"key_points": ["scope"]},
    }
    own_repo = QuestionRepository(db_session, tenant.org_id)
    first = await _create_question(own_repo, tenant, curriculum, **content)

    different_subject = await _create_question(
        own_repo,
        tenant,
        curriculum,
        subject_id=other_subject.id,
        chapter_id=None,
        **content,
    )
    assert different_subject.question.id != first.question.id
    assert different_subject.question.content_hash == first.question.content_hash

    other_repo = QuestionRepository(db_session, other_org.id)
    different_org = await _create_question(
        other_repo,
        tenant,
        curriculum,
        org_id=other_org.id,
        **content,
    )
    assert different_org.question.id not in {
        first.question.id,
        different_subject.question.id,
    }
    assert different_org.question.content_hash == first.question.content_hash


async def test_org_and_global_banks_do_not_cross_dedup_boundaries(
    db_session, tenant, curriculum
):
    repo = QuestionRepository(db_session, tenant.org_id)

    org_first_content = {
        "stem": f"P2-T4 org before global {uuid.uuid4()}",
        "answer": {"text": "Same content, separate ownership scope"},
    }
    org_first = await _create_question(
        repo, tenant, curriculum, **org_first_content
    )
    global_after = await _create_question(
        repo,
        tenant,
        curriculum,
        scope="global",
        org_id=None,
        **org_first_content,
    )
    assert global_after.question.org_id is None
    assert global_after.question.scope == "global"
    assert global_after.question.content_hash == org_first.question.content_hash

    global_first_content = {
        "stem": f"P2-T4 global before org {uuid.uuid4()}",
        "answer": {"text": "Same content, separate ownership scope"},
    }
    global_first = await _create_question(
        repo,
        tenant,
        curriculum,
        scope="global",
        org_id=None,
        **global_first_content,
    )
    org_after = await _create_question(
        repo, tenant, curriculum, **global_first_content
    )
    assert org_after.question.org_id == tenant.org_id
    assert org_after.question.scope == "org"
    assert org_after.question.content_hash == global_first.question.content_hash

    with pytest.raises(ExactDuplicateQuestionError) as exc_info:
        await _create_question(
            repo,
            tenant,
            curriculum,
            scope="global",
            org_id=None,
            **global_first_content,
        )
    assert exc_info.value.existing_question.id == global_first.question.id


async def test_metadata_only_br8_fork_may_keep_the_parent_content_hash(
    db_session, tenant, curriculum
):
    repo = QuestionRepository(db_session, tenant.org_id)
    created = await _create_question(
        repo,
        tenant,
        curriculum,
        stem=f"P2-T4 identical-hash BR-8 fork {uuid.uuid4()}",
        answer={"text": "The answer is unchanged by metadata edits."},
        difficulty="medium",
    )
    parent = await repo.approve_question(
        created.question.id,
        created.question.row_version,
    )
    forked = await repo.fork(
        parent,
        caller_version=parent.row_version,
        difficulty="hard",
        created_by=tenant.user_id,
    )

    assert forked.id != parent.id
    assert forked.previous_version_id == parent.id
    assert forked.root_question_id == parent.root_question_id
    assert forked.version_number == 2
    assert forked.difficulty == "hard"
    assert forked.content_hash == parent.content_hash, (
        "difficulty is metadata, not part of the established P2-T3 content hash; "
        "the fork must bypass first-version exact rejection"
    )
    current_count = await db_session.scalar(
        sa.select(sa.func.count())
        .select_from(QuestionBank)
        .where(
            QuestionBank.root_question_id == parent.root_question_id,
            QuestionBank.is_current.is_(True),
        )
    )
    assert current_count == 1


_QUESTION_TYPE_CONTENT: tuple[tuple[str, Any, Any, Any], ...] = (
    (
        "mcq",
        [
            {"text": "A", "is_correct": True},
            {"text": "B", "is_correct": False},
        ],
        {"index": 0},
        None,
    ),
    ("true_false", None, {"value": True}, None),
    ("one_word", None, {"value": "answer", "synonyms": []}, None),
    ("fill_blank", None, {"blanks": ["answer"]}, None),
    (
        "short_answer",
        None,
        {"text": "answer"},
        {"key_points": ["answer"]},
    ),
    (
        "long_answer",
        None,
        {"text": "answer"},
        {"marking_scheme": [{"point": "answer", "marks": "2"}]},
    ),
    (
        "assertion_reason",
        {"reason": "reason"},
        {"relationship": "both_true_reason_correct"},
        None,
    ),
    (
        "case_study",
        {
            "sub_questions": [
                {"stem": "one", "marks": "1", "answer": {"text": "a"}},
                {"stem": "two", "marks": "1", "answer": {"text": "b"}},
            ]
        },
        None,
        None,
    ),
    ("numerical", None, {"value": 42, "units": "m"}, {"steps": ["work"]}),
    (
        "diagram_based",
        {"asset_id": None},
        {"text": "answer"},
        {"labels": ["A"]},
    ),
    (
        "match",
        {"column_a": ["A"], "column_b": ["B"]},
        {"pairs": [[0, 0]]},
        None,
    ),
    ("very_short_answer", None, {"text": "answer"}, None),
    (
        "source_based",
        {
            "sub_questions": [
                {"stem": "one", "marks": "1", "answer": {"text": "a"}},
                {"stem": "two", "marks": "1", "answer": {"text": "b"}},
            ]
        },
        None,
        None,
    ),
)


async def test_all_13_question_types_use_the_same_exact_dedup_path(
    db_session, tenant, curriculum
):
    repo = QuestionRepository(db_session, tenant.org_id)
    token = uuid.uuid4().hex
    created_ids: set[uuid.UUID] = set()

    for qtype, options, answer, rubric in _QUESTION_TYPE_CONTENT:
        stem = f"P2-T4 {token} exact path for {qtype}"
        if qtype == "fill_blank":
            stem += " ___"
        first = await _create_question(
            repo,
            tenant,
            curriculum,
            qtype=qtype,
            stem=stem,
            options=options,
            answer=answer,
            rubric=rubric,
        )
        created_ids.add(first.question.id)

        with pytest.raises(ExactDuplicateQuestionError) as exc_info:
            await _create_question(
                repo,
                tenant,
                curriculum,
                qtype=qtype,
                stem=stem,
                options=options,
                answer=answer,
                rubric=rubric,
            )
        assert exc_info.value.existing_question.id == first.question.id

    assert len(created_ids) == 13
    stored_count = await db_session.scalar(
        sa.select(sa.func.count())
        .select_from(QuestionBank)
        .where(QuestionBank.id.in_(created_ids))
    )
    assert stored_count == 13
