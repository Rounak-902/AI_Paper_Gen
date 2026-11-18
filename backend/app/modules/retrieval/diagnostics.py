"""Non-production HTTP adapter used to verify the internal retrieval service."""

from __future__ import annotations

import uuid

from fastapi import APIRouter, Depends, Path
from sqlalchemy.ext.asyncio import AsyncSession

from app.common.deps import RequestScope, get_db, get_scope
from app.common.routing import EnvelopeRoute
from app.core.config import Settings, get_settings
from app.modules.retrieval.repository import QuestionBankCandidateSource
from app.modules.retrieval.schemas import (
    BlueprintRetrievalRequest,
    BlueprintRetrievalResult,
)
from app.modules.retrieval.service import RetrievalService


router = APIRouter(
    prefix="/api/v1/_diag/orgs/{org_id}/retrieval/candidates",
    tags=["Diagnostics"],
    route_class=EnvelopeRoute,
    dependencies=[Depends(get_scope)],
)


@router.post("", include_in_schema=False)
async def retrieve_candidates(
    payload: BlueprintRetrievalRequest,
    org_id: uuid.UUID = Path(...),
    scope: RequestScope = Depends(get_scope),
    session: AsyncSession = Depends(get_db),
    settings: Settings = Depends(get_settings),
) -> BlueprintRetrievalResult:
    """Run the real read pipeline; mounted only in local/test environments."""

    source = QuestionBankCandidateSource(
        session=session,
        scope=scope,
        org_id=org_id,
    )
    retrieval = RetrievalService.from_settings(source=source, settings=settings)
    return await retrieval.select_for_blueprint(payload)


__all__ = ["router"]
