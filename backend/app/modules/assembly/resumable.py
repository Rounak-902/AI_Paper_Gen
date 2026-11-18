"""P4-T5 durable, per-slot assembly orchestration.

The P4-T4 stage sequence remains retrieve -> gap -> generate -> validate ->
verify -> dedup.  This module changes transaction and invocation boundaries only:
planning, every slot, and finalization commit independently.
"""

from __future__ import annotations

import copy
import uuid
from dataclasses import asdict, replace
from datetime import datetime, timezone
from decimal import Decimal
from typing import Any

from sqlalchemy import select

from app.common.deps import RequestScope
from app.core.config import Settings
from app.models.assembly import PaperAssemblyRun, PaperAssemblySlot
from app.models.question import QuestionBank
from app.modules.ai.pipeline import (
    ContextBuilder,
    CostCascade,
    CostRates,
    CurriculumContext,
    InMemoryExactResultCache,
    InMemoryTokenBudgetGate,
)
from app.modules.ai.pipeline.schemas import GeneratedContent
from app.modules.ai.providers.base import LlmProvider
from app.modules.assembly.repository import PaperAssemblyRepository
from app.modules.assembly.projection import (
    MULTIPART_QUESTION_TYPES,
    project_answer_entry,
    project_version_question,
)
from app.modules.assembly.service import PaperAssemblyError, PaperAssemblyService, _json_value
from app.modules.feasibility.repository import FeasibilityQuestionRepository
from app.modules.feasibility.schemas import FeasibilityRequest
from app.modules.feasibility.service import blueprint_from_request, candidate_from_question
from app.modules.programs.repository import ProgramRepository
from app.modules.questions import service as question_service
from app.modules.questions.pipeline_service import (
    bank_question_resolver,
    create_pipeline_question,
    similarity_checker,
)
from app.modules.retrieval.repository import QuestionBankCandidateSource
from app.modules.retrieval.schemas import BlueprintSlot, SlotRetrievalResult
from app.modules.retrieval.service import RetrievalService
from app.workers.outbox import add_outbox_task
from packages.domain.assembler import CpSatFeasibilitySolver, FeasibilityOutcome


class ResumablePaperAssemblyService:
    """Application service whose public methods are one-transaction typed steps."""

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
        self.compat = PaperAssemblyService(
            session=session,
            settings=settings,
            org_id=org_id,
            provider=provider,
            solver=self.solver,
        )

    async def prepare(
        self, *, paper_id: uuid.UUID, label: str = "Generated Set A"
    ) -> PaperAssemblyRun:
        existing = (
            await self.session.execute(
                select(PaperAssemblyRun).where(
                    PaperAssemblyRun.org_id == self.org_id,
                    PaperAssemblyRun.paper_id == paper_id,
                    PaperAssemblyRun.label == label,
                )
            )
        ).scalar_one_or_none()
        if existing is not None:
            return existing

        paper = await self.repository.get_paper(paper_id)
        if paper is None:
            raise PaperAssemblyError("paper not found in tenant scope")
        payload = FeasibilityRequest.model_validate(paper.blueprint)
        scope, rows = await self._scope_and_rows(paper, payload)
        del scope
        mode_rows = tuple(
            row for row in rows if PaperAssemblyService._mode_allows(payload.generation_mode, row)
        )
        candidates = tuple(
            candidate_from_question(row) for row in mode_rows if row.chapter_id is not None
        )
        result = self.solver.solve(
            candidates,
            blueprint_from_request(payload),
            time_budget_ms=self.settings.AI_SOLVER_TIME_BUDGET_MS,
        )
        if result.outcome is FeasibilityOutcome.INFEASIBLE:
            raise PaperAssemblyError(
                "solver rejected paper: "
                + "; ".join(reason.detail for reason in result.reasons)
            )

        run = PaperAssemblyRun(
            org_id=self.org_id,
            paper_id=paper_id,
            label=label,
            status="running",
            solver_snapshot=self._solve_snapshot(result),
        )
        self.session.add(run)
        await self.session.flush()

        positions: dict[str, int] = {}
        first_slot: PaperAssemblySlot | None = None
        for assignment in result.assignments:
            position = positions.get(assignment.section_id, 0) + 1
            positions[assignment.section_id] = position
            slot_id = f"{assignment.section_id}:{position}"
            checkpoint = PaperAssemblySlot(
                org_id=self.org_id,
                run_id=run.id,
                paper_id=paper_id,
                slot_id=slot_id,
                section_id=assignment.section_id,
                position=position,
                status="pending",
                idempotency_key=self._slot_key(run.id, slot_id, 0),
                solver_question_id=assignment.question_id,
            )
            self.session.add(checkpoint)
            if first_slot is None:
                first_slot = checkpoint
        await self.session.flush()
        if first_slot is None:
            raise PaperAssemblyError("solver returned no slots")
        await self._enqueue_slot(run, first_slot)
        return run

    async def prepare_regeneration(
        self,
        *,
        paper_id: uuid.UUID,
        source_version_id: uuid.UUID,
        target_slot_ids: tuple[str, ...],
        locked_slot_ids: tuple[str, ...],
        request_id: uuid.UUID,
    ) -> PaperAssemblyRun:
        """Plan a targeted run with every non-target source slot frozen.

        P4-T5's residual correctness property comes from representing settled slots as
        completed checkpoints before solving the remaining set.  Teacher-initiated
        regeneration uses that same representation explicitly: requested targets are
        pending, while locked slots *and unlocked slots outside the requested scope*
        are copied byte-for-byte from the immutable source version as completed
        checkpoints.  Any source-mismatch residual solve triggered later therefore
        sees those checkpoints through ``_completed_slots`` and cannot re-litigate
        them.
        """

        label = f"Regeneration {request_id}"
        existing = (
            await self.session.execute(
                select(PaperAssemblyRun).where(
                    PaperAssemblyRun.org_id == self.org_id,
                    PaperAssemblyRun.paper_id == paper_id,
                    PaperAssemblyRun.label == label,
                )
            )
        ).scalar_one_or_none()
        if existing is not None:
            return existing

        paper = await self.repository.get_paper(paper_id)
        if paper is None:
            raise PaperAssemblyError("paper not found in tenant scope")
        source_version = await self.repository.get_version_by_id(
            paper_id, source_version_id
        )
        if source_version is None:
            raise PaperAssemblyError("source paper version not found")

        source_slots = self._source_version_slots(source_version.content)
        source_by_id = {str(item["slotId"]): item for item in source_slots}
        source_ids = set(source_by_id)
        targets = set(target_slot_ids)
        locked = set(locked_slot_ids)
        if not targets:
            raise PaperAssemblyError("at least one target slot is required")
        unknown_targets = targets - source_ids
        unknown_locked = locked - source_ids
        if unknown_targets:
            raise PaperAssemblyError(
                f"target slots are not in source version: {sorted(unknown_targets)}"
            )
        if unknown_locked:
            raise PaperAssemblyError(
                f"locked slots are not in source version: {sorted(unknown_locked)}"
            )
        if targets & locked:
            raise PaperAssemblyError("locked slots cannot be regeneration targets")

        payload = FeasibilityRequest.model_validate(paper.blueprint)
        scope, rows = await self._scope_and_rows(paper, payload)
        del scope
        excluded_ids = {
            uuid.UUID(str(item["id"]))
            for item in source_slots
            if item.get("id") is not None
        }
        mode_rows = tuple(
            row
            for row in rows
            if row.id not in excluded_ids
            and row.chapter_id is not None
            and PaperAssemblyService._mode_allows(payload.generation_mode, row)
        )
        residual_blueprint = self._target_blueprint(
            payload=payload,
            source_by_id=source_by_id,
            target_slot_ids=targets,
        )
        result = self.solver.solve(
            tuple(candidate_from_question(row) for row in mode_rows),
            residual_blueprint,
            time_budget_ms=self.settings.AI_SOLVER_TIME_BUDGET_MS,
        )
        if result.outcome is FeasibilityOutcome.INFEASIBLE:
            raise PaperAssemblyError(
                "regeneration residual solver rejected target slots: "
                + "; ".join(reason.detail for reason in result.reasons)
            )

        assignments: dict[str, list[uuid.UUID]] = {}
        for assignment in result.assignments:
            assignments.setdefault(assignment.section_id, []).append(
                assignment.question_id
            )

        run = PaperAssemblyRun(
            org_id=self.org_id,
            paper_id=paper_id,
            label=label,
            status="running",
            residual_solve_count=1,
            solver_snapshot={
                "regeneration": {
                    "scope": "explicit-targets-with-frozen-complement",
                    "sourceVersionId": str(source_version_id),
                    "targetSlotIds": list(target_slot_ids),
                    "lockedSlotIds": list(locked_slot_ids),
                    "completedSlotsFixed": len(source_slots) - len(targets),
                    "excludedQuestionIds": sorted(str(item) for item in excluded_ids),
                },
                "residualSolves": [
                    {
                        **self._solve_snapshot(result),
                        "scope": "full-requested-unlocked-set",
                        "completedSlotsFixed": len(source_slots) - len(targets),
                    }
                ],
            },
        )
        self.session.add(run)
        await self.session.flush()

        answer_by_slot = self._source_answers(source_version.answer_key)
        first_target: PaperAssemblySlot | None = None
        section_positions: dict[str, int] = {}
        for source in source_slots:
            slot_id = str(source["slotId"])
            section_id = str(source["sectionId"])
            position = section_positions.get(section_id, 0) + 1
            section_positions[section_id] = position
            if slot_id in targets:
                choices = assignments.get(section_id, [])
                if not choices:
                    raise PaperAssemblyError(
                        "regeneration solver assignment mapping was incomplete"
                    )
                checkpoint = PaperAssemblySlot(
                    org_id=self.org_id,
                    run_id=run.id,
                    paper_id=paper_id,
                    slot_id=slot_id,
                    section_id=section_id,
                    position=position,
                    status="pending",
                    idempotency_key=self._slot_key(run.id, slot_id, 0),
                    solver_question_id=choices.pop(0),
                )
                if first_target is None:
                    first_target = checkpoint
            else:
                question_id = uuid.UUID(str(source["id"]))
                existing_answer = answer_by_slot.get(slot_id)
                if (
                    str(source.get("qtype")) in MULTIPART_QUESTION_TYPES
                    and (
                        not isinstance(existing_answer, dict)
                        or not existing_answer.get("subQuestions")
                    )
                ):
                    existing_answer = project_answer_entry(source)
                checkpoint = PaperAssemblySlot(
                    org_id=self.org_id,
                    run_id=run.id,
                    paper_id=paper_id,
                    slot_id=slot_id,
                    section_id=section_id,
                    position=position,
                    status="completed",
                    idempotency_key=self._slot_key(run.id, slot_id, 0),
                    solver_question_id=question_id,
                    question_id=question_id,
                    snapshot=copy.deepcopy(source),
                    answer=copy.deepcopy(
                        existing_answer or project_answer_entry(source)
                    ),
                    trace={
                        "slotId": slot_id,
                        "solverQuestionId": str(question_id),
                        "regeneration": "frozen_locked" if slot_id in locked else "frozen_out_of_scope",
                        "sourceVersionId": str(source_version_id),
                    },
                    completed_at=datetime.now(timezone.utc),
                )
            self.session.add(checkpoint)

        if any(choices for choices in assignments.values()) or first_target is None:
            raise PaperAssemblyError("regeneration solver assignment count was incorrect")
        await self.session.flush()
        await self._enqueue_slot(run, first_target)
        return run

    async def process_slot(
        self, *, run_id: uuid.UUID, slot_id: str
    ) -> dict[str, Any]:
        run = await self._locked_run(run_id)
        checkpoint = (
            await self.session.execute(
                select(PaperAssemblySlot)
                .where(
                    PaperAssemblySlot.org_id == self.org_id,
                    PaperAssemblySlot.run_id == run_id,
                    PaperAssemblySlot.slot_id == slot_id,
                )
                .with_for_update()
            )
        ).scalar_one_or_none()
        if checkpoint is None:
            raise PaperAssemblyError(f"checkpoint {slot_id} not found")
        if checkpoint.status == "completed":
            return {"state": "already_completed", "slot_id": slot_id}

        paper = await self.repository.get_paper(run.paper_id)
        if paper is None:
            raise PaperAssemblyError("paper not found in tenant scope")
        payload = FeasibilityRequest.model_validate(paper.blueprint)
        scope, rows = await self._scope_and_rows(paper, payload)
        by_id = {row.id: row for row in rows if row.chapter_id is not None}
        row = by_id.get(checkpoint.solver_question_id)
        if row is None:
            row = (
                await self.session.execute(
                    select(QuestionBank).where(
                        QuestionBank.id == checkpoint.solver_question_id,
                        QuestionBank.deleted_at.is_(None),
                    )
                )
            ).scalar_one_or_none()
        if row is None:
            raise PaperAssemblyError(
                f"slot {slot_id} solver question is no longer readable"
            )

        checkpoint.status = "running"
        checkpoint.attempt_count += 1
        checkpoint.last_error = None
        await self.session.flush()

        completed = await self._completed_slots(run_id)
        selected_ids = [item.question_id for item in completed if item.question_id]
        retrieval = RetrievalService.from_settings(
            source=QuestionBankCandidateSource(
                session=self.session, scope=scope, org_id=self.org_id
            ),
            settings=self.settings,
        )
        bank_resolver = bank_question_resolver(
            session=self.session, request_scope=scope, org_id=self.org_id
        )
        cascade = CostCascade(
            provider=self.provider,
            cache=InMemoryExactResultCache(),
            bank_resolver=bank_resolver,
            budget=InMemoryTokenBudgetGate(self.settings.AI_TENANT_TOKEN_BUDGET),
            rates=CostRates.from_settings(self.settings),
            similarity_checker=similarity_checker(
                session=self.session, request_scope=scope, org_id=self.org_id
            ),
            escalation_thinking_tokens=self.settings.AI_ESCALATION_THINKING_BUDGET,
        )
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
            excluded_question_ids=selected_ids,
        )
        retrieved = await retrieval.select_candidates(slot, top_k=100)
        candidate = next(
            (item for item in retrieved.candidates if item.question_id == row.id),
            self.compat._seed_candidate(row),
        )
        selected_retrieval = SlotRetrievalResult(
            slot_id=slot_id,
            candidates=[candidate],
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
        preflight = await bank_resolver(candidate)
        gap_reason = None
        if preflight is None:
            gap_reason = "solver-selected question is no longer readable"
        else:
            try:
                cascade.validator.request(preflight, context)
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
        prompts = await self.compat._resolve_prompts() if selected_retrieval.gap else None
        cascade_result = await cascade.run(
            tenant_id=self.org_id, context=context, prompts=prompts
        )
        if cascade_result.question is None:
            raise PaperAssemblyError(
                f"slot {slot_id} degraded: {cascade_result.degrade_reason}"
            )

        question = cascade_result.question
        source_id = question.meta.get("reused_question_id")
        residual_snapshot: dict[str, Any] | None = None
        if source_id is None:
            created = await create_pipeline_question(
                result=cascade_result.pipeline_result,
                session=self.session,
                request_scope=scope,
                org_id=self.org_id,
            )
            question_id = created.id
            if question.source != row.source:
                residual_snapshot = await self._residual_resolve(
                    run=run,
                    current=checkpoint,
                    payload=payload,
                    rows=rows,
                    generated=created,
                    failed_question_id=row.id,
                )
                if checkpoint.solver_question_id != created.id:
                    await self._enqueue_slot(run, checkpoint)
                    return {
                        "state": "residual_replanned",
                        "slot_id": slot_id,
                        "residual": residual_snapshot,
                    }
        else:
            question_id = uuid.UUID(str(source_id))

        if cascade_result.pipeline_result is None:
            flags = await cascade.validator.generated(
                GeneratedContent(
                    **question.model_dump(),
                    cited_question_ids=[question_id],
                    cited_snippet_indices=[],
                ),
                context,
            )
        else:
            flags = list(cascade_result.pipeline_result.flags)

        snapshot = question.model_dump(mode="json")
        snapshot.update(
            id=str(question_id),
            slotId=slot_id,
            sectionId=checkpoint.section_id,
            cascadeRung=cascade_result.cascade_rung,
        )
        checkpoint.question_id = question_id
        checkpoint.snapshot = snapshot
        checkpoint.answer = project_answer_entry(snapshot)
        checkpoint.trace = {
            "slotId": slot_id,
            "solverQuestionId": str(checkpoint.solver_question_id),
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
                    "state": "gap_detected" if selected_retrieval.gap else "candidate_satisfied",
                },
                {
                    "name": "generate",
                    "state": "completed" if cascade_result.cascade_rung >= 2 else "skipped_bank_reuse",
                },
                {"name": "validate", "state": "completed"},
                {"name": "verify", "state": "completed", "flags": flags},
                {"name": "dedup", "state": "completed"},
            ],
            "dedup": "p3-retrieval+p2-t4-final-write",
            "residualSolve": residual_snapshot,
        }
        checkpoint.status = "completed"
        checkpoint.completed_at = datetime.now(timezone.utc)
        await self.session.flush()

        next_slot = (
            await self.session.execute(
                select(PaperAssemblySlot)
                .where(
                    PaperAssemblySlot.run_id == run.id,
                    PaperAssemblySlot.status == "pending",
                )
                .order_by(PaperAssemblySlot.section_id, PaperAssemblySlot.position)
                .limit(1)
            )
        ).scalar_one_or_none()
        if next_slot is not None:
            await self._enqueue_slot(run, next_slot)
        else:
            run.status = "finalizing"
            await add_outbox_task(
                self.session,
                org_id=self.org_id,
                task_name="papergen.workers.finalize_paper",
                idempotency_key=f"paper-run:{run.id}:finalize",
                payload={"org_id": str(self.org_id), "run_id": str(run.id)},
            )
        return {"state": "completed", "slot_id": slot_id, "question_id": str(question_id)}

    async def finalize(self, *, run_id: uuid.UUID) -> dict[str, Any]:
        run = await self._locked_run(run_id)
        if run.status == "completed":
            return {
                "state": "already_completed",
                "paper_version_id": str(run.paper_version_id),
            }
        slots = await self._all_slots(run_id)
        if not slots or any(slot.status != "completed" for slot in slots):
            raise PaperAssemblyError("cannot finalize while slots remain incomplete")
        paper = await self.repository.get_paper(run.paper_id)
        if paper is None:
            raise PaperAssemblyError("paper not found in tenant scope")
        existing = await self.repository.get_version(run.paper_id, run.label)
        if existing is not None:
            run.status = "completed"
            run.paper_version_id = existing.id
            return {"state": "already_completed", "paper_version_id": str(existing.id)}

        payload = FeasibilityRequest.model_validate(paper.blueprint)
        scope, _ = await self._scope_and_rows(paper, payload)
        selected_rows = (
            await self.session.execute(
                select(QuestionBank).where(
                    QuestionBank.id.in_([slot.question_id for slot in slots])
                )
            )
        ).scalars().all()
        exact_result = self.solver.solve(
            tuple(candidate_from_question(row) for row in selected_rows),
            blueprint_from_request(payload),
            time_budget_ms=self.settings.AI_SOLVER_TIME_BUDGET_MS,
        )
        if exact_result.outcome is FeasibilityOutcome.INFEASIBLE:
            raise PaperAssemblyError("final exact-selection solver validation failed")
        selected_ids = [slot.question_id for slot in slots]
        duplicates = await question_service.retrieval_duplicate_pairs(
            session=self.session,
            scope=scope,
            org_id=self.org_id,
            question_ids=tuple(selected_ids),
            near_duplicate_threshold=self.settings.DEDUP_SIMILARITY_THRESHOLD,
        )
        snapshots = [project_version_question(slot.snapshot) for slot in slots]
        checks = PaperAssemblyService._paper_checks(
            payload=payload,
            solve_result=exact_result,
            snapshots=snapshots,
            selected_ids=selected_ids,
            duplicate_pair_count=len(duplicates),
        )
        sections = []
        for section in payload.sections:
            questions = [item for item in snapshots if item["sectionId"] == section.id]
            sections.append(
                {
                    "id": section.id,
                    "questions": questions,
                    "marks": str(
                        sum((Decimal(str(item["marks"])) for item in questions), Decimal(0))
                    ),
                }
            )
        content = {
            "schemaVersion": 1,
            "sections": sections,
            "assembly": {
                "engine": "cp-sat",
                "solverOutcome": exact_result.outcome.value,
                "solverStatus": exact_result.solver_status,
                "selectedAssignments": [_json_value(asdict(item)) for item in exact_result.assignments],
                "compromises": [_json_value(asdict(item)) for item in exact_result.compromises],
                "coverage": [_json_value(asdict(item)) for item in exact_result.coverage],
                "paperChecks": checks,
                "slotTraces": [slot.trace for slot in slots],
                "checkpointRunId": str(run.id),
                "residualSolveCount": run.residual_solve_count,
            },
        }
        version = await self.repository.create_version(
            paper=paper,
            label=run.label,
            content=content,
            answer_key={"questions": [slot.answer for slot in slots]},
            total_marks=exact_result.selected_marks,
        )
        run.status = "completed"
        run.paper_version_id = version.id
        run.solver_snapshot = {
            **run.solver_snapshot,
            "finalExactSelection": self._solve_snapshot(exact_result),
        }
        return {
            "state": "completed",
            "paper_id": str(paper.id),
            "paper_version_id": str(version.id),
            "selected_question_count": len(slots),
            "residual_solve_count": run.residual_solve_count,
        }

    async def _residual_resolve(
        self,
        *,
        run: PaperAssemblyRun,
        current: PaperAssemblySlot,
        payload: FeasibilityRequest,
        rows,
        generated: QuestionBank,
        failed_question_id: uuid.UUID,
    ) -> dict[str, Any]:
        pending = (
            await self.session.execute(
                select(PaperAssemblySlot)
                .where(
                    PaperAssemblySlot.run_id == run.id,
                    PaperAssemblySlot.status.in_(("pending", "running")),
                )
                .order_by(PaperAssemblySlot.section_id, PaperAssemblySlot.position)
                .with_for_update()
            )
        ).scalars().all()
        counts: dict[str, int] = {}
        for slot in pending:
            counts[slot.section_id] = counts.get(slot.section_id, 0) + 1
        original = blueprint_from_request(payload)
        residual_sections = tuple(
            replace(section, question_count=counts[section.id])
            for section in original.sections
            if counts.get(section.id, 0)
        )
        residual_marks = sum(
            (Decimal(section.question_count) * section.marks_per_question for section in residual_sections),
            Decimal(0),
        )
        residual_blueprint = replace(
            original, total_marks=residual_marks, sections=residual_sections
        )
        completed_ids = {
            slot.question_id for slot in await self._completed_slots(run.id) if slot.question_id
        }
        pool_rows = [
            row
            for row in rows
            if row.id not in completed_ids
            and row.id != failed_question_id
            and row.chapter_id is not None
            and PaperAssemblyService._mode_allows(payload.generation_mode, row)
        ]
        pool_rows.append(generated)
        unique_rows = {row.id: row for row in pool_rows}
        result = self.solver.solve(
            tuple(candidate_from_question(row) for row in unique_rows.values()),
            residual_blueprint,
            time_budget_ms=self.settings.AI_SOLVER_TIME_BUDGET_MS,
        )
        if result.outcome is FeasibilityOutcome.INFEASIBLE:
            raise PaperAssemblyError(
                "residual solver rejected remaining paper: "
                + "; ".join(reason.detail for reason in result.reasons)
            )
        assignments: dict[str, list[uuid.UUID]] = {}
        for assignment in result.assignments:
            assignments.setdefault(assignment.section_id, []).append(assignment.question_id)
        # Keep the just-generated replacement in the affected slot when selected;
        # all other assignments are deterministically distributed across the full
        # remaining set in that section.
        if generated.id in assignments.get(current.section_id, []):
            assignments[current.section_id].remove(generated.id)
            current.solver_question_id = generated.id
        for slot in pending:
            if slot.id == current.id and slot.solver_question_id == generated.id:
                continue
            choices = assignments.get(slot.section_id, [])
            if not choices:
                raise PaperAssemblyError("residual solver assignment mapping was incomplete")
            slot.solver_question_id = choices.pop(0)
        run.residual_solve_count += 1
        snapshot = self._solve_snapshot(result)
        snapshot.update(
            {
                "scope": "full-remaining-unfilled-set",
                "changedSlotId": current.slot_id,
                "failedQuestionId": str(failed_question_id),
                "replacementQuestionId": str(generated.id),
                "completedSlotsFixed": len(completed_ids),
            }
        )
        history = list(run.solver_snapshot.get("residualSolves", []))
        history.append(snapshot)
        run.solver_snapshot = {**run.solver_snapshot, "residualSolves": history}
        current.idempotency_key = self._slot_key(
            run.id, current.slot_id, run.residual_solve_count
        )
        return snapshot

    async def _scope_and_rows(self, paper, payload):
        membership = await self.repository.get_active_membership(paper.created_by)
        if membership is None:
            raise PaperAssemblyError("paper author has no active organization membership")
        subject_ids = None
        if membership.role == "teacher":
            subject_ids = await ProgramRepository(self.session, self.org_id).get_teacher_subject_ids(
                paper.created_by
            )
        scope = RequestScope(
            org_id=self.org_id,
            user_id=paper.created_by,
            role=membership.role,
            branch_id=membership.branch_id,
            subject_ids=subject_ids,
        )
        rows = await FeasibilityQuestionRepository(self.session, self.org_id).list_eligible(
            board_id=payload.board_id,
            class_id=payload.class_id,
            subject_id=payload.subject_id,
            curriculum_version_id=payload.curriculum_version_id,
            chapter_ids=tuple(payload.chapter_ids),
            language=payload.language,
            subject_scope_ids=scope.subject_ids,
        )
        return scope, tuple(rows)

    @staticmethod
    def _source_version_slots(content: dict[str, Any]) -> list[dict[str, Any]]:
        sections = content.get("sections")
        if not isinstance(sections, list):
            raise PaperAssemblyError("source paper version has no sections")
        slots: list[dict[str, Any]] = []
        for section in sections:
            if not isinstance(section, dict) or not isinstance(
                section.get("questions"), list
            ):
                raise PaperAssemblyError("source paper version has invalid sections")
            section_id = str(section.get("id", ""))
            for question in section["questions"]:
                if not isinstance(question, dict):
                    raise PaperAssemblyError("source paper version has invalid question")
                snapshot = copy.deepcopy(question)
                snapshot.setdefault("sectionId", section_id)
                if not snapshot.get("slotId") or not snapshot.get("id"):
                    raise PaperAssemblyError(
                        "source paper version question is missing slotId or id"
                    )
                slots.append(snapshot)
        slot_ids = [str(item["slotId"]) for item in slots]
        if len(slot_ids) != len(set(slot_ids)):
            raise PaperAssemblyError("source paper version repeats a slotId")
        return slots

    @staticmethod
    def _source_answers(answer_key: Any) -> dict[str, dict[str, Any]]:
        if not isinstance(answer_key, dict) or not isinstance(
            answer_key.get("questions"), list
        ):
            return {}
        return {
            str(item["slotId"]): item
            for item in answer_key["questions"]
            if isinstance(item, dict) and item.get("slotId")
        }

    @staticmethod
    def _target_blueprint(
        *,
        payload: FeasibilityRequest,
        source_by_id: dict[str, dict[str, Any]],
        target_slot_ids: set[str],
    ):
        counts: dict[str, int] = {}
        for slot_id in target_slot_ids:
            section_id = str(source_by_id[slot_id]["sectionId"])
            counts[section_id] = counts.get(section_id, 0) + 1
        original = blueprint_from_request(payload)
        sections = tuple(
            replace(section, question_count=counts[section.id])
            for section in original.sections
            if counts.get(section.id, 0)
        )
        if sum(section.question_count for section in sections) != len(target_slot_ids):
            raise PaperAssemblyError(
                "source version contains a section outside the paper blueprint"
            )
        total_marks = sum(
            (
                Decimal(section.question_count) * section.marks_per_question
                for section in sections
            ),
            Decimal(0),
        )
        return replace(original, total_marks=total_marks, sections=sections)

    async def _locked_run(self, run_id: uuid.UUID) -> PaperAssemblyRun:
        run = (
            await self.session.execute(
                select(PaperAssemblyRun)
                .where(
                    PaperAssemblyRun.id == run_id,
                    PaperAssemblyRun.org_id == self.org_id,
                )
                .with_for_update()
            )
        ).scalar_one_or_none()
        if run is None:
            raise PaperAssemblyError("assembly run not found in tenant scope")
        return run

    async def _completed_slots(self, run_id: uuid.UUID):
        return (
            await self.session.execute(
                select(PaperAssemblySlot)
                .where(
                    PaperAssemblySlot.run_id == run_id,
                    PaperAssemblySlot.status == "completed",
                )
                .order_by(PaperAssemblySlot.section_id, PaperAssemblySlot.position)
            )
        ).scalars().all()

    async def _all_slots(self, run_id: uuid.UUID):
        return (
            await self.session.execute(
                select(PaperAssemblySlot)
                .where(PaperAssemblySlot.run_id == run_id)
                .order_by(PaperAssemblySlot.section_id, PaperAssemblySlot.position)
            )
        ).scalars().all()

    async def _enqueue_slot(self, run: PaperAssemblyRun, slot: PaperAssemblySlot) -> None:
        await add_outbox_task(
            self.session,
            org_id=self.org_id,
            task_name="papergen.workers.process_paper_slot",
            idempotency_key=slot.idempotency_key,
            payload={
                "org_id": str(self.org_id),
                "run_id": str(run.id),
                "slot_id": slot.slot_id,
            },
        )

    @staticmethod
    def _slot_key(run_id: uuid.UUID, slot_id: str, plan_revision: int) -> str:
        return f"paper-run:{run_id}:slot:{slot_id}:plan:{plan_revision}"

    @staticmethod
    def _solve_snapshot(result) -> dict[str, Any]:
        return {
            "outcome": result.outcome.value,
            "solverStatus": result.solver_status,
            "assignments": [_json_value(asdict(item)) for item in result.assignments],
            "compromises": [_json_value(asdict(item)) for item in result.compromises],
            "coverage": [_json_value(asdict(item)) for item in result.coverage],
            "selectedMarks": str(result.selected_marks),
        }


__all__ = ["ResumablePaperAssemblyService"]
