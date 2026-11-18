from __future__ import annotations

import uuid
from decimal import Decimal

from app.modules.feasibility.recommendation import recommend_blueprint
from app.modules.feasibility.schemas import (
    FeasibilityDraftRequest,
    FeasibilityRequest,
    SectionIn,
)
from packages.domain.assembler import CpSatFeasibilitySolver, QuestionCandidate


CHAPTER = uuid.UUID("10000000-0000-4000-8000-000000000001")
BOARD = uuid.UUID("10000000-0000-4000-8000-000000000002")
SCHOOL_CLASS = uuid.UUID("10000000-0000-4000-8000-000000000003")
SUBJECT = uuid.UUID("10000000-0000-4000-8000-000000000004")
VERSION = uuid.UUID("10000000-0000-4000-8000-000000000005")


def _rows(
    *, count: int, marks: int, qtype: str, source: str, start: int
) -> list[QuestionCandidate]:
    return [
        QuestionCandidate(
            id=uuid.UUID(f"20000000-0000-4000-8000-{start + index:012d}"),
            chapter_id=CHAPTER,
            marks=Decimal(marks),
            difficulty=("easy", "medium", "hard")[index % 3],
            bloom=("remember", "apply")[index % 2],
            qtype=qtype,
            source=source,
        )
        for index in range(count)
    ]


def _request(total: int = 80) -> FeasibilityRequest:
    return FeasibilityRequest(
        board_id=BOARD,
        class_id=SCHOOL_CLASS,
        subject_id=SUBJECT,
        curriculum_version_id=VERSION,
        chapter_ids=[CHAPTER],
        total_marks=Decimal(total),
        sections=[
            SectionIn(
                id="A",
                question_count=10,
                marks_per_question=Decimal(1),
                allowed_question_types=["mcq", "short_answer"],
            ),
            SectionIn(
                id="B",
                question_count=11,
                marks_per_question=Decimal(2),
                allowed_question_types=["short_answer"],
            ),
            SectionIn(
                id="C",
                question_count=8,
                marks_per_question=Decimal(3),
                allowed_question_types=["long_answer"],
            ),
            SectionIn(
                id="D",
                question_count=6,
                marks_per_question=Decimal(4),
                allowed_question_types=["numerical"],
            ),
        ],
        difficulty_mix={
            "easy": {"targetPercent": 30, "tolerancePercentagePoints": 5},
            "medium": {"targetPercent": 50, "tolerancePercentagePoints": 5},
            "hard": {"targetPercent": 20, "tolerancePercentagePoints": 5},
        },
        source_mix={"textbook": {"targetPercent": 100}},
        bloom_mix={
            "remember": {"targetPercent": 30},
            "understand": {"targetPercent": 40},
            "apply": {"targetPercent": 30},
        },
        generation_mode="global_bank",
        textbook_floor_percent=100,
    )


def test_recommendation_repairs_section_shape_and_derives_targets() -> None:
    candidates = tuple(
        _rows(count=5, marks=1, qtype="very_short_answer", source="intext", start=1)
        + _rows(count=43, marks=2, qtype="short_answer", source="textbook", start=100)
        + _rows(count=50, marks=3, qtype="numerical", source="textbook", start=1000)
        + _rows(count=19, marks=4, qtype="long_answer", source="intext", start=2000)
    )

    result = recommend_blueprint(
        candidates=candidates,
        payload=_request(),
        solver=CpSatFeasibilitySolver(),
        time_budget_ms=2_000,
    )

    assert result is not None
    assert [
        (section.question_count, section.marks_per_question)
        for section in result.sections
    ] == [
        (5, Decimal(1)),
        (12, Decimal(2)),
        (9, Decimal(3)),
        (6, Decimal(4)),
    ]
    assert result.sections[0].allowed_question_types == ["very_short_answer"]
    assert result.sections[2].allowed_question_types == ["numerical"]
    assert sum(item.target_percent for item in result.difficulty_mix.values()) == 100
    assert sum(item.target_percent for item in result.source_mix.values()) == 100
    assert sum(item.target_percent for item in result.bloom_mix.values()) == 100
    assert result.generation_mode == "global_bank"
    assert result.total_marks == 80
    assert result.chapter_ids == [CHAPTER]


def test_recommendation_is_honest_when_exact_total_cannot_be_built() -> None:
    candidates = tuple(
        _rows(count=10, marks=2, qtype="short_answer", source="textbook", start=1)
    )
    payload = _request(total=80).model_copy(
        update={
            "total_marks": Decimal(3),
            "sections": [
                SectionIn(
                    id="A",
                    question_count=3,
                    marks_per_question=Decimal(1),
                    allowed_question_types=["short_answer"],
                )
            ],
        }
    )

    assert (
        recommend_blueprint(
            candidates=candidates,
            payload=payload,
            solver=CpSatFeasibilitySolver(),
            time_budget_ms=2_000,
        )
        is None
    )


def test_builder_draft_allows_unbalanced_sections_for_auto_fit() -> None:
    payload = _request().model_dump(warnings=False)
    payload["sections"] = [
        SectionIn(
            id="A",
            question_count=5,
            marks_per_question=Decimal(1),
            allowed_question_types=["very_short_answer"],
        )
    ]

    draft = FeasibilityDraftRequest.model_validate(payload)

    assert draft.total_marks == Decimal(80)
    assert sum(
        Decimal(section.question_count) * section.marks_per_question
        for section in draft.sections
    ) == Decimal(5)

    candidates = tuple(
        _rows(count=5, marks=1, qtype="very_short_answer", source="intext", start=1)
        + _rows(count=43, marks=2, qtype="short_answer", source="textbook", start=100)
        + _rows(count=50, marks=3, qtype="numerical", source="textbook", start=1000)
        + _rows(count=19, marks=4, qtype="long_answer", source="intext", start=2000)
    )
    recommendation = recommend_blueprint(
        candidates=candidates,
        payload=draft,
        solver=CpSatFeasibilitySolver(),
        time_budget_ms=2_000,
    )

    assert recommendation is not None
    assert [
        (section.question_count, section.marks_per_question)
        for section in recommendation.sections
    ] == [
        (5, Decimal(1)),
        (12, Decimal(2)),
        (9, Decimal(3)),
        (6, Decimal(4)),
    ]
