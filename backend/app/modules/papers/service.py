"""Paper creation and transactional initial-generation scheduling."""

from __future__ import annotations

import uuid

from sqlalchemy import select, update
from sqlalchemy.dialects.postgresql import insert
from sqlalchemy.ext.asyncio import AsyncSession

from app.common.deps import RequestScope
from app.common.errors import NotFoundError, ValidationError
from app.db.exceptions import OptimisticLockError
from app.core.config import Settings
from app.models.assembly import PaperAssemblyRun, PaperAssemblySlot
from app.models.paper import GeneratedPaper
from app.models.paper_draft import PaperEditorDraft
from app.modules.assembly.repository import PaperAssemblyRepository
from app.modules.feasibility.schemas import CompromiseOut, CoverageFactOut
from app.modules.feasibility.repository import FeasibilityQuestionRepository
from app.modules.feasibility.service import FeasibilityService
from app.modules.papers.schemas import (
    GeneratePaperRequest,
    GenerationAccepted,
    PaperCoverageReport,
    PaperDraftResource,
    PaperRealtimeSlotState,
    PaperRealtimeState,
    SavePaperDraftRequest,
)
from app.modules.realtime.events import paper_sequence_key
from app.infra.redis import get_redis
from app.workers.outbox import schedule_paper_generation


class PaperGenerationService:
    """Validate, persist, and schedule a paper in the caller's transaction."""

    def __init__(self, *, session: AsyncSession, settings: Settings | None = None) -> None:
        self._session = session
        self._settings = settings

    async def submit(
        self,
        *,
        payload: GeneratePaperRequest,
        scope: RequestScope,
        path_org_id: uuid.UUID,
    ) -> GenerationAccepted:
        if self._settings is None:
            raise RuntimeError("paper generation settings are required")
        # This is the exact same authorization, bank snapshot, mode filtering, DTO
        # adapter, and CP-SAT path used by the standalone feasibility pre-check.
        feasibility = await FeasibilityService(
            repository=FeasibilityQuestionRepository(self._session, path_org_id),
            settings=self._settings,
        ).check(
            payload=payload.blueprint,
            scope=scope,
            path_org_id=path_org_id,
        )
        if feasibility.outcome == "infeasible":
            reason = "; ".join(item.detail for item in feasibility.reasons)
            raise ValidationError(
                reason or "The paper blueprint is infeasible.",
                code="BLUEPRINT_INFEASIBLE",
                eh_ref="EH-9",
            )

        paper = GeneratedPaper(
            org_id=scope.org_id,
            created_by=scope.user_id,
            blueprint=payload.blueprint.model_dump(mode="json", by_alias=True),
            curriculum_version_id=payload.blueprint.curriculum_version_id,
            title=payload.title,
            total_marks=payload.blueprint.total_marks,
            duration_min=payload.duration_minutes,
            status="draft",
        )
        self._session.add(paper)
        await self._session.flush()

        outbox = await schedule_paper_generation(
            self._session,
            org_id=scope.org_id,
            paper_id=paper.id,
        )
        return GenerationAccepted(
            paper_id=paper.id,
            job_id=outbox.id,
            outbox_id=outbox.id,
            status="queued",
        )

    async def coverage_report(
        self,
        *,
        paper_id: uuid.UUID,
        version_id: uuid.UUID,
        scope: RequestScope,
    ) -> PaperCoverageReport:
        """Project persisted final-selection facts; never invoke the solver here."""

        repository = PaperAssemblyRepository(self._session, scope.org_id)
        paper = await repository.get_paper(paper_id)
        if paper is None:
            raise NotFoundError("Paper not found.")
        version = await repository.get_version_by_id(paper_id, version_id)
        if version is None:
            raise NotFoundError("Paper version not found.")

        assembly = version.content.get("assembly")
        if not isinstance(assembly, dict):
            raise NotFoundError("Coverage report is not available for this paper version.")
        raw_coverage = assembly.get("coverage")
        raw_compromises = assembly.get("compromises")
        if not isinstance(raw_coverage, list) or not isinstance(raw_compromises, list):
            raise NotFoundError("Coverage report is not available for this paper version.")

        return PaperCoverageReport(
            paper_id=paper.id,
            version_id=version.id,
            solver_outcome=str(assembly.get("solverOutcome", "unknown")),
            coverage=[CoverageFactOut.model_validate(item) for item in raw_coverage],
            compromises=[CompromiseOut.model_validate(item) for item in raw_compromises],
        )

    async def realtime_state(
        self,
        *,
        paper_id: uuid.UUID,
        scope: RequestScope,
    ) -> PaperRealtimeState:
        """Read the latest committed assembly checkpoints without mutating the run."""

        paper = await PaperAssemblyRepository(self._session, scope.org_id).get_paper(paper_id)
        if paper is None:
            raise NotFoundError("Paper not found.")

        run = (
            await self._session.execute(
                select(PaperAssemblyRun)
                .where(
                    PaperAssemblyRun.paper_id == paper_id,
                    PaperAssemblyRun.org_id == scope.org_id,
                )
                .order_by(PaperAssemblyRun.created_at.desc(), PaperAssemblyRun.id.desc())
                .limit(1)
            )
        ).scalar_one_or_none()

        sequence = 0
        try:
            raw_sequence = await get_redis().get(paper_sequence_key(scope.org_id, paper_id))
            sequence = int(raw_sequence or 0)
        except Exception:  # noqa: BLE001 - SQL checkpoints remain authoritative
            pass

        if run is None:
            return PaperRealtimeState(
                paper_id=paper_id,
                status="queued",
                sequence=sequence,
                filled_slots=0,
                total_slots=0,
                slots=[],
            )

        checkpoints = (
            await self._session.execute(
                select(PaperAssemblySlot)
                .where(
                    PaperAssemblySlot.run_id == run.id,
                    PaperAssemblySlot.org_id == scope.org_id,
                )
                .order_by(PaperAssemblySlot.section_id, PaperAssemblySlot.position)
            )
        ).scalars().all()
        filled = sum(checkpoint.status == "completed" for checkpoint in checkpoints)
        slots = [
            PaperRealtimeSlotState(
                slot_id=checkpoint.slot_id,
                section_id=checkpoint.section_id,
                position=checkpoint.position,
                status=checkpoint.status,
                attempt_count=checkpoint.attempt_count,
                question=checkpoint.snapshot,
                error=(
                    "Question generation stopped after all retry attempts. Retry generation."
                    if checkpoint.status == "failed"
                    else None
                ),
            )
            for checkpoint in checkpoints
        ]
        return PaperRealtimeState(
            paper_id=paper_id,
            run_id=run.id,
            status=run.status,
            version_id=run.paper_version_id,
            sequence=sequence,
            filled_slots=filled,
            total_slots=len(checkpoints),
            slots=slots,
        )


class PaperDraftService:
    """Owner-scoped full-snapshot persistence with atomic compare-and-swap writes."""

    def __init__(self, *, session: AsyncSession) -> None:
        self._session = session

    async def load(
        self,
        *,
        draft_id: uuid.UUID,
        scope: RequestScope,
    ) -> PaperDraftResource:
        row = await self._owned_row(draft_id=draft_id, scope=scope)
        if row is None:
            raise NotFoundError("Paper draft not found.")
        return self._resource(row)

    async def save(
        self,
        *,
        draft_id: uuid.UUID,
        payload: SavePaperDraftRequest,
        scope: RequestScope,
    ) -> PaperDraftResource:
        paper_id = await self._paper_id(payload.document, scope)

        if payload.expected_revision is None:
            statement = (
                insert(PaperEditorDraft)
                .values(
                    id=draft_id,
                    org_id=scope.org_id,
                    owner_id=scope.user_id,
                    paper_id=paper_id,
                    document=payload.document,
                    revision=1,
                )
                .on_conflict_do_nothing(index_elements=[PaperEditorDraft.id])
                .returning(PaperEditorDraft)
            )
            row = (await self._session.execute(statement)).scalar_one_or_none()
            if row is not None:
                return self._resource(row)

            # Two first saves can race. The unique PK chooses the winner; the loser
            # receives the same 409 as any later stale revision, never an IntegrityError.
            existing = await self._owned_row(draft_id=draft_id, scope=scope)
            if existing is None:
                raise NotFoundError("Paper draft not found.")
            raise OptimisticLockError(
                entity="paper_editor_drafts",
                entity_id=draft_id,
                expected_version=0,
            )

        statement = (
            update(PaperEditorDraft)
            .where(
                PaperEditorDraft.id == draft_id,
                PaperEditorDraft.org_id == scope.org_id,
                PaperEditorDraft.owner_id == scope.user_id,
                PaperEditorDraft.revision == payload.expected_revision,
            )
            .values(
                document=payload.document,
                paper_id=paper_id,
                revision=PaperEditorDraft.revision + 1,
            )
            .returning(PaperEditorDraft)
        )
        row = (await self._session.execute(statement)).scalar_one_or_none()
        if row is not None:
            return self._resource(row)

        existing = await self._owned_row(draft_id=draft_id, scope=scope)
        if existing is None:
            raise NotFoundError("Paper draft not found.")
        raise OptimisticLockError(
            entity="paper_editor_drafts",
            entity_id=draft_id,
            expected_version=payload.expected_revision,
        )

    async def _owned_row(
        self,
        *,
        draft_id: uuid.UUID,
        scope: RequestScope,
    ) -> PaperEditorDraft | None:
        return (
            await self._session.execute(
                select(PaperEditorDraft).where(
                    PaperEditorDraft.id == draft_id,
                    PaperEditorDraft.org_id == scope.org_id,
                    PaperEditorDraft.owner_id == scope.user_id,
                )
            )
        ).scalar_one_or_none()

    async def _paper_id(
        self,
        document: dict[str, object],
        scope: RequestScope,
    ) -> uuid.UUID | None:
        raw_paper_id = document.get("paperId")
        if raw_paper_id is None:
            return None
        try:
            paper_id = uuid.UUID(str(raw_paper_id))
        except (ValueError, TypeError, AttributeError) as exc:
            raise ValidationError("The draft paperId is invalid.") from exc

        exists = await self._session.scalar(
            select(GeneratedPaper.id).where(
                GeneratedPaper.id == paper_id,
                GeneratedPaper.org_id == scope.org_id,
            )
        )
        if exists is None:
            raise NotFoundError("Paper not found.")
        return paper_id

    @staticmethod
    def _resource(row: PaperEditorDraft) -> PaperDraftResource:
        return PaperDraftResource(
            draft_id=row.id,
            paper_id=row.paper_id,
            revision=row.revision,
            document=row.document,
            updated_at=row.updated_at,
        )


__all__ = ["PaperDraftService", "PaperGenerationService"]
