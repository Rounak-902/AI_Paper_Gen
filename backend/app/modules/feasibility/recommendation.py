"""Bank-aware blueprint recommendations for the paper builder.

There is no honest universal Bloom/source/difficulty preset.  A useful preset
has to start from the approved questions that are actually available for the
selected curriculum scope.  This module therefore:

1. finds the closest exact-marks section shape supported by the bank;
2. widens each section only to question types that exist at that mark value;
3. asks the production solver for a hard-valid selection; and
4. derives mark-weighted soft targets from that concrete selection.

The result is advisory.  It never changes the requested curriculum, language,
total marks, or generation mode, and it never enqueues generation.
"""

from __future__ import annotations

from collections import defaultdict
from decimal import Decimal, ROUND_FLOOR
from typing import Iterable, Sequence

from app.modules.feasibility import schemas
from app.modules.questions.schemas import (
    QUESTION_BLOOM_LEVELS,
    QUESTION_DIFFICULTIES,
    QUESTION_SOURCES,
)
from packages.domain.assembler import (
    Blueprint,
    CpSatFeasibilitySolver,
    FeasibilityOutcome,
    QuestionCandidate,
    Section,
)


_MARK_SCALE = Decimal("10")


def _scaled_marks(value: Decimal) -> int:
    return int((value * _MARK_SCALE).to_integral_value())


def _section_id(index: int) -> str:
    # 50 sections are allowed by the API.  A-Z covers realistic school papers;
    # later ids remain deterministic instead of silently colliding.
    return chr(ord("A") + index) if index < 26 else f"S{index + 1}"


def _closest_supported_shape(
    candidates: Sequence[QuestionCandidate],
    payload: schemas.FeasibilityRequest,
) -> list[tuple[Decimal, int, tuple[str, ...]]] | None:
    """Return exact-total section groups closest to the teacher's current shape.

    Dynamic programming is preferable to a greedy fill here: a locally obvious
    denomination can make the remaining total impossible (for example 3-mark and
    4-mark questions in a 10-mark remainder).  The objective minimizes the
    absolute change in marks contributed by every denomination.
    """

    by_marks: dict[Decimal, list[QuestionCandidate]] = defaultdict(list)
    for candidate in candidates:
        if candidate.marks > 0:
            by_marks[candidate.marks].append(candidate)
    if not by_marks:
        return None

    total = _scaled_marks(payload.total_marks)
    desired: dict[int, int] = defaultdict(int)
    for section in payload.sections:
        mark = _scaled_marks(section.marks_per_question)
        desired[mark] += section.question_count * mark

    groups: list[tuple[Decimal, int, tuple[str, ...], int]] = []
    for marks, rows in sorted(by_marks.items()):
        scaled = _scaled_marks(marks)
        if scaled <= 0 or scaled > total:
            continue
        types = tuple(sorted({row.qtype for row in rows}))
        capacity = min(len(rows), total // scaled)
        if capacity:
            groups.append((marks, capacity, types, scaled))
    if not groups:
        return None

    if sum(desired.values()) != total:
        # An incomplete/default draft is not a meaningful pedagogical target.
        # Give one-mark recall a smaller share and spread the rest evenly over
        # the higher-order denominations.  For the common 1/2/3/4-mark shape
        # this is 10/30/30/30 and naturally adapts when a denomination is absent.
        weights = [1, *([3] * (len(groups) - 1))]
        weight_total = sum(weights)
        balanced = [total * weight // weight_total for weight in weights]
        for index in range(total - sum(balanced)):
            balanced[-1 - (index % len(balanced))] += 1
        desired = {
            group[3]: contribution
            for group, contribution in zip(groups, balanced)
        }

    # total -> (cost, counts).  Unexpected mark denominations receive a tiny
    # additional penalty so ties preserve the teacher's original shape.
    states: dict[int, tuple[int, tuple[int, ...]]] = {0: (0, ())}
    for _marks, capacity, _types, scaled in groups:
        next_states: dict[int, tuple[int, tuple[int, ...]]] = {}
        wanted = desired.get(scaled, 0)
        for subtotal, (cost, counts) in states.items():
            for count in range(0, min(capacity, (total - subtotal) // scaled) + 1):
                next_total = subtotal + count * scaled
                deviation = abs(count * scaled - wanted)
                new_cost = cost + deviation + (1 if wanted == 0 and count else 0)
                current = next_states.get(next_total)
                candidate_state = (new_cost, (*counts, count))
                if current is None or candidate_state < current:
                    next_states[next_total] = candidate_state
        states = next_states

    solution = states.get(total)
    if solution is None:
        return None
    counts = solution[1]
    return [
        (marks, count, types)
        for (marks, _capacity, types, _scaled), count in zip(groups, counts)
        if count > 0
    ]


def _percent_mix(
    selected: Sequence[QuestionCandidate],
    attribute: str,
    vocabulary: Iterable[str],
) -> dict[str, schemas.MixTargetIn]:
    """Convert selected marks into integer percentages that sum exactly to 100."""

    allowed = tuple(vocabulary)
    marks_by_value: dict[str, Decimal] = defaultdict(Decimal)
    for question in selected:
        value = getattr(question, attribute)
        if value in allowed:
            marks_by_value[value] += question.marks
    known_total = sum(marks_by_value.values(), Decimal(0))
    if known_total <= 0:
        # Bloom is nullable in the database.  A 100pp tolerance is explicit:
        # the target is a UI placeholder, not a fabricated classification.
        fallback = allowed[0]
        return {
            fallback: schemas.MixTargetIn(
                target_percent=Decimal(100),
                tolerance_percentage_points=Decimal(100),
            )
        }

    raw = {
        value: marks * Decimal(100) / known_total
        for value, marks in marks_by_value.items()
    }
    whole = {
        value: int(percent.to_integral_value(rounding=ROUND_FLOOR))
        for value, percent in raw.items()
    }
    remainder = 100 - sum(whole.values())
    for value in sorted(raw, key=lambda item: (raw[item] - whole[item], item), reverse=True)[:remainder]:
        whole[value] += 1
    return {
        value: schemas.MixTargetIn(
            target_percent=Decimal(percent),
            tolerance_percentage_points=Decimal(5),
        )
        for value, percent in whole.items()
        if percent > 0
    }


def recommend_blueprint(
    *,
    candidates: Sequence[QuestionCandidate],
    payload: schemas.FeasibilityRequest,
    solver: CpSatFeasibilitySolver,
    time_budget_ms: int,
) -> schemas.FeasibilityRequest | None:
    """Return a solver-proven bank-aware recommendation, or ``None`` honestly."""

    shape = _closest_supported_shape(candidates, payload)
    if not shape:
        return None
    sections = tuple(
        Section(
            id=_section_id(index),
            question_count=count,
            marks_per_question=marks,
            allowed_question_types=types,
            chapter_ids=(),
        )
        for index, (marks, count, types) in enumerate(shape)
    )
    hard_blueprint = Blueprint(
        total_marks=payload.total_marks,
        sections=sections,
        difficulty_mix={},
        source_mix={},
        bloom_mix={},
        chapter_coverage=(),
        learning_outcome_coverage=(),
        generation_mode=payload.generation_mode,
        textbook_floor_percent=Decimal(0),
    )
    hard_result = solver.solve(
        candidates,
        hard_blueprint,
        time_budget_ms=time_budget_ms,
    )
    if hard_result.outcome is FeasibilityOutcome.INFEASIBLE:
        return None

    selected_ids = {assignment.question_id for assignment in hard_result.assignments}
    selected = tuple(row for row in candidates if row.id in selected_ids)
    if not selected:
        return None

    textbook_derived = sum(
        (row.marks for row in selected if row.source in {"textbook", "intext"}),
        Decimal(0),
    )
    textbook_percent = int(
        (textbook_derived * Decimal(100) / payload.total_marks).to_integral_value(
            rounding=ROUND_FLOOR
        )
    )
    recommendation = payload.model_copy(
        update={
            "sections": [
                schemas.SectionIn(
                    id=section.id,
                    question_count=section.question_count,
                    marks_per_question=section.marks_per_question,
                    allowed_question_types=list(section.allowed_question_types),
                    chapter_ids=[],
                )
                for section in sections
            ],
            "difficulty_mix": _percent_mix(
                selected, "difficulty", QUESTION_DIFFICULTIES
            ),
            "source_mix": _percent_mix(selected, "source", QUESTION_SOURCES),
            "bloom_mix": _percent_mix(selected, "bloom", QUESTION_BLOOM_LEVELS),
            "chapter_coverage": [],
            "learning_outcome_coverage": [],
            # In mixed mode this remains a real product floor; in other modes it
            # is informational because the solver does not apply that constraint.
            "textbook_floor_percent": Decimal(min(70, textbook_percent)),
        }
    )

    # Final proof: never tell the UI a recommendation is usable unless the same
    # production solver accepts it with the derived soft targets.
    proof = solver.solve(
        candidates,
        Blueprint(
            total_marks=recommendation.total_marks,
            sections=sections,
            difficulty_mix={
                key: _mix(value) for key, value in recommendation.difficulty_mix.items()
            },
            source_mix={
                key: _mix(value) for key, value in recommendation.source_mix.items()
            },
            bloom_mix={
                key: _mix(value) for key, value in recommendation.bloom_mix.items()
            },
            chapter_coverage=(),
            learning_outcome_coverage=(),
            generation_mode=recommendation.generation_mode,
            textbook_floor_percent=recommendation.textbook_floor_percent,
        ),
        time_budget_ms=time_budget_ms,
    )
    return recommendation if proof.outcome is not FeasibilityOutcome.INFEASIBLE else None


def _mix(value: schemas.MixTargetIn):
    # Local import keeps the public surface of this module limited to the
    # recommendation function while reusing the domain DTO exactly.
    from packages.domain.assembler import MixTarget

    return MixTarget(
        target_percent=value.target_percent,
        tolerance_percentage_points=value.tolerance_percentage_points,
    )


__all__ = ["recommend_blueprint"]
