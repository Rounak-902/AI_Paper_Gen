"""Synchronous request-path orchestration for the CP-SAT feasibility gate."""

from __future__ import annotations

import uuid

from app.common.deps import RequestScope
from app.common.errors import AuthorizationError
from app.core.config import Settings
from app.modules.feasibility import schemas
from app.modules.feasibility.repository import FeasibilityQuestionRepository
from app.modules.feasibility.recommendation import recommend_blueprint
from packages.domain.assembler import (
    Blueprint,
    ChapterTarget,
    CpSatFeasibilitySolver,
    FeasibilityOutcome,
    MixTarget,
    LearningOutcomeTarget,
    QuestionCandidate,
    Section,
)


def candidate_from_question(row) -> QuestionCandidate:
    """Map one P2 row into the shared solver DTO, including LO identity."""

    return QuestionCandidate(
        id=row.id,
        chapter_id=row.chapter_id,
        marks=row.marks,
        difficulty=row.difficulty,
        bloom=row.bloom,
        qtype=row.qtype,
        source=row.source,
        learning_outcome_ids=tuple(row.learning_outcome_ids),
    )


def blueprint_from_request(payload: schemas.FeasibilityRequest) -> Blueprint:
    """Single HTTP/worker adapter into the pure P4-T3 solver contract."""

    return Blueprint(
        total_marks=payload.total_marks,
        sections=tuple(
            Section(
                id=section.id,
                question_count=section.question_count,
                marks_per_question=section.marks_per_question,
                allowed_question_types=tuple(section.allowed_question_types),
                chapter_ids=tuple(section.chapter_ids),
            )
            for section in payload.sections
        ),
        difficulty_mix={
            name: MixTarget(
                target_percent=target.target_percent,
                tolerance_percentage_points=target.tolerance_percentage_points,
            )
            for name, target in payload.difficulty_mix.items()
        },
        source_mix={
            name: MixTarget(
                target_percent=target.target_percent,
                tolerance_percentage_points=target.tolerance_percentage_points,
            )
            for name, target in payload.source_mix.items()
        },
        bloom_mix={
            name: MixTarget(
                target_percent=target.target_percent,
                tolerance_percentage_points=target.tolerance_percentage_points,
            )
            for name, target in payload.bloom_mix.items()
        },
        chapter_coverage=tuple(
            ChapterTarget(
                chapter_id=target.chapter_id,
                minimum_percent=target.minimum_percent,
                tolerance_percentage_points=target.tolerance_percentage_points,
            )
            for target in payload.chapter_coverage
        ),
        learning_outcome_coverage=tuple(
            LearningOutcomeTarget(
                learning_outcome_id=target.learning_outcome_id,
                minimum_percent=target.minimum_percent,
                tolerance_percentage_points=target.tolerance_percentage_points,
            )
            for target in payload.learning_outcome_coverage
        ),
        generation_mode=payload.generation_mode,
        textbook_floor_percent=payload.textbook_floor_percent,
    )


class FeasibilityService:
    """Load the RLS-visible bank snapshot and solve before any enqueue exists."""

    def __init__(
        self,
        *,
        repository: FeasibilityQuestionRepository,
        settings: Settings,
        solver: CpSatFeasibilitySolver | None = None,
    ) -> None:
        self._repository = repository
        self._settings = settings
        self._solver = solver or CpSatFeasibilitySolver()

    async def check(
        self,
        *,
        payload: schemas.FeasibilityRequest,
        scope: RequestScope,
        path_org_id: uuid.UUID,
    ) -> schemas.FeasibilityResponse:
        if path_org_id != scope.org_id:
            raise AuthorizationError("You do not have access to this resource.")

        rows = await self._repository.list_eligible(
            board_id=payload.board_id,
            class_id=payload.class_id,
            subject_id=payload.subject_id,
            curriculum_version_id=payload.curriculum_version_id,
            chapter_ids=tuple(payload.chapter_ids),
            language=payload.language,
            subject_scope_ids=scope.subject_ids,
        )
        mode_rows = tuple(
            row
            for row in rows
            if (
                payload.generation_mode == "mixed"
                or (payload.generation_mode == "textual" and row.source == "textbook")
                or (payload.generation_mode == "intext" and row.source == "intext")
                or (
                    payload.generation_mode == "ai_generated"
                    and row.source == "ai_generated"
                )
                or (
                    payload.generation_mode == "previous_year"
                    and row.source == "previous_year"
                )
                or (payload.generation_mode == "institute_bank" and row.scope == "org")
                or (payload.generation_mode == "global_bank" and row.scope == "global")
            )
        )
        candidates = tuple(
            candidate_from_question(row)
            for row in mode_rows
            if row.chapter_id is not None
        )
        result = self._solver.solve(
            candidates,
            blueprint_from_request(payload),
            time_budget_ms=self._settings.AI_SOLVER_TIME_BUDGET_MS,
        )
        recommendation = (
            recommend_blueprint(
                candidates=candidates,
                payload=payload,
                solver=self._solver,
                time_budget_ms=self._settings.AI_SOLVER_TIME_BUDGET_MS,
            )
            if result.outcome is not FeasibilityOutcome.FEASIBLE
            else None
        )
        return schemas.FeasibilityResponse(
            outcome=result.outcome.value,
            assignments=[
                schemas.AssignmentOut(
                    question_id=item.question_id,
                    section_id=item.section_id,
                )
                for item in result.assignments
            ],
            compromises=[
                schemas.CompromiseOut(
                    tier=item.tier,
                    constraint=item.constraint,
                    target=item.target,
                    actual=item.actual,
                    deviation=item.deviation,
                    reason=item.reason,
                )
                for item in result.compromises
            ],
            coverage=[
                schemas.CoverageFactOut(
                    dimension=item.dimension,
                    target=item.target,
                    target_value=item.target_value,
                    actual=item.actual,
                    satisfied=item.satisfied,
                )
                for item in result.coverage
            ],
            reasons=[
                schemas.InfeasibilityReasonOut(
                    code=item.code,
                    constraint=item.constraint,
                    required=item.required,
                    available=item.available,
                    detail=item.detail,
                )
                for item in result.reasons
            ],
            metrics=schemas.SolverMetricsOut(
                solver_status=result.solver_status,
                solve_time_ms=result.solve_time_ms,
                model_build_time_ms=result.model_build_time_ms,
                total_time_ms=result.total_time_ms,
                time_budget_ms=result.time_budget_ms,
                within_solve_budget=result.solve_time_ms <= result.time_budget_ms,
                optimal=result.optimal,
                timed_out=result.timed_out,
            ),
            bank_snapshot=schemas.BankSnapshotOut(
                eligible_question_count=result.candidate_count,
                selected_question_count=result.selected_question_count,
                selected_marks=result.selected_marks,
            ),
            recommended_blueprint=recommendation,
            recommendation_notes=(
                [
                    "Section marks and allowed types were fitted to the approved bank.",
                    "Difficulty, source, and Bloom targets were derived from a solver-valid selection.",
                    "Curriculum, language, total marks, and generation mode were preserved.",
                ]
                if recommendation is not None
                else []
            ),
        )


__all__ = [
    "FeasibilityService",
    "blueprint_from_request",
    "candidate_from_question",
]
