from __future__ import annotations

import uuid

from fastapi import APIRouter, Depends, Path, Query
from sqlalchemy.ext.asyncio import AsyncSession

from app.common.deps import RequestScope, get_db, get_scope
from app.common.routing import EnvelopeRoute
from app.modules.notifications import schemas, service

router = APIRouter(
    prefix="/api/v1/notifications",
    tags=["Notifications"],
    route_class=EnvelopeRoute,
)


@router.get("", response_model=schemas.NotificationPage)
async def notifications(
    limit: int = Query(default=20, ge=1, le=100),
    cursor: str | None = Query(default=None),
    unread_only: bool = Query(default=False, alias="unreadOnly"),
    scope: RequestScope = Depends(get_scope),
    session: AsyncSession = Depends(get_db),
) -> schemas.NotificationPage:
    return await service.list_notifications(
        session,
        org_id=scope.org_id,
        user_id=scope.user_id,
        limit=limit,
        cursor=cursor,
        unread_only=unread_only,
    )


@router.get("/unread-count", response_model=schemas.UnreadCountOut)
async def notification_unread_count(
    scope: RequestScope = Depends(get_scope),
    session: AsyncSession = Depends(get_db),
) -> schemas.UnreadCountOut:
    return schemas.UnreadCountOut(
        unread_count=await service.unread_count(
            session, org_id=scope.org_id, user_id=scope.user_id
        )
    )


@router.post("/read-all", response_model=schemas.MarkAllReadOut)
async def read_all_notifications(
    scope: RequestScope = Depends(get_scope),
    session: AsyncSession = Depends(get_db),
) -> schemas.MarkAllReadOut:
    return schemas.MarkAllReadOut(
        marked_read=await service.mark_all_read(
            session, org_id=scope.org_id, user_id=scope.user_id
        )
    )


@router.post("/{notification_id}/read", response_model=schemas.NotificationOut)
async def read_notification(
    notification_id: uuid.UUID = Path(...),
    scope: RequestScope = Depends(get_scope),
    session: AsyncSession = Depends(get_db),
) -> schemas.NotificationOut:
    row = await service.mark_read(
        session,
        org_id=scope.org_id,
        user_id=scope.user_id,
        notification_id=notification_id,
    )
    return service._out(row)


__all__ = ["router"]
