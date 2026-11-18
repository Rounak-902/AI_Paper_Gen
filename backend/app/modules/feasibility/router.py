"""Paper feasibility endpoint: synchronous and deliberately pre-enqueue.

Request flow today::

    auth -> org/scope guards -> RLS transaction -> P2 bank snapshot -> CP-SAT -> response

There is intentionally no Celery import and no enqueue call in this module.  P4-T4/T5
will add paper persistence and generation enqueueing only after this result is accepted.
"""

from __future__ import annotations

import uuid

from fastapi import APIRouter, Depends, Path
from sqlalchemy.ext.asyncio import AsyncSession

from app.common.deps import RequestScope, get_db, get_scope
from app.common.routing import EnvelopeRoute
from app.core.config import Settings, get_settings
from app.modules.feasibility import schemas
from app.modules.feasibility.repository import FeasibilityQuestionRepository
from app.modules.feasibility.service import FeasibilityService


router = APIRouter(
    prefix="/api/v1/orgs/{org_id}/papers/feasibility",
    tags=["Papers"],
    route_class=EnvelopeRoute,
    dependencies=[Depends(get_scope)],
)


@router.post("")
async def check_paper_feasibility(
    payload: schemas.FeasibilityDraftRequest,
    org_id: uuid.UUID = Path(...),
    scope: RequestScope = Depends(get_scope),
    session: AsyncSession = Depends(get_db),
    settings: Settings = Depends(get_settings),
) -> schemas.FeasibilityResponse:
    """Run the real-bank CP-SAT gate synchronously before any future enqueue."""

    service = FeasibilityService(
        repository=FeasibilityQuestionRepository(session, org_id),
        settings=settings,
    )
    return await service.check(
        payload=payload,
        scope=scope,
        path_org_id=org_id,
    )


__all__ = ["router"]
