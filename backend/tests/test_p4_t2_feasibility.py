"""Focused proofs for the synchronous P4-T2 CP-SAT feasibility gate."""

from __future__ import annotations

import ast
import json
import uuid
from decimal import Decimal
from pathlib import Path

from hypothesis import given, settings
from hypothesis import strategies as st

from packages.domain.assembler import (
    Blueprint,
    ChapterTarget,
    CpSatFeasibilitySolver,
    FeasibilityOutcome,
    LearningOutcomeTarget,
    MixTarget,
    QuestionCandidate,
    Section,
)


CHAPTER = uuid.UUID("10000000-0000-4000-8000-000000000001")
CHAPTER_B = uuid.UUID("10000000-0000-4000-8000-000000000002")
OUTCOME_A = uuid.UUID("30000000-0000-4000-8000-000000000001")
OUTCOME_B = uuid.UUID("30000000-0000-4000-8000-000000000002")


def _question(
    number: int,
    *,
    difficulty: str,
    source: str,
    bloom: str = "remember",
    chapter_id: uuid.UUID = CHAPTER,
    learning_outcome_ids: tuple[uuid.UUID, ...] = (),
) -> QuestionCandidate:
    return QuestionCandidate(
        id=uuid.UUID(f"20000000-0000-4000-8000-{number:012d}"),
        chapter_id=chapter_id,
        marks=Decimal("2.0"),
        difficulty=difficulty,
        bloom=bloom,
        qtype="short_answer",
        source=source,
        learning_outcome_ids=learning_outcome_ids,
    )


def _section(count: int) -> Section:
    return Section(
        id="A",
        question_count=count,
        marks_per_question=Decimal("2.0"),
        allowed_question_types=("short_answer",),
        chapter_ids=(CHAPTER,),
    )


def test_exact_assignment_is_feasible() -> None:
    pool = (
        _question(1, difficulty="easy", source="textbook", bloom="remember"),
        _question(2, difficulty="easy", source="intext", bloom="remember"),
        _question(3, difficulty="hard", source="textbook", bloom="apply"),
        _question(4, difficulty="hard", source="intext", bloom="apply"),
    )
    result = CpSatFeasibilitySolver().solve(
        pool,
        Blueprint(
            total_marks=Decimal("8.0"),
            sections=(_section(4),),
            difficulty_mix={
                "easy": MixTarget(Decimal(50), Decimal(0)),
                "hard": MixTarget(Decimal(50), Decimal(0)),
            },
            source_mix={
                "textbook": MixTarget(Decimal(50), Decimal(0)),
                "intext": MixTarget(Decimal(50), Decimal(0)),
            },
            bloom_mix={
                "remember": MixTarget(Decimal(50), Decimal(0)),
                "apply": MixTarget(Decimal(50), Decimal(0)),
            },
            chapter_coverage=(),
        ),
        time_budget_ms=2000,
    )

    assert result.outcome is FeasibilityOutcome.FEASIBLE
    assert len(result.assignments) == 4
    assert result.compromises == ()
    assert result.solve_time_ms < 2000


def test_relaxation_is_named_and_obeys_five_tier_order() -> None:
    # Selecting q1+q2 preserves source=100% textbook and the textbook floor but
    # compromises difficulty. Selecting q1+q3 would preserve difficulty but relax
    # source. The documented ladder says difficulty must be sacrificed first.
    pool = (
        _question(1, difficulty="easy", source="textbook"),
        _question(2, difficulty="easy", source="textbook"),
        _question(3, difficulty="hard", source="ai_generated"),
    )
    result = CpSatFeasibilitySolver().solve(
        pool,
        Blueprint(
            total_marks=Decimal("4.0"),
            sections=(_section(2),),
            difficulty_mix={
                "easy": MixTarget(Decimal(50), Decimal(0)),
                "hard": MixTarget(Decimal(50), Decimal(0)),
            },
            source_mix={"textbook": MixTarget(Decimal(100), Decimal(0))},
            bloom_mix={},
            chapter_coverage=(),
        ),
        time_budget_ms=2000,
    )

    assert result.outcome is FeasibilityOutcome.FEASIBLE_WITH_COMPROMISE
    assert {item.tier for item in result.compromises} == {"difficulty"}
    assert {item.constraint for item in result.compromises} == {
        "difficulty:easy",
        "difficulty:hard",
    }


def test_non_adjacent_source_and_chapter_tiers_relax_together_in_order() -> None:
    # Two slots must be filled. q1+q2 preserves source=100% textbook but gives
    # chapter B 0%. q1+q3 gives chapter B its best possible 50%, at the cost of
    # reducing textbook source to 50%. Chapter coverage is tier 4 and source is
    # tier 2, so the solver must choose q3 and relax both constraints rather than
    # sacrificing the more-protected chapter tier to preserve source.
    pool = (
        _question(1, difficulty="easy", source="textbook"),
        _question(2, difficulty="easy", source="textbook"),
        _question(
            3,
            difficulty="easy",
            source="ai_generated",
            chapter_id=CHAPTER_B,
        ),
    )
    section = Section(
        id="A",
        question_count=2,
        marks_per_question=Decimal("2.0"),
        allowed_question_types=("short_answer",),
        chapter_ids=(CHAPTER, CHAPTER_B),
    )
    result = CpSatFeasibilitySolver().solve(
        pool,
        Blueprint(
            total_marks=Decimal("4.0"),
            sections=(section,),
            difficulty_mix={},
            source_mix={"textbook": MixTarget(Decimal(100), Decimal(0))},
            bloom_mix={},
            chapter_coverage=(
                ChapterTarget(
                    chapter_id=CHAPTER_B,
                    minimum_percent=Decimal(100),
                    tolerance_percentage_points=Decimal(0),
                ),
            ),
            generation_mode="mixed",
            textbook_floor_percent=Decimal(50),
        ),
        time_budget_ms=2000,
    )

    selected_ids = {assignment.question_id for assignment in result.assignments}
    compromises = [
        {
            "tier": item.tier,
            "constraint": item.constraint,
            "target": item.target,
            "actual": item.actual,
            "deviation": item.deviation,
        }
        for item in result.compromises
    ]
    print(
        "MULTI_TIER_RELAXATION_OUTPUT="
        + json.dumps(
            {
                "outcome": result.outcome.value,
                "solver_status": result.solver_status,
                "selected_question_ids": sorted(map(str, selected_ids)),
                "compromises": compromises,
                "solve_time_ms": round(result.solve_time_ms, 3),
            },
            sort_keys=True,
        )
    )

    assert result.outcome is FeasibilityOutcome.FEASIBLE_WITH_COMPROMISE
    assert uuid.UUID("20000000-0000-4000-8000-000000000003") in selected_ids
    assert [item.tier for item in result.compromises] == [
        "source",
        "chapter_coverage",
    ]
    assert compromises == [
        {
            "tier": "source",
            "constraint": "source:textbook",
            "target": "100% +/- 0pp",
            "actual": "50.0%",
            "deviation": "50.0% beyond tolerance",
        },
        {
            "tier": "chapter_coverage",
            "constraint": f"chapter:{CHAPTER_B}",
            "target": ">=100% - 0pp",
            "actual": "50.0%",
            "deviation": "50.0% below minimum",
        },
    ]


def test_learning_outcome_coverage_changes_selection_at_chapter_priority() -> None:
    pool = (
        _question(
            1,
            difficulty="easy",
            source="textbook",
            learning_outcome_ids=(OUTCOME_A,),
        ),
        _question(
            2,
            difficulty="easy",
            source="textbook",
            learning_outcome_ids=(OUTCOME_A,),
        ),
        _question(
            3,
            difficulty="easy",
            source="ai_generated",
            learning_outcome_ids=(OUTCOME_B,),
        ),
    )
    baseline = CpSatFeasibilitySolver().solve(
        pool,
        Blueprint(
            total_marks=Decimal("4.0"),
            sections=(_section(2),),
            difficulty_mix={},
            source_mix={"textbook": MixTarget(Decimal(100), Decimal(0))},
            bloom_mix={},
            chapter_coverage=(),
            generation_mode="mixed",
            textbook_floor_percent=Decimal(50),
        ),
        time_budget_ms=2000,
    )
    with_outcome = CpSatFeasibilitySolver().solve(
        pool,
        Blueprint(
            total_marks=Decimal("4.0"),
            sections=(_section(2),),
            difficulty_mix={},
            source_mix={"textbook": MixTarget(Decimal(100), Decimal(0))},
            bloom_mix={},
            chapter_coverage=(),
            learning_outcome_coverage=(
                LearningOutcomeTarget(OUTCOME_B, Decimal(50), Decimal(0)),
            ),
            generation_mode="mixed",
            textbook_floor_percent=Decimal(50),
        ),
        time_budget_ms=2000,
    )

    baseline_ids = {item.question_id for item in baseline.assignments}
    selected_ids = {item.question_id for item in with_outcome.assignments}
    outcome_question_id = uuid.UUID("20000000-0000-4000-8000-000000000003")
    assert outcome_question_id not in baseline_ids
    assert outcome_question_id in selected_ids
    assert any(
        fact.dimension == "learning_outcome"
        and fact.target == str(OUTCOME_B)
        and fact.actual == "50.0%"
        and fact.satisfied
        for fact in with_outcome.coverage
    )
    assert {item.tier for item in with_outcome.compromises} == {"source"}


def test_hard_section_shortfall_is_structured_infeasible() -> None:
    pool = (_question(1, difficulty="easy", source="textbook"),)
    result = CpSatFeasibilitySolver().solve(
        pool,
        Blueprint(
            total_marks=Decimal("4.0"),
            sections=(_section(2),),
            difficulty_mix={},
            source_mix={},
            bloom_mix={},
            chapter_coverage=(),
            generation_mode="textual",
        ),
        time_budget_ms=2000,
    )

    assert result.outcome is FeasibilityOutcome.INFEASIBLE
    assert result.reasons[0].code == "section_candidate_shortfall"
    assert result.reasons[0].constraint == "section:A"
    assert result.reasons[0].required.startswith("2 distinct")
    assert result.reasons[0].available == "1 eligible question(s)"
    assert result.solver_status == "INFEASIBLE"
    assert result.solve_time_ms > 0


def test_request_adapter_has_no_worker_or_celery_dependency() -> None:
    root = Path(__file__).resolve().parents[1]
    files = (
        root / "app/modules/feasibility/router.py",
        root / "app/modules/feasibility/service.py",
        root / "app/modules/feasibility/repository.py",
    )
    imported_modules: set[str] = set()
    for file in files:
        tree = ast.parse(file.read_text(encoding="utf-8"))
        for node in ast.walk(tree):
            if isinstance(node, ast.Import):
                imported_modules.update(alias.name for alias in node.names)
            elif isinstance(node, ast.ImportFrom) and node.module:
                imported_modules.add(node.module)

    assert not any(name == "celery" or name.startswith("celery.") for name in imported_modules)
    assert not any(
        name == "app.workers" or name.startswith("app.workers.")
        for name in imported_modules
    )


@settings(max_examples=40, deadline=None)
@given(
    pool_size=st.integers(min_value=0, max_value=25),
    requested=st.integers(min_value=1, max_value=25),
    difficulties=st.lists(
        st.sampled_from(("easy", "medium", "hard")),
        min_size=25,
        max_size=25,
    ),
)
def test_property_solver_returns_hard_valid_assignment_or_honest_reason(
    pool_size: int,
    requested: int,
    difficulties: list[str],
) -> None:
    pool = tuple(
        _question(
            index + 1,
            difficulty=difficulties[index],
            source="textbook",
        )
        for index in range(pool_size)
    )
    result = CpSatFeasibilitySolver().solve(
        pool,
        Blueprint(
            total_marks=Decimal(requested * 2),
            sections=(_section(requested),),
            difficulty_mix={
                "easy": MixTarget(Decimal(34), Decimal(66)),
                "medium": MixTarget(Decimal(33), Decimal(67)),
                "hard": MixTarget(Decimal(33), Decimal(67)),
            },
            source_mix={"textbook": MixTarget(Decimal(100), Decimal(0))},
            bloom_mix={},
            chapter_coverage=(),
        ),
        time_budget_ms=500,
    )

    if pool_size < requested:
        assert result.outcome is FeasibilityOutcome.INFEASIBLE
        assert result.reasons
        assert result.assignments == ()
        return

    assert result.outcome in {
        FeasibilityOutcome.FEASIBLE,
        FeasibilityOutcome.FEASIBLE_WITH_COMPROMISE,
    }
    assert result.reasons == ()
    assert len(result.assignments) == requested
    assert len({assignment.question_id for assignment in result.assignments}) == requested
    assert result.selected_marks == Decimal(requested * 2)
