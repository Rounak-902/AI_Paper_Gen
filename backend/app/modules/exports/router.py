"""Asynchronous export request, polling/history, and signed-download endpoints."""

from __future__ import annotations

import uuid

from fastapi import APIRouter, Depends, Path, Query, status
from sqlalchemy.ext.asyncio import AsyncSession

from app.common.deps import RequestScope, get_db, get_scope
from app.common.routing import EnvelopeRoute
from app.core.config import Settings, get_settings
from app.modules.exports.schemas import ExportCreateIn, ExportJobOut, SignedDownloadOut
from app.modules.exports.service import ExportService


router = APIRouter(
    prefix="/api/v1/papers",
    tags=["Exports"],
    route_class=EnvelopeRoute,
    dependencies=[Depends(get_scope)],
)


@router.post(
    "/{paper_id}/versions/{version_id}/exports",
    response_model=ExportJobOut,
    status_code=status.HTTP_202_ACCEPTED,
)
async def create_export(
    payload: ExportCreateIn,
    paper_id: uuid.UUID = Path(...),
    version_id: uuid.UUID = Path(...),
    scope: RequestScope = Depends(get_scope),
    session: AsyncSession = Depends(get_db),
    settings: Settings = Depends(get_settings),
) -> ExportJobOut:
    return await ExportService(session=session, settings=settings).create(
        paper_id=paper_id, version_id=version_id, payload=payload, scope=scope
    )


@router.get("/exports/{export_id}", response_model=ExportJobOut)
async def get_export(
    export_id: uuid.UUID = Path(...),
    scope: RequestScope = Depends(get_scope),
    session: AsyncSession = Depends(get_db),
    settings: Settings = Depends(get_settings),
) -> ExportJobOut:
    return await ExportService(session=session, settings=settings).get(
        export_id=export_id, scope=scope
    )


@router.get(
    "/{paper_id}/versions/{version_id}/exports", response_model=list[ExportJobOut]
)
async def list_exports(
    paper_id: uuid.UUID = Path(...),
    version_id: uuid.UUID = Path(...),
    artifact_type: str | None = Query(
        default=None,
        alias="artifactType",
        pattern="^(paper|answer_key)$",
    ),
    scope: RequestScope = Depends(get_scope),
    session: AsyncSession = Depends(get_db),
    settings: Settings = Depends(get_settings),
) -> list[ExportJobOut]:
    return await ExportService(session=session, settings=settings).list(
        paper_id=paper_id,
        version_id=version_id,
        artifact_type=artifact_type,
        scope=scope,
    )


@router.post("/exports/{export_id}/download", response_model=SignedDownloadOut)
async def create_signed_download(
    export_id: uuid.UUID = Path(...),
    scope: RequestScope = Depends(get_scope),
    session: AsyncSession = Depends(get_db),
    settings: Settings = Depends(get_settings),
) -> SignedDownloadOut:
    return await ExportService(session=session, settings=settings).download(
        export_id=export_id, scope=scope
    )


__all__ = ["router"]
