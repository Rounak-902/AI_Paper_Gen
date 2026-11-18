"""P6-T4 approval request, history, comment, and version-diff endpoints."""

from __future__ import annotations

import uuid

from fastapi import APIRouter, Depends, Path, Query, status
from sqlalchemy.ext.asyncio import AsyncSession

from app.common.deps import OrgContext, RequestScope, get_db, get_scope, require_roles
from app.common.routing import EnvelopeRoute
from app.modules.approvals import schemas
from app.modules.approvals.service import ApprovalService


router = APIRouter(
    prefix="/api/v1",
    tags=["Approvals"],
    route_class=EnvelopeRoute,
)

_REVIEW_ROLES = ("coaching_admin", "super_admin")


@router.post(
    "/papers/{paper_id}/versions/{version_id}/approval-requests",
    response_model=schemas.ApprovalRequestOut,
    status_code=status.HTTP_201_CREATED,
)
async def submit_for_approval(
    payload: schemas.ApprovalSubmitIn,
    paper_id: uuid.UUID = Path(...),
    version_id: uuid.UUID = Path(...),
    scope: RequestScope = Depends(get_scope),
    session: AsyncSession = Depends(get_db),
) -> schemas.ApprovalRequestOut:
    return await ApprovalService(session, scope).submit(
        paper_id=paper_id, version_id=version_id, payload=payload
    )


@router.get(
    "/papers/{paper_id}/approval-requests",
    response_model=list[schemas.ApprovalRequestOut],
)
async def list_paper_approval_rounds(
    paper_id: uuid.UUID = Path(...),
    scope: RequestScope = Depends(get_scope),
    session: AsyncSession = Depends(get_db),
) -> list[schemas.ApprovalRequestOut]:
    return await ApprovalService(session, scope).list_for_paper(paper_id)


@router.get(
    "/papers/{paper_id}/versions/{version_id}/diff",
    response_model=schemas.PaperVersionDiffOut,
)
async def diff_paper_versions(
    paper_id: uuid.UUID = Path(...),
    version_id: uuid.UUID = Path(...),
    base_version_id: uuid.UUID | None = Query(default=None, alias="baseVersionId"),
    scope: RequestScope = Depends(get_scope),
    session: AsyncSession = Depends(get_db),
) -> schemas.PaperVersionDiffOut:
    return await ApprovalService(session, scope).diff(
        paper_id=paper_id,
        target_version_id=version_id,
        base_version_id=base_version_id,
    )


@router.get(
    "/approval-requests", response_model=list[schemas.ApprovalRequestOut]
)
async def approval_queue(
    _ctx: OrgContext = Depends(require_roles(*_REVIEW_ROLES)),
    scope: RequestScope = Depends(get_scope),
    session: AsyncSession = Depends(get_db),
) -> list[schemas.ApprovalRequestOut]:
    return await ApprovalService(session, scope).queue()


@router.get(
    "/approval-requests/{request_id}", response_model=schemas.ApprovalHistoryOut
)
async def approval_history(
    request_id: uuid.UUID = Path(...),
    scope: RequestScope = Depends(get_scope),
    session: AsyncSession = Depends(get_db),
) -> schemas.ApprovalHistoryOut:
    return await ApprovalService(session, scope).history(request_id)


@router.post(
    "/approval-requests/{request_id}/review",
    response_model=schemas.ApprovalRequestOut,
)
async def start_review(
    payload: schemas.ApprovalTransitionIn,
    request_id: uuid.UUID = Path(...),
    _ctx: OrgContext = Depends(require_roles(*_REVIEW_ROLES)),
    scope: RequestScope = Depends(get_scope),
    session: AsyncSession = Depends(get_db),
) -> schemas.ApprovalRequestOut:
    return await ApprovalService(session, scope).start_review(request_id, payload)


@router.post(
    "/approval-requests/{request_id}/approve",
    response_model=schemas.ApprovalRequestOut,
)
async def approve(
    payload: schemas.ApprovalTransitionIn,
    request_id: uuid.UUID = Path(...),
    _ctx: OrgContext = Depends(require_roles(*_REVIEW_ROLES)),
    scope: RequestScope = Depends(get_scope),
    session: AsyncSession = Depends(get_db),
) -> schemas.ApprovalRequestOut:
    return await ApprovalService(session, scope).decide(
        request_id, payload, decision="approved"
    )


@router.post(
    "/approval-requests/{request_id}/reject",
    response_model=schemas.ApprovalRequestOut,
)
async def reject(
    payload: schemas.ApprovalTransitionIn,
    request_id: uuid.UUID = Path(...),
    _ctx: OrgContext = Depends(require_roles(*_REVIEW_ROLES)),
    scope: RequestScope = Depends(get_scope),
    session: AsyncSession = Depends(get_db),
) -> schemas.ApprovalRequestOut:
    return await ApprovalService(session, scope).decide(
        request_id, payload, decision="rejected"
    )


@router.post(
    "/approval-requests/{request_id}/withdraw-approval",
    response_model=schemas.ApprovalRequestOut,
)
async def withdraw_approval(
    payload: schemas.ApprovalTransitionIn,
    request_id: uuid.UUID = Path(...),
    _ctx: OrgContext = Depends(require_roles(*_REVIEW_ROLES)),
    scope: RequestScope = Depends(get_scope),
    session: AsyncSession = Depends(get_db),
) -> schemas.ApprovalRequestOut:
    return await ApprovalService(session, scope).withdraw_approval(request_id, payload)


@router.post(
    "/approval-requests/{request_id}/comments",
    response_model=schemas.ApprovalCommentOut,
    status_code=status.HTTP_201_CREATED,
)
async def add_question_comment(
    payload: schemas.ApprovalCommentIn,
    request_id: uuid.UUID = Path(...),
    _ctx: OrgContext = Depends(require_roles(*_REVIEW_ROLES)),
    scope: RequestScope = Depends(get_scope),
    session: AsyncSession = Depends(get_db),
) -> schemas.ApprovalCommentOut:
    return await ApprovalService(session, scope).add_comment(request_id, payload)


__all__ = ["router"]
