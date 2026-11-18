"""Pure, I/O-free CP-SAT feasibility gate for paper blueprints.

This is the production promotion of the P0-T1 spike.  The model keeps the spike's
marks-based mixes and CP-SAT engine, and adds the section assignment and mixed-mode
textbook-floor constraints that the spike explicitly left for P4.

The five soft tiers are lexicographic.  Chapter and learning-outcome coverage are
co-optimized in the same curriculum-coverage tier: they are two views of syllabus
spread, and giving either an implicit priority over the other would be arbitrary.
We first protect the constraint relaxed last
and finish with the one relaxed first::

    textbook floor -> chapter + learning-outcome coverage -> Bloom -> source -> difficulty

That solve order produces the caller-facing relaxation order requested by P4-T2::

    difficulty -> source -> Bloom -> chapter + learning-outcome coverage -> textbook floor

Hard constraints (section structure, marks, types, curriculum-scoped input, and no
duplicate question) are never relaxed.
"""

from __future__ import annotations

import time
import uuid
from dataclasses import dataclass
from decimal import Decimal, ROUND_CEILING, ROUND_FLOOR
from enum import Enum
from typing import Iterable, Mapping, Optional

from ortools.sat.python import cp_model


_MARK_SCALE = Decimal("10")
_SOLVE_ORDER = (
    "textbook_floor",
    "curriculum_coverage",
    "bloom",
    "source",
    "difficulty",
)
_REPORT_PRIORITY = {
    "difficulty": 0,
    "source": 1,
    "bloom": 2,
    "chapter_coverage": 3,
    "learning_outcome_coverage": 3,
    "textbook_floor": 4,
}
_TEXTBOOK_DERIVED_SOURCES = frozenset(("textbook", "intext"))


class FeasibilityOutcome(str, Enum):
    FEASIBLE = "feasible"
    FEASIBLE_WITH_COMPROMISE = "feasible-with-compromise"
    INFEASIBLE = "infeasible"


@dataclass(frozen=True)
class QuestionCandidate:
    id: uuid.UUID
    chapter_id: uuid.UUID
    marks: Decimal
    difficulty: str
    bloom: Optional[str]
    qtype: str
    source: str
    learning_outcome_ids: tuple[uuid.UUID, ...] = ()


@dataclass(frozen=True)
class Section:
    id: str
    question_count: int
    marks_per_question: Decimal
    allowed_question_types: tuple[str, ...]
    chapter_ids: tuple[uuid.UUID, ...] = ()


@dataclass(frozen=True)
class MixTarget:
    target_percent: Decimal
    tolerance_percentage_points: Decimal


@dataclass(frozen=True)
class ChapterTarget:
    chapter_id: uuid.UUID
    minimum_percent: Decimal
    tolerance_percentage_points: Decimal


@dataclass(frozen=True)
class LearningOutcomeTarget:
    learning_outcome_id: uuid.UUID
    minimum_percent: Decimal
    tolerance_percentage_points: Decimal


@dataclass(frozen=True)
class Blueprint:
    total_marks: Decimal
    sections: tuple[Section, ...]
    difficulty_mix: Mapping[str, MixTarget]
    source_mix: Mapping[str, MixTarget]
    bloom_mix: Mapping[str, MixTarget]
    chapter_coverage: tuple[ChapterTarget, ...]
    learning_outcome_coverage: tuple[LearningOutcomeTarget, ...] = ()
    generation_mode: str = "mixed"
    textbook_floor_percent: Decimal = Decimal("70")


@dataclass(frozen=True)
class Assignment:
    question_id: uuid.UUID
    section_id: str


@dataclass(frozen=True)
class Compromise:
    tier: str
    constraint: str
    target: str
    actual: str
    deviation: str
    reason: str


@dataclass(frozen=True)
class InfeasibilityReason:
    code: str
    constraint: str
    required: str
    available: str
    detail: str


@dataclass(frozen=True)
class CoverageFact:
    dimension: str
    target: str
    target_value: str
    actual: str
    satisfied: bool


@dataclass(frozen=True)
class SolveResult:
    outcome: FeasibilityOutcome
    assignments: tuple[Assignment, ...]
    compromises: tuple[Compromise, ...]
    coverage: tuple[CoverageFact, ...]
    reasons: tuple[InfeasibilityReason, ...]
    solve_time_ms: float
    model_build_time_ms: float
    total_time_ms: float
    time_budget_ms: int
    solver_status: str
    optimal: bool
    timed_out: bool
    candidate_count: int
    selected_question_count: int
    selected_marks: Decimal


@dataclass(frozen=True)
class _SoftConstraint:
    tier: str
    name: str
    category: str
    target_text: str
    lower: int
    upper: Optional[int]
    expression: cp_model.LinearExpr
    slack_variables: tuple[cp_model.IntVar, ...]


def _marks(value: Decimal) -> int:
    scaled = value * _MARK_SCALE
    integral = scaled.to_integral_value()
    if scaled != integral:
        raise ValueError(f"marks must have at most one decimal place: {value}")
    return int(integral)


def _lower_bound(total: int, percent: Decimal) -> int:
    value = Decimal(total) * max(Decimal(0), percent) / Decimal(100)
    return int(value.to_integral_value(rounding=ROUND_CEILING))


def _upper_bound(total: int, percent: Decimal) -> int:
    value = Decimal(total) * min(Decimal(100), percent) / Decimal(100)
    return int(value.to_integral_value(rounding=ROUND_FLOOR))


def _pct(value: int, total: int) -> Decimal:
    if total == 0:
        return Decimal(0)
    return (Decimal(value) * Decimal(100) / Decimal(total)).quantize(
        Decimal("0.1")
    )


class CpSatFeasibilitySolver:
    """Solve one already curriculum-scoped bank snapshot within one wall budget."""

    def solve(
        self,
        candidates: Iterable[QuestionCandidate],
        blueprint: Blueprint,
        *,
        time_budget_ms: int,
    ) -> SolveResult:
        if time_budget_ms < 1:
            raise ValueError("time_budget_ms must be positive")

        total_started = time.perf_counter()
        pool = tuple(candidates)
        total_marks = _marks(blueprint.total_marks)
        section_total = sum(
            section.question_count * _marks(section.marks_per_question)
            for section in blueprint.sections
        )
        if section_total != total_marks:
            reason = InfeasibilityReason(
                code="blueprint_marks_mismatch",
                constraint="total_marks",
                required=f"{blueprint.total_marks} marks",
                available=f"{Decimal(section_total) / _MARK_SCALE} marks across sections",
                detail="Section marks must sum exactly to blueprint total marks.",
            )
            return self._infeasible_without_solve(
                reason, pool, time_budget_ms, total_started
            )

        model_started = time.perf_counter()
        model = cp_model.CpModel()
        variables: dict[tuple[int, int], cp_model.IntVar] = {}
        variables_by_question: list[list[cp_model.IntVar]] = [
            [] for _ in pool
        ]
        section_candidate_indexes: list[list[int]] = []

        for section_index, section in enumerate(blueprint.sections):
            eligible: list[int] = []
            allowed_types = set(section.allowed_question_types)
            allowed_chapters = set(section.chapter_ids)
            for question_index, question in enumerate(pool):
                if question.qtype not in allowed_types:
                    continue
                if _marks(question.marks) != _marks(section.marks_per_question):
                    continue
                if allowed_chapters and question.chapter_id not in allowed_chapters:
                    continue
                eligible.append(question_index)
                variable = model.new_bool_var(
                    f"q{question_index}_s{section_index}"
                )
                variables[(question_index, section_index)] = variable
                variables_by_question[question_index].append(variable)
            section_candidate_indexes.append(eligible)

        for section_index, section in enumerate(blueprint.sections):
            section_vars = [
                variables[(question_index, section_index)]
                for question_index in section_candidate_indexes[section_index]
            ]
            model.add(sum(section_vars) == section.question_count)
            model.add(
                sum(
                    section_vars[position]
                    * _marks(pool[question_index].marks)
                    for position, question_index in enumerate(
                        section_candidate_indexes[section_index]
                    )
                )
                == section.question_count * _marks(section.marks_per_question)
            )

        selected: list[cp_model.LinearExpr] = []
        for question_vars in variables_by_question:
            expression = sum(question_vars) if question_vars else 0
            if question_vars:
                model.add(expression <= 1)
            selected.append(expression)

        model.add(
            sum(
                selected[index] * _marks(question.marks)
                for index, question in enumerate(pool)
            )
            == total_marks
        )

        soft = self._add_soft_constraints(
            model, pool, selected, blueprint, total_marks
        )
        model_build_ms = (time.perf_counter() - model_started) * 1000

        solve_started = time.perf_counter()
        last_solver: Optional[cp_model.CpSolver] = None
        last_status: Optional[cp_model.CpSolverStatus] = None
        incumbent_solver: Optional[cp_model.CpSolver] = None
        incumbent_status: Optional[cp_model.CpSolverStatus] = None
        completed_all_tiers = True

        for tier in _SOLVE_ORDER:
            tier_slacks = [
                variable
                for constraint in soft
                if constraint.tier == tier
                for variable in constraint.slack_variables
            ]
            if not tier_slacks:
                continue

            elapsed_ms = (time.perf_counter() - solve_started) * 1000
            remaining_ms = time_budget_ms - elapsed_ms
            if remaining_ms <= 0:
                completed_all_tiers = False
                break

            model.minimize(sum(tier_slacks))
            solver = cp_model.CpSolver()
            solver.parameters.max_time_in_seconds = remaining_ms / 1000.0
            solver.parameters.num_search_workers = 1
            solver.parameters.random_seed = 0
            status = solver.solve(model)
            last_solver, last_status = solver, status

            if status == cp_model.INFEASIBLE:
                return self._hard_infeasible(
                    pool=pool,
                    blueprint=blueprint,
                    section_candidate_indexes=section_candidate_indexes,
                    time_budget_ms=time_budget_ms,
                    model_build_ms=model_build_ms,
                    solve_started=solve_started,
                    total_started=total_started,
                    status=solver.status_name(status),
                )
            if status not in (cp_model.OPTIMAL, cp_model.FEASIBLE):
                completed_all_tiers = False
                break

            incumbent_solver, incumbent_status = solver, status

            if status != cp_model.OPTIMAL:
                completed_all_tiers = False
                break

            optimum = int(round(solver.objective_value))
            model.add(sum(tier_slacks) == optimum)

        if last_solver is None:
            # No soft constraints: perform the one hard-feasibility solve.
            solver = cp_model.CpSolver()
            solver.parameters.max_time_in_seconds = time_budget_ms / 1000.0
            solver.parameters.num_search_workers = 1
            solver.parameters.random_seed = 0
            last_status = solver.solve(model)
            last_solver = solver
            if last_status in (cp_model.OPTIMAL, cp_model.FEASIBLE):
                incumbent_solver, incumbent_status = solver, last_status
            if last_status == cp_model.INFEASIBLE:
                return self._hard_infeasible(
                    pool=pool,
                    blueprint=blueprint,
                    section_candidate_indexes=section_candidate_indexes,
                    time_budget_ms=time_budget_ms,
                    model_build_ms=model_build_ms,
                    solve_started=solve_started,
                    total_started=total_started,
                    status=solver.status_name(last_status),
                )

        solve_ms = (time.perf_counter() - solve_started) * 1000
        status_name = last_solver.status_name(last_status)
        if incumbent_solver is None or incumbent_status is None:
            reason = InfeasibilityReason(
                code="solver_timeout",
                constraint="solver_time_budget",
                required="a hard-constraint assignment within the wall-clock budget",
                available="no incumbent assignment",
                detail=(
                    "CP-SAT reached the configured budget without an assignment; "
                    "this is not a proof that the blueprint is mathematically infeasible."
                ),
            )
            return SolveResult(
                outcome=FeasibilityOutcome.INFEASIBLE,
                assignments=(),
                compromises=(),
                coverage=(),
                reasons=(reason,),
                solve_time_ms=solve_ms,
                model_build_time_ms=model_build_ms,
                total_time_ms=(time.perf_counter() - total_started) * 1000,
                time_budget_ms=time_budget_ms,
                solver_status=status_name,
                optimal=False,
                timed_out=True,
                candidate_count=len(pool),
                selected_question_count=0,
                selected_marks=Decimal(0),
            )

        # If the last optimization tier returned UNKNOWN, retain the last hard-valid
        # incumbent from the preceding tier and label it time-bounded/best-effort.
        solution_solver = incumbent_solver
        assignments = tuple(
            Assignment(question_id=pool[question_index].id, section_id=section.id)
            for section_index, section in enumerate(blueprint.sections)
            for question_index in section_candidate_indexes[section_index]
            if solution_solver.value(variables[(question_index, section_index)]) == 1
        )
        selected_ids = {assignment.question_id for assignment in assignments}
        selected_pool = tuple(question for question in pool if question.id in selected_ids)
        compromises = self._compromises(selected_pool, blueprint, total_marks)
        coverage = self._coverage(selected_pool, blueprint, total_marks)
        timed_out = (
            not completed_all_tiers
            or incumbent_status == cp_model.FEASIBLE
            or solve_ms >= time_budget_ms
        )
        outcome = (
            FeasibilityOutcome.FEASIBLE_WITH_COMPROMISE
            if compromises
            else FeasibilityOutcome.FEASIBLE
        )
        return SolveResult(
            outcome=outcome,
            assignments=assignments,
            compromises=compromises,
            coverage=coverage,
            reasons=(),
            solve_time_ms=solve_ms,
            model_build_time_ms=model_build_ms,
            total_time_ms=(time.perf_counter() - total_started) * 1000,
            time_budget_ms=time_budget_ms,
            solver_status=status_name,
            optimal=not timed_out,
            timed_out=timed_out,
            candidate_count=len(pool),
            selected_question_count=len(assignments),
            selected_marks=sum(
                (question.marks for question in selected_pool), Decimal(0)
            ),
        )

    @staticmethod
    def _capacity_reasons(
        pool: tuple[QuestionCandidate, ...],
        sections: tuple[Section, ...],
        eligible_indexes: list[list[int]],
    ) -> list[InfeasibilityReason]:
        reasons: list[InfeasibilityReason] = []
        for section, indexes in zip(sections, eligible_indexes):
            if len(indexes) >= section.question_count:
                continue
            chapter_text = (
                f" in chapters {', '.join(map(str, section.chapter_ids))}"
                if section.chapter_ids
                else ""
            )
            reasons.append(
                InfeasibilityReason(
                    code="section_candidate_shortfall",
                    constraint=f"section:{section.id}",
                    required=(
                        f"{section.question_count} distinct "
                        f"{section.marks_per_question}-mark question(s) of type "
                        f"{', '.join(section.allowed_question_types)}{chapter_text}"
                    ),
                    available=f"{len(indexes)} eligible question(s)",
                    detail=(
                        "The approved, current, non-deleted P2 bank does not contain "
                        "enough curriculum-scoped questions for this hard section."
                    ),
                )
            )
        required_count = sum(section.question_count for section in sections)
        usable = set(index for indexes in eligible_indexes for index in indexes)
        if len(usable) < required_count and not reasons:
            reasons.append(
                InfeasibilityReason(
                    code="distinct_question_shortfall",
                    constraint="no_duplicates",
                    required=f"{required_count} distinct questions",
                    available=f"{len(usable)} distinct section-eligible questions",
                    detail="A question may be assigned to at most one section.",
                )
            )
        return reasons

    def _hard_infeasible(
        self,
        *,
        pool: tuple[QuestionCandidate, ...],
        blueprint: Blueprint,
        section_candidate_indexes: list[list[int]],
        time_budget_ms: int,
        model_build_ms: float,
        solve_started: float,
        total_started: float,
        status: str,
    ) -> SolveResult:
        reasons = self._capacity_reasons(
            pool, blueprint.sections, section_candidate_indexes
        )
        if not reasons:
            section_counts = ", ".join(
                f"{section.id}={len(indexes)}/{section.question_count}"
                for section, indexes in zip(
                    blueprint.sections, section_candidate_indexes
                )
            )
            reasons = [
                InfeasibilityReason(
                    code="joint_section_assignment_conflict",
                    constraint="section_structure_and_no_duplicates",
                    required="one distinct eligible question for every section slot",
                    available=section_counts,
                    detail=(
                        "Each section has enough marginal candidates, but the same "
                        "questions are needed by incompatible slots; CP-SAT proved the "
                        "joint hard assignment infeasible."
                    ),
                )
            ]
        return SolveResult(
            outcome=FeasibilityOutcome.INFEASIBLE,
            assignments=(),
            compromises=(),
            coverage=(),
            reasons=tuple(reasons),
            solve_time_ms=(time.perf_counter() - solve_started) * 1000,
            model_build_time_ms=model_build_ms,
            total_time_ms=(time.perf_counter() - total_started) * 1000,
            time_budget_ms=time_budget_ms,
            solver_status=status,
            optimal=True,
            timed_out=False,
            candidate_count=len(pool),
            selected_question_count=0,
            selected_marks=Decimal(0),
        )

    @staticmethod
    def _infeasible_without_solve(
        reason: InfeasibilityReason,
        pool: tuple[QuestionCandidate, ...],
        time_budget_ms: int,
        started: float,
    ) -> SolveResult:
        return SolveResult(
            outcome=FeasibilityOutcome.INFEASIBLE,
            assignments=(),
            compromises=(),
            coverage=(),
            reasons=(reason,),
            solve_time_ms=0.0,
            model_build_time_ms=0.0,
            total_time_ms=(time.perf_counter() - started) * 1000,
            time_budget_ms=time_budget_ms,
            solver_status="BLUEPRINT_INFEASIBLE",
            optimal=True,
            timed_out=False,
            candidate_count=len(pool),
            selected_question_count=0,
            selected_marks=Decimal(0),
        )

    def _add_soft_constraints(
        self,
        model: cp_model.CpModel,
        pool: tuple[QuestionCandidate, ...],
        selected: list[cp_model.LinearExpr],
        blueprint: Blueprint,
        total_marks: int,
    ) -> tuple[_SoftConstraint, ...]:
        constraints: list[_SoftConstraint] = []

        def expression_for(predicate) -> cp_model.LinearExpr:
            return sum(
                selected[index] * _marks(question.marks)
                for index, question in enumerate(pool)
                if predicate(question)
            )

        def add_range(
            *,
            tier: str,
            name: str,
            category: str,
            expression: cp_model.LinearExpr,
            target: MixTarget,
        ) -> None:
            lower = _lower_bound(
                total_marks,
                target.target_percent - target.tolerance_percentage_points,
            )
            upper = _upper_bound(
                total_marks,
                target.target_percent + target.tolerance_percentage_points,
            )
            low_slack = model.new_int_var(0, total_marks, f"{name}_under")
            high_slack = model.new_int_var(0, total_marks, f"{name}_over")
            model.add(expression + low_slack >= lower)
            model.add(expression - high_slack <= upper)
            constraints.append(
                _SoftConstraint(
                    tier=tier,
                    name=name,
                    category=category,
                    target_text=(
                        f"{target.target_percent}% +/- "
                        f"{target.tolerance_percentage_points}pp"
                    ),
                    lower=lower,
                    upper=upper,
                    expression=expression,
                    slack_variables=(low_slack, high_slack),
                )
            )

        for category, target in blueprint.difficulty_mix.items():
            add_range(
                tier="difficulty",
                name=f"difficulty:{category}",
                category=category,
                expression=expression_for(lambda q, value=category: q.difficulty == value),
                target=target,
            )
        for category, target in blueprint.source_mix.items():
            add_range(
                tier="source",
                name=f"source:{category}",
                category=category,
                expression=expression_for(lambda q, value=category: q.source == value),
                target=target,
            )
        for category, target in blueprint.bloom_mix.items():
            add_range(
                tier="bloom",
                name=f"bloom:{category}",
                category=category,
                expression=expression_for(lambda q, value=category: q.bloom == value),
                target=target,
            )
        for target in blueprint.chapter_coverage:
            expression = expression_for(
                lambda q, chapter_id=target.chapter_id: q.chapter_id == chapter_id
            )
            lower = _lower_bound(
                total_marks,
                target.minimum_percent - target.tolerance_percentage_points,
            )
            slack = model.new_int_var(
                0, total_marks, f"chapter_{target.chapter_id}_under"
            )
            model.add(expression + slack >= lower)
            constraints.append(
                _SoftConstraint(
                    tier="curriculum_coverage",
                    name=f"chapter:{target.chapter_id}",
                    category=str(target.chapter_id),
                    target_text=(
                        f">={target.minimum_percent}% - "
                        f"{target.tolerance_percentage_points}pp"
                    ),
                    lower=lower,
                    upper=None,
                    expression=expression,
                    slack_variables=(slack,),
                )
            )
        for target in blueprint.learning_outcome_coverage:
            expression = expression_for(
                lambda q, outcome_id=target.learning_outcome_id: (
                    outcome_id in q.learning_outcome_ids
                )
            )
            lower = _lower_bound(
                total_marks,
                target.minimum_percent - target.tolerance_percentage_points,
            )
            slack = model.new_int_var(
                0, total_marks, f"outcome_{target.learning_outcome_id}_under"
            )
            model.add(expression + slack >= lower)
            constraints.append(
                _SoftConstraint(
                    tier="curriculum_coverage",
                    name=f"learning_outcome:{target.learning_outcome_id}",
                    category=str(target.learning_outcome_id),
                    target_text=(
                        f">={target.minimum_percent}% - "
                        f"{target.tolerance_percentage_points}pp"
                    ),
                    lower=lower,
                    upper=None,
                    expression=expression,
                    slack_variables=(slack,),
                )
            )
        if blueprint.generation_mode == "mixed":
            expression = expression_for(
                lambda q: q.source in _TEXTBOOK_DERIVED_SOURCES
            )
            lower = _lower_bound(total_marks, blueprint.textbook_floor_percent)
            slack = model.new_int_var(0, total_marks, "textbook_floor_under")
            model.add(expression + slack >= lower)
            constraints.append(
                _SoftConstraint(
                    tier="textbook_floor",
                    name="textbook_floor",
                    category="textbook+intext",
                    target_text=f">={blueprint.textbook_floor_percent}%",
                    lower=lower,
                    upper=None,
                    expression=expression,
                    slack_variables=(slack,),
                )
            )
        return tuple(constraints)

    def _compromises(
        self,
        selected: tuple[QuestionCandidate, ...],
        blueprint: Blueprint,
        total_marks: int,
    ) -> tuple[Compromise, ...]:
        actuals: dict[tuple[str, str], int] = {}

        def marks_where(predicate) -> int:
            return sum(_marks(question.marks) for question in selected if predicate(question))

        for category in blueprint.difficulty_mix:
            actuals[("difficulty", category)] = marks_where(
                lambda q, value=category: q.difficulty == value
            )
        for category in blueprint.source_mix:
            actuals[("source", category)] = marks_where(
                lambda q, value=category: q.source == value
            )
        for category in blueprint.bloom_mix:
            actuals[("bloom", category)] = marks_where(
                lambda q, value=category: q.bloom == value
            )

        compromises: list[Compromise] = []
        mixes = (
            ("difficulty", blueprint.difficulty_mix),
            ("source", blueprint.source_mix),
            ("bloom", blueprint.bloom_mix),
        )
        for tier, mix in mixes:
            for category, target in mix.items():
                actual = _pct(actuals[(tier, category)], total_marks)
                lower = target.target_percent - target.tolerance_percentage_points
                upper = target.target_percent + target.tolerance_percentage_points
                if lower <= actual <= upper:
                    continue
                outside_by = lower - actual if actual < lower else actual - upper
                compromises.append(
                    Compromise(
                        tier=tier,
                        constraint=f"{tier}:{category}",
                        target=(
                            f"{target.target_percent}% +/- "
                            f"{target.tolerance_percentage_points}pp"
                        ),
                        actual=f"{actual}%",
                        deviation=f"{outside_by}% beyond tolerance",
                        reason=(
                            f"The selected hard-valid assignment cannot meet the "
                            f"{tier} target for {category} without relaxing a more "
                            "protected tier."
                        ),
                    )
                )

        for target in blueprint.chapter_coverage:
            actual = _pct(
                marks_where(lambda q, chapter=target.chapter_id: q.chapter_id == chapter),
                total_marks,
            )
            lower = target.minimum_percent - target.tolerance_percentage_points
            if actual >= lower:
                continue
            compromises.append(
                Compromise(
                    tier="chapter_coverage",
                    constraint=f"chapter:{target.chapter_id}",
                    target=(
                        f">={target.minimum_percent}% - "
                        f"{target.tolerance_percentage_points}pp"
                    ),
                    actual=f"{actual}%",
                    deviation=f"{lower - actual}% below minimum",
                    reason="The eligible bank lacks enough marks from this chapter.",
                )
            )

        for target in blueprint.learning_outcome_coverage:
            actual = _pct(
                marks_where(
                    lambda q, outcome=target.learning_outcome_id: (
                        outcome in q.learning_outcome_ids
                    )
                ),
                total_marks,
            )
            lower = target.minimum_percent - target.tolerance_percentage_points
            if actual >= lower:
                continue
            compromises.append(
                Compromise(
                    tier="learning_outcome_coverage",
                    constraint=f"learning_outcome:{target.learning_outcome_id}",
                    target=(
                        f">={target.minimum_percent}% - "
                        f"{target.tolerance_percentage_points}pp"
                    ),
                    actual=f"{actual}%",
                    deviation=f"{lower - actual}% below minimum",
                    reason=(
                        "The eligible bank lacks enough marks aligned to this "
                        "learning outcome."
                    ),
                )
            )

        if blueprint.generation_mode == "mixed":
            actual = _pct(
                marks_where(lambda q: q.source in _TEXTBOOK_DERIVED_SOURCES),
                total_marks,
            )
            if actual < blueprint.textbook_floor_percent:
                compromises.append(
                    Compromise(
                        tier="textbook_floor",
                        constraint="textbook_floor",
                        target=f">={blueprint.textbook_floor_percent}%",
                        actual=f"{actual}%",
                        deviation=(
                            f"{blueprint.textbook_floor_percent - actual}% below floor"
                        ),
                        reason=(
                            "The mixed-mode pool cannot reach the textbook+intext "
                            "product floor while preserving all hard constraints."
                        ),
                    )
                )

        return tuple(
            sorted(
                compromises,
                key=lambda item: (
                    _REPORT_PRIORITY[item.tier],
                    item.constraint,
                ),
            )
        )

    def _coverage(
        self,
        selected: tuple[QuestionCandidate, ...],
        blueprint: Blueprint,
        total_marks: int,
    ) -> tuple[CoverageFact, ...]:
        """Materialize solver-owned facts consumed by paper-level checks.

        Callers must persist and render these facts instead of independently
        re-deriving soft-constraint results from a potentially different snapshot.
        """

        def actual(predicate) -> Decimal:
            value = sum(
                _marks(question.marks) for question in selected if predicate(question)
            )
            return _pct(value, total_marks)

        facts: list[CoverageFact] = []
        for dimension, targets, attribute in (
            ("difficulty", blueprint.difficulty_mix, "difficulty"),
            ("source", blueprint.source_mix, "source"),
            ("bloom", blueprint.bloom_mix, "bloom"),
        ):
            for category, target in targets.items():
                value = actual(
                    lambda q, name=attribute, expected=category: (
                        getattr(q, name) == expected
                    )
                )
                lower = target.target_percent - target.tolerance_percentage_points
                upper = target.target_percent + target.tolerance_percentage_points
                facts.append(
                    CoverageFact(
                        dimension=dimension,
                        target=category,
                        target_value=(
                            f"{target.target_percent}% +/- "
                            f"{target.tolerance_percentage_points}pp"
                        ),
                        actual=f"{value}%",
                        satisfied=lower <= value <= upper,
                    )
                )
        for target in blueprint.chapter_coverage:
            value = actual(lambda q, expected=target.chapter_id: q.chapter_id == expected)
            lower = target.minimum_percent - target.tolerance_percentage_points
            facts.append(
                CoverageFact(
                    dimension="chapter",
                    target=str(target.chapter_id),
                    target_value=(
                        f">={target.minimum_percent}% - "
                        f"{target.tolerance_percentage_points}pp"
                    ),
                    actual=f"{value}%",
                    satisfied=value >= lower,
                )
            )
        for target in blueprint.learning_outcome_coverage:
            value = actual(
                lambda q, expected=target.learning_outcome_id: (
                    expected in q.learning_outcome_ids
                )
            )
            lower = target.minimum_percent - target.tolerance_percentage_points
            facts.append(
                CoverageFact(
                    dimension="learning_outcome",
                    target=str(target.learning_outcome_id),
                    target_value=(
                        f">={target.minimum_percent}% - "
                        f"{target.tolerance_percentage_points}pp"
                    ),
                    actual=f"{value}%",
                    satisfied=value >= lower,
                )
            )
        if blueprint.generation_mode == "mixed":
            value = actual(lambda q: q.source in _TEXTBOOK_DERIVED_SOURCES)
            facts.append(
                CoverageFact(
                    dimension="textbook_floor",
                    target="textbook+intext",
                    target_value=f">={blueprint.textbook_floor_percent}%",
                    actual=f"{value}%",
                    satisfied=value >= blueprint.textbook_floor_percent,
                )
            )
        return tuple(facts)


__all__ = [
    "Assignment",
    "Blueprint",
    "ChapterTarget",
    "Compromise",
    "CoverageFact",
    "CpSatFeasibilitySolver",
    "FeasibilityOutcome",
    "InfeasibilityReason",
    "LearningOutcomeTarget",
    "MixTarget",
    "QuestionCandidate",
    "Section",
    "SolveResult",
]
