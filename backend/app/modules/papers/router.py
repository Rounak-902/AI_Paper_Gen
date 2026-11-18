"""Paper submission and teacher-initiated regeneration APIs.

Initial submission validates the shared feasibility blueprint before atomically
creating the paper and its P4-T5 prepare-paper outbox task. Regeneration remains the
small P5-T3 extension that carries an explicit lock snapshot into the same resumable
orchestration.
"""

from __future__ import annotations

import uuid

from fastapi import APIRouter, Depends, Path, status
from sqlalchemy.ext.asyncio import AsyncSession

from app.common.deps import RequestScope, get_db, get_scope
from app.common.errors import NotFoundError, ValidationError
from app.common.routing import EnvelopeRoute
from app.core.config import Settings, get_settings
from app.modules.ai.providers.factory import create_llm_provider
from app.modules.assembly.resumable import ResumablePaperAssemblyService
from app.modules.assembly.service import PaperAssemblyError
from app.modules.papers.schemas import (
    GeneratePaperRequest,
    GenerationAccepted,
    PaperCoverageReport,
    PaperDraftResource,
    PaperRealtimeState,
    RegenerationAccepted,
    RegenerationRequest,
    SavePaperDraftRequest,
)
from app.modules.papers.service import PaperDraftService, PaperGenerationService


router = APIRouter(
    prefix="/api/v1/papers",
    tags=["Papers"],
    route_class=EnvelopeRoute,
    dependencies=[Depends(get_scope)],
)

generation_router = APIRouter(
    prefix="/api/v1/orgs/{org_id}/papers",
    tags=["Papers"],
    route_class=EnvelopeRoute,
    dependencies=[Depends(get_scope)],
)


@generation_router.get(
    "/drafts/{draft_id}",
    response_model=PaperDraftResource,
)
async def get_paper_draft(
    draft_id: uuid.UUID = Path(...),
    scope: RequestScope = Depends(get_scope),
    session: AsyncSession = Depends(get_db),
) -> PaperDraftResource:
    return await PaperDraftService(session=session).load(
        draft_id=draft_id,
        scope=scope,
    )


@generation_router.put(
    "/drafts/{draft_id}",
    response_model=PaperDraftResource,
)
async def save_paper_draft(
    payload: SavePaperDraftRequest,
    draft_id: uuid.UUID = Path(...),
    scope: RequestScope = Depends(get_scope),
    session: AsyncSession = Depends(get_db),
) -> PaperDraftResource:
    return await PaperDraftService(session=session).save(
        draft_id=draft_id,
        payload=payload,
        scope=scope,
    )


@generation_router.post(
    "/generate",
    response_model=GenerationAccepted,
    status_code=status.HTTP_202_ACCEPTED,
)
async def generate_paper(
    payload: GeneratePaperRequest,
    org_id: uuid.UUID = Path(...),
    scope: RequestScope = Depends(get_scope),
    session: AsyncSession = Depends(get_db),
    settings: Settings = Depends(get_settings),
) -> GenerationAccepted:
    """Create a validated paper and schedule prepare_paper atomically."""

    return await PaperGenerationService(session=session, settings=settings).submit(
        payload=payload,
        scope=scope,
        path_org_id=org_id,
    )


@router.post(
    "/{paper_id}/regenerations",
    response_model=RegenerationAccepted,
    status_code=status.HTTP_202_ACCEPTED,
)
async def regenerate_paper_slots(
    payload: RegenerationRequest,
    paper_id: uuid.UUID = Path(...),
    scope: RequestScope = Depends(get_scope),
    session: AsyncSession = Depends(get_db),
    settings: Settings = Depends(get_settings),
) -> RegenerationAccepted:
    """Freeze the request's complement and enqueue only the requested slots."""

    try:
        provider = create_llm_provider(settings)
        async with provider:
            run = await ResumablePaperAssemblyService(
                session=session,
                settings=settings,
                org_id=scope.org_id,
                provider=provider,
            ).prepare_regeneration(
                paper_id=paper_id,
                source_version_id=payload.source_version_id,
                target_slot_ids=tuple(payload.target_slot_ids),
                locked_slot_ids=tuple(payload.locked_slot_ids),
                request_id=payload.request_id,
            )
    except PaperAssemblyError as exc:
        if "not found" in str(exc):
            raise NotFoundError(str(exc)) from exc
        raise ValidationError(str(exc)) from exc

    return RegenerationAccepted(
        job_id=run.id,
        run_id=run.id,
        paper_id=paper_id,
        source_version_id=payload.source_version_id,
        target_slot_ids=payload.target_slot_ids,
        locked_slot_ids=payload.locked_slot_ids,
        status=run.status,
    )


@router.get(
    "/{paper_id}/realtime-state",
    response_model=PaperRealtimeState,
)
async def get_paper_realtime_state(
    paper_id: uuid.UUID = Path(...),
    scope: RequestScope = Depends(get_scope),
    session: AsyncSession = Depends(get_db),
) -> PaperRealtimeState:
    """Return committed generation state for reconnect and polling convergence."""

    return await PaperGenerationService(session=session).realtime_state(
        paper_id=paper_id,
        scope=scope,
    )


@router.get(
    "/{paper_id}/versions/{version_id}/coverage-report",
    response_model=PaperCoverageReport,
)
async def get_paper_coverage_report(
    paper_id: uuid.UUID = Path(...),
    version_id: uuid.UUID = Path(...),
    scope: RequestScope = Depends(get_scope),
    session: AsyncSession = Depends(get_db),
) -> PaperCoverageReport:
    """Return the exact final solver facts persisted with this version."""

    return await PaperGenerationService(session=session).coverage_report(
        paper_id=paper_id,
        version_id=version_id,
        scope=scope,
    )


__all__ = ["generation_router", "router"]
