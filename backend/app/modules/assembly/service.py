"""P4-T4 per-slot loop and paper-level assembly checks.

The service deliberately calls the P4-T3 solver and the P3 retrieval/cascade
implementations.  It contains no second assignment heuristic, validation engine,
verification sandbox, or dedup algorithm.
"""

from __future__ import annotations

import uuid
from dataclasses import asdict, dataclass
from decimal import Decimal
from typing import Any

from app.common.deps import RequestScope
from app.core.config import Settings
from app.modules.ai.pipeline import (
    ContextBuilder,
    CostCascade,
    CostRates,
    CurriculumContext,
    InMemoryExactResultCache,
    InMemoryTokenBudgetGate,
    PipelinePrompts,
)
from app.modules.ai.pipeline.schemas import GeneratedContent
from app.modules.ai.providers.base import LlmProvider
from app.modules.feasibility.repository import FeasibilityQuestionRepository
from app.modules.feasibility.schemas import FeasibilityRequest
from app.modules.feasibility.service import (
    blueprint_from_request,
    candidate_from_question,
)
from app.modules.prompts.repository import PromptVersionRepository
from app.modules.prompts.service import PromptResolver
from app.modules.programs.repository import ProgramRepository
from app.modules.questions.pipeline_service import (
    bank_question_resolver,
    create_pipeline_question,
    similarity_checker,
)
from app.modules.questions import service as question_service
from app.modules.retrieval.repository import QuestionBankCandidateSource
from app.modules.retrieval.schemas import BlueprintSlot, ScoredCandidate, SlotRetrievalResult
from app.modules.retrieval.service import RetrievalService
from app.modules.assembly.repository import PaperAssemblyRepository
from app.modules.assembly.projection import project_version_pair
from packages.domain.assembler import CpSatFeasibilitySolver, FeasibilityOutcome


class PaperAssemblyError(RuntimeError):
    pass


@dataclass(frozen=True)
class AssemblyResult:
    paper_id: uuid.UUID
    paper_version_id: uuid.UUID
    outcome: str
    selected_question_count: int
    selected_marks: Decimal
    compromise_count: int
    reused_existing_version: bool = False


def _json_value(value: Any) -> Any:
    if isinstance(value, uuid.UUID):
        return str(value)
    if isinstance(value, Decimal):
        return str(value)
    if isinstance(value, dict):
        return {str(key): _json_value(item) for key, item in value.items()}
    if isinstance(value, (list, tuple)):
        return [_json_value(item) for item in value]
    return value


class PaperAssemblyService:
    """One atomic paper assembly run; P4-T5 owns resumable checkpoints later."""

    def __init__(
        self,
        *,
        session,
        settings: Settings,
        org_id: uuid.UUID,
        provider: LlmProvider,
        solver: CpSatFeasibilitySolver | None = None,
    ) -> None:
        self.session = session
        self.settings = settings
        self.org_id = org_id
        self.provider = provider
        self.solver = solver or CpSatFeasibilitySolver()
        self.repository = PaperAssemblyRepository(session, org_id)

    async def assemble(
        self, *, paper_id: uuid.UUID, label: str = "Generated Set A"
    ) -> AssemblyResult:
        paper = await self.repository.get_paper(paper_id)
        if paper is None:
            raise PaperAssemblyError("paper not found in tenant scope")
        existing = await self.repository.get_version(paper_id, label)
        if existing is not None:
            assembly = existing.content.get("assembly", {})
            return AssemblyResult(
                paper_id=paper.id,
                paper_version_id=existing.id,
                outcome=str(assembly.get("solverOutcome", "unknown")),
                selected_question_count=len(
                    [
                        question
                        for section in existing.content.get("sections", [])
                        for question in section.get("questions", [])
                    ]
                ),
                selected_marks=Decimal(str(paper.total_marks or 0)),
                compromise_count=len(assembly.get("compromises", [])),
                reused_existing_version=True,
            )

        payload = FeasibilityRequest.model_validate(paper.blueprint)
        membership = await self.repository.get_active_membership(paper.created_by)
        if membership is None:
            raise PaperAssemblyError("paper author has no active organization membership")
        subject_ids = None
        if membership.role == "teacher":
            subject_ids = await ProgramRepository(
                self.session, self.org_id
            ).get_teacher_subject_ids(paper.created_by)
        scope = RequestScope(
            org_id=self.org_id,
            user_id=paper.created_by,
            role=membership.role,
            branch_id=membership.branch_id,
            subject_ids=subject_ids,
        )

        rows = await FeasibilityQuestionRepository(
            self.session, self.org_id
        ).list_eligible(
            board_id=payload.board_id,
            class_id=payload.class_id,
            subject_id=payload.subject_id,
            curriculum_version_id=payload.curriculum_version_id,
            chapter_ids=tuple(payload.chapter_ids),
            language=payload.language,
            subject_scope_ids=scope.subject_ids,
        )
        mode_rows = tuple(row for row in rows if self._mode_allows(payload.generation_mode, row))
        by_id = {row.id: row for row in mode_rows if row.chapter_id is not None}
        solve_result = self.solver.solve(
            tuple(candidate_from_question(row) for row in by_id.values()),
            blueprint_from_request(payload),
            time_budget_ms=self.settings.AI_SOLVER_TIME_BUDGET_MS,
        )
        if solve_result.outcome is FeasibilityOutcome.INFEASIBLE:
            raise PaperAssemblyError(
                "solver rejected paper: "
                + "; ".join(reason.detail for reason in solve_result.reasons)
            )

        retrieval = RetrievalService.from_settings(
            source=QuestionBankCandidateSource(
                session=self.session,
                scope=scope,
                org_id=self.org_id,
            ),
            settings=self.settings,
        )
        bank_resolver = bank_question_resolver(
            session=self.session,
            request_scope=scope,
            org_id=self.org_id,
        )
        cascade = CostCascade(
            provider=self.provider,
            cache=InMemoryExactResultCache(),
            bank_resolver=bank_resolver,
            budget=InMemoryTokenBudgetGate(self.settings.AI_TENANT_TOKEN_BUDGET),
            rates=CostRates.from_settings(self.settings),
            similarity_checker=similarity_checker(
                session=self.session,
                request_scope=scope,
                org_id=self.org_id,
            ),
            escalation_thinking_tokens=self.settings.AI_ESCALATION_THINKING_BUDGET,
        )
        prompts: PipelinePrompts | None = None
        selected_ids: list[uuid.UUID] = []
        snapshots: list[dict[str, Any]] = []
        answers: list[dict[str, Any]] = []
        traces: list[dict[str, Any]] = []

        section_positions: dict[str, int] = {}
        for assignment in solve_result.assignments:
            row = by_id[assignment.question_id]
            position = section_positions.get(assignment.section_id, 0) + 1
            section_positions[assignment.section_id] = position
            slot_id = f"{assignment.section_id}:{position}"
            slot = BlueprintSlot(
                slot_id=slot_id,
                board_id=payload.board_id,
                class_id=payload.class_id,
                subject_id=payload.subject_id,
                curriculum_version_id=payload.curriculum_version_id,
                chapter_ids=[row.chapter_id],
                qtype=row.qtype,
                marks=row.marks,
                difficulty=row.difficulty,
                bloom=row.bloom,
                generation_mode=payload.generation_mode,
                learning_outcome_ids=list(row.learning_outcome_ids),
                excluded_question_ids=list(selected_ids),
            )
            retrieved = await retrieval.select_candidates(slot, top_k=100)
            selected_candidate = next(
                (
                    candidate
                    for candidate in retrieved.candidates
                    if candidate.question_id == assignment.question_id
                ),
                None,
            )
            if selected_candidate is None:
                # The solver row is the authoritative starting candidate. Retrieval
                # still ran and its gap is recorded, but ranking must not replace the
                # solver's joint assignment with a locally preferred alternative.
                selected_candidate = self._seed_candidate(row)
            selected_retrieval = SlotRetrievalResult(
                slot_id=slot_id,
                candidates=[selected_candidate],
                metadata_candidate_count=retrieved.metadata_candidate_count,
                duplicate_candidates_removed=retrieved.duplicate_candidates_removed,
                required_candidates=1,
                gap=False,
            )
            description, snippets = await self.repository.curriculum_context(
                chapter_ids=tuple(slot.chapter_ids),
                learning_outcome_ids=tuple(slot.learning_outcome_ids),
            )
            context = ContextBuilder().build(
                slot=slot,
                retrieval=selected_retrieval,
                curriculum=CurriculumContext(
                    board_id=slot.board_id,
                    class_id=slot.class_id,
                    subject_id=slot.subject_id,
                    curriculum_version_id=slot.curriculum_version_id,
                    chapter_ids=slot.chapter_ids,
                    learning_outcome_ids=slot.learning_outcome_ids,
                    description=description,
                    textbook_snippets=snippets,
                ),
                language=payload.language,
            )
            preflight_question = await bank_resolver(selected_candidate)
            gap_reason = None
            if preflight_question is None:
                gap_reason = "solver-selected question is no longer readable"
            else:
                try:
                    cascade.validator.request(preflight_question, context)
                except Exception as exc:
                    gap_reason = f"solver-selected question failed P3 validation: {exc}"
            if gap_reason is not None:
                selected_retrieval = selected_retrieval.model_copy(
                    update={"gap": True, "gap_reason": gap_reason}
                )
                context = context.__class__(
                    context.slot,
                    selected_retrieval,
                    context.curriculum,
                    context.language,
                    context.template,
                )
            if selected_retrieval.gap and prompts is None:
                prompts = await self._resolve_prompts()
            cascade_result = await cascade.run(
                tenant_id=self.org_id,
                context=context,
                prompts=prompts,
            )
            if cascade_result.question is None:
                raise PaperAssemblyError(
                    f"slot {slot_id} degraded: {cascade_result.degrade_reason}"
                )
            question = cascade_result.question
            source_id = question.meta.get("reused_question_id")
            if source_id is None:
                if question.source != row.source:
                    raise PaperAssemblyError(
                        f"slot {slot_id} generated source {question.source!r} would "
                        f"replace solver source {row.source!r}; residual re-solving "
                        "is deferred to P4-T5, so the solver coverage facts cannot "
                        "be safely reused for this substitution"
                    )
                created = await create_pipeline_question(
                    result=cascade_result.pipeline_result,
                    session=self.session,
                    request_scope=scope,
                    org_id=self.org_id,
                )
                question_id = created.id
            else:
                question_id = uuid.UUID(str(source_id))
            if cascade_result.pipeline_result is None:
                verification_flags = await cascade.validator.generated(
                    GeneratedContent(
                        **question.model_dump(),
                        cited_question_ids=[question_id],
                        cited_snippet_indices=[],
                    ),
                    context,
                )
            else:
                verification_flags = list(cascade_result.pipeline_result.flags)
            selected_ids.append(question_id)
            snapshot = question.model_dump(mode="json")
            snapshot.update(
                id=str(question_id),
                slotId=slot_id,
                sectionId=assignment.section_id,
                cascadeRung=cascade_result.cascade_rung,
            )
            version_question, answer_entry = project_version_pair(snapshot)
            snapshots.append(version_question)
            answers.append(answer_entry)
            traces.append(
                {
                    "slotId": slot_id,
                    "solverQuestionId": str(assignment.question_id),
                    "retrievalCandidateCount": len(retrieved.candidates),
                    "retrievalGap": retrieved.gap,
                    "gap": selected_retrieval.gap,
                    "gapReason": selected_retrieval.gap_reason,
                    "cascadeRung": cascade_result.cascade_rung,
                    "pipelineStages": (
                        cascade_result.pipeline_result.stages
                        if cascade_result.pipeline_result is not None
                        else ["context_builder", "bank_reuse", "validation_engine"]
                    ),
                    "loopStages": [
                        {"name": "retrieve", "state": "completed"},
                        {
                            "name": "gap",
                            "state": (
                                "gap_detected"
                                if selected_retrieval.gap
                                else "candidate_satisfied"
                            ),
                        },
                        {
                            "name": "generate",
                            "state": (
                                "completed"
                                if cascade_result.cascade_rung >= 2
                                else "skipped_bank_reuse"
                            ),
                        },
                        {"name": "validate", "state": "completed"},
                        {
                            "name": "verify",
                            "state": "completed",
                            "flags": verification_flags,
                        },
                        {"name": "dedup", "state": "completed"},
                    ],
                    "dedup": "p3-retrieval+p2-t4-final-write",
                }
            )

        duplicate_pairs = await question_service.retrieval_duplicate_pairs(
            session=self.session,
            scope=scope,
            org_id=self.org_id,
            question_ids=tuple(selected_ids),
            near_duplicate_threshold=self.settings.DEDUP_SIMILARITY_THRESHOLD,
        )
        checks = self._paper_checks(
            payload=payload,
            solve_result=solve_result,
            snapshots=snapshots,
            selected_ids=selected_ids,
            duplicate_pair_count=len(duplicate_pairs),
        )
        sections = []
        for section in payload.sections:
            section_questions = [
                item for item in snapshots if item["sectionId"] == section.id
            ]
            sections.append(
                {
                    "id": section.id,
                    "questions": section_questions,
                    "marks": str(
                        sum(
                            (Decimal(str(item["marks"])) for item in section_questions),
                            Decimal(0),
                        )
                    ),
                }
            )
        content = {
            "schemaVersion": 1,
            "sections": sections,
            "assembly": {
                "engine": "cp-sat",
                "solverOutcome": solve_result.outcome.value,
                "solverStatus": solve_result.solver_status,
                "selectedAssignments": [
                    _json_value(asdict(item)) for item in solve_result.assignments
                ],
                "compromises": [
                    _json_value(asdict(item)) for item in solve_result.compromises
                ],
                "coverage": [
                    _json_value(asdict(item)) for item in solve_result.coverage
                ],
                "paperChecks": checks,
                "slotTraces": traces,
            },
        }
        version = await self.repository.create_version(
            paper=paper,
            label=label,
            content=content,
            answer_key={"questions": answers},
            total_marks=solve_result.selected_marks,
        )
        return AssemblyResult(
            paper_id=paper.id,
            paper_version_id=version.id,
            outcome=solve_result.outcome.value,
            selected_question_count=len(snapshots),
            selected_marks=solve_result.selected_marks,
            compromise_count=len(solve_result.compromises),
        )

    async def _resolve_prompts(self) -> PipelinePrompts:
        resolver = PromptResolver(PromptVersionRepository(self.session))
        return PipelinePrompts(
            generation=await resolver.resolve(
                task="generate_question", model=self.settings.AI_MODEL_GENERATE
            ),
            classification=await resolver.resolve(
                task="classify_question", model=self.settings.AI_MODEL_CLASSIFY
            ),
        )

    @staticmethod
    def _seed_candidate(row) -> ScoredCandidate:
        return ScoredCandidate(
            question_id=row.id,
            rank=1,
            pre_coverage_rank=1,
            score=1.0,
            metadata_score=1.0,
            fts_score=0.0,
            trigram_score=0.0,
            vector_score=0.0,
            coverage_priority=0.0,
            dedup_cluster_size=1,
            chapter_id=row.chapter_id,
            matched_chapter_ids=[row.chapter_id],
            learning_outcome_ids=list(row.learning_outcome_ids),
            qtype=row.qtype,
            marks=row.marks,
            difficulty=row.difficulty,
            bloom=row.bloom,
            source=row.source,
            scope=row.scope,
            stem=row.stem,
        )

    @staticmethod
    def _mode_allows(mode: str, row) -> bool:
        return (
            mode == "mixed"
            or (mode == "textual" and row.source == "textbook")
            or (mode == "intext" and row.source == "intext")
            or (mode == "ai_generated" and row.source == "ai_generated")
            or (mode == "previous_year" and row.source == "previous_year")
            or (mode == "institute_bank" and row.scope == "org")
            or (mode == "global_bank" and row.scope == "global")
        )

    @staticmethod
    def _paper_checks(
        *, payload, solve_result, snapshots, selected_ids, duplicate_pair_count
    ):
        difficulty = [
            _json_value(asdict(item))
            for item in solve_result.coverage
            if item.dimension == "difficulty"
        ]
        textbook_floor = [
            _json_value(asdict(item))
            for item in solve_result.coverage
            if item.dimension == "textbook_floor"
        ]
        actual_marks = sum(
            (Decimal(str(item["marks"])) for item in snapshots), Decimal(0)
        )
        checks = {
            "difficultyBalance": {
                "source": "solver-coverage-facts",
                "facts": difficulty,
                "passedOrReported": all(item["satisfied"] for item in difficulty)
                or any(
                    item.tier == "difficulty" for item in solve_result.compromises
                ),
            },
            "mixedModeTextbookFloor": {
                "source": "solver-coverage-facts",
                "facts": textbook_floor,
                "passedOrReported": all(
                    item["satisfied"] for item in textbook_floor
                )
                or any(
                    item.tier == "textbook_floor"
                    for item in solve_result.compromises
                ),
            },
            "marksReconciliation": {
                "expected": str(payload.total_marks),
                "actual": str(actual_marks),
                "passed": actual_marks == payload.total_marks,
            },
            "noInPaperDuplicates": {
                "selected": len(selected_ids),
                "distinct": len(set(selected_ids)),
                "p2DedupPairs": duplicate_pair_count,
                "passed": (
                    len(selected_ids) == len(set(selected_ids))
                    and duplicate_pair_count == 0
                ),
            },
        }
        if not all(
            (
                checks["difficultyBalance"]["passedOrReported"],
                checks["mixedModeTextbookFloor"]["passedOrReported"],
                checks["marksReconciliation"]["passed"],
                checks["noInPaperDuplicates"]["passed"],
            )
        ):
            raise PaperAssemblyError(f"paper-level checks failed: {checks}")
        return checks


__all__ = ["AssemblyResult", "PaperAssemblyError", "PaperAssemblyService"]
