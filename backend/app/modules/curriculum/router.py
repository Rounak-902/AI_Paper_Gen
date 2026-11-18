"""HTTP routes for the shared curriculum catalog (P2-T1)."""

from __future__ import annotations

import uuid
from typing import Optional

from fastapi import APIRouter, Depends, Path, Query, Request, status
from sqlalchemy.ext.asyncio import AsyncSession

from app.common.deps import OrgContext, get_db, get_org_context, require_roles
from app.common.envelope import Pagination
from app.common.routing import Enveloped, EnvelopeRoute
from app.modules.curriculum import service
from app.modules.curriculum import import_service as curriculum_import
from app.modules.curriculum.import_schemas import (
    CurriculumImportApplyIn,
    CurriculumImportDocumentIn,
    CurriculumImportPlanOut,
    CurriculumImportResultOut,
    CurriculumImportValidationOut,
)
from app.modules.curriculum.schemas import (
    BoardCreateIn,
    BoardDeleteIn,
    BoardOut,
    BoardUpdateIn,
    ChapterCreateIn,
    ChapterDeleteIn,
    ChapterLineageOut,
    ChapterOut,
    ChapterUpdateIn,
    ClassCreateIn,
    ClassDeleteIn,
    ClassOut,
    ClassUpdateIn,
    CurriculumVersionCreateIn,
    CurriculumVersionDeleteIn,
    CurriculumVersionOut,
    CurriculumVersionStatus,
    CurriculumVersionUpdateIn,
    DeletedOut,
    OutcomeCreateIn,
    OutcomeDeleteIn,
    OutcomeOut,
    OutcomeUpdateIn,
    SubjectCreateIn,
    SubjectDeleteIn,
    SubjectOut,
    SubjectUpdateIn,
)


class PageParams:
    """Bounded one-based pagination with a one-row has-next probe."""

    def __init__(
        self,
        page: int = Query(default=1, ge=1),
        page_size: int = Query(default=20, ge=1, le=100, alias="pageSize"),
    ) -> None:
        self.page = page
        self.page_size = page_size

    @property
    def offset(self) -> int:
        return (self.page - 1) * self.page_size

    @property
    def probe_limit(self) -> int:
        return self.page_size + 1


def _paginate(items: list, params: PageParams) -> Enveloped:
    has_next = len(items) > params.page_size
    page_items = items[: params.page_size]
    return Enveloped(
        page_items,
        pagination=Pagination(
            page=params.page,
            page_size=params.page_size,
            # The protected repository base has no count operation.  As in the P1
            # tenancy routes, this is page count while hasNext remains exact.
            total=len(page_items),
            has_next=has_next,
        ),
    )


def _client_ip(request: Request) -> Optional[str]:
    return request.client.host if request.client else None


router = APIRouter(
    prefix="/api/v1",
    tags=["Curriculum"],
    route_class=EnvelopeRoute,
    # Reads are global but never anonymous: every request must carry a verified
    # active organization.  The context is authorization/audit context, not a row
    # filter, because these tables are intentionally RLS-exempt reference data.
    dependencies=[Depends(get_org_context)],
)


# ---------------------------------------------------------------------------
# Aggregate curriculum import workflow
# ---------------------------------------------------------------------------
@router.post("/curriculum-imports/validate")
async def validate_curriculum_import(
    payload: CurriculumImportDocumentIn,
    ctx: OrgContext = Depends(require_roles("super_admin")),
) -> CurriculumImportValidationOut:
    """Validate one nested manifest without reading or mutating catalog state."""

    del ctx  # authorization is the only context needed for local validation
    return curriculum_import.validate_document(payload)


@router.post("/curriculum-imports/preview")
async def preview_curriculum_import(
    payload: CurriculumImportDocumentIn,
    ctx: OrgContext = Depends(require_roles("super_admin")),
    session: AsyncSession = Depends(get_db),
) -> CurriculumImportPlanOut:
    """Build a server-authoritative, zero-write plan against the live catalog."""

    return await curriculum_import.preview_import(
        session=session,
        ctx=ctx,
        document=payload,
    )


@router.post("/curriculum-imports", status_code=status.HTTP_201_CREATED)
async def apply_curriculum_import(
    payload: CurriculumImportApplyIn,
    request: Request,
    ctx: OrgContext = Depends(require_roles("super_admin")),
    session: AsyncSession = Depends(get_db),
) -> CurriculumImportResultOut:
    """Re-plan under the board lock and atomically apply the reviewed manifest."""

    return await curriculum_import.apply_import(
        session=session,
        ctx=ctx,
        payload=payload,
        ip=_client_ip(request),
    )


# ---------------------------------------------------------------------------
# Boards
# ---------------------------------------------------------------------------
@router.post("/boards", status_code=status.HTTP_201_CREATED)
async def create_board(
    payload: BoardCreateIn,
    request: Request,
    ctx: OrgContext = Depends(require_roles("super_admin")),
    session: AsyncSession = Depends(get_db),
) -> BoardOut:
    return await service.create_board(
        session=session, ctx=ctx, payload=payload, ip=_client_ip(request)
    )


@router.get("/boards")
async def list_boards(
    ctx: OrgContext = Depends(get_org_context),
    session: AsyncSession = Depends(get_db),
    page: PageParams = Depends(),
) -> list[BoardOut]:
    items, _ = await service.list_boards(
        session=session,
        ctx=ctx,
        limit=page.probe_limit,
        offset=page.offset,
    )
    return _paginate(items, page)


@router.get("/boards/{board_id}")
async def get_board(
    board_id: uuid.UUID = Path(...),
    ctx: OrgContext = Depends(get_org_context),
    session: AsyncSession = Depends(get_db),
) -> BoardOut:
    return await service.get_board(session=session, ctx=ctx, board_id=board_id)


@router.patch("/boards/{board_id}")
async def update_board(
    payload: BoardUpdateIn,
    request: Request,
    board_id: uuid.UUID = Path(...),
    ctx: OrgContext = Depends(require_roles("super_admin")),
    session: AsyncSession = Depends(get_db),
) -> BoardOut:
    return await service.update_board(
        session=session,
        ctx=ctx,
        board_id=board_id,
        payload=payload,
        ip=_client_ip(request),
    )


@router.delete("/boards/{board_id}")
async def delete_board(
    payload: BoardDeleteIn,
    request: Request,
    board_id: uuid.UUID = Path(...),
    ctx: OrgContext = Depends(require_roles("super_admin")),
    session: AsyncSession = Depends(get_db),
) -> DeletedOut:
    await service.delete_board(
        session=session,
        ctx=ctx,
        board_id=board_id,
        row_version=payload.row_version,
        ip=_client_ip(request),
    )
    return DeletedOut(id=board_id)


# ---------------------------------------------------------------------------
# Classes
# ---------------------------------------------------------------------------
@router.post("/classes", status_code=status.HTTP_201_CREATED)
async def create_class(
    payload: ClassCreateIn,
    request: Request,
    ctx: OrgContext = Depends(require_roles("super_admin")),
    session: AsyncSession = Depends(get_db),
) -> ClassOut:
    return await service.create_class(
        session=session, ctx=ctx, payload=payload, ip=_client_ip(request)
    )


@router.get("/classes")
async def list_classes(
    board_id: uuid.UUID = Query(..., alias="boardId"),
    ctx: OrgContext = Depends(get_org_context),
    session: AsyncSession = Depends(get_db),
    page: PageParams = Depends(),
) -> list[ClassOut]:
    items, _ = await service.list_classes(
        session=session,
        ctx=ctx,
        board_id=board_id,
        limit=page.probe_limit,
        offset=page.offset,
    )
    return _paginate(items, page)


@router.get("/classes/{class_id}")
async def get_class(
    class_id: uuid.UUID = Path(...),
    ctx: OrgContext = Depends(get_org_context),
    session: AsyncSession = Depends(get_db),
) -> ClassOut:
    return await service.get_class(session=session, ctx=ctx, class_id=class_id)


@router.patch("/classes/{class_id}")
async def update_class(
    payload: ClassUpdateIn,
    request: Request,
    class_id: uuid.UUID = Path(...),
    ctx: OrgContext = Depends(require_roles("super_admin")),
    session: AsyncSession = Depends(get_db),
) -> ClassOut:
    return await service.update_class(
        session=session,
        ctx=ctx,
        class_id=class_id,
        payload=payload,
        ip=_client_ip(request),
    )


@router.delete("/classes/{class_id}")
async def delete_class(
    payload: ClassDeleteIn,
    request: Request,
    class_id: uuid.UUID = Path(...),
    ctx: OrgContext = Depends(require_roles("super_admin")),
    session: AsyncSession = Depends(get_db),
) -> DeletedOut:
    await service.delete_class(
        session=session,
        ctx=ctx,
        class_id=class_id,
        row_version=payload.row_version,
        ip=_client_ip(request),
    )
    return DeletedOut(id=class_id)


# ---------------------------------------------------------------------------
# Subjects
# ---------------------------------------------------------------------------
@router.post("/subjects", status_code=status.HTTP_201_CREATED)
async def create_subject(
    payload: SubjectCreateIn,
    request: Request,
    ctx: OrgContext = Depends(require_roles("super_admin")),
    session: AsyncSession = Depends(get_db),
) -> SubjectOut:
    return await service.create_subject(
        session=session, ctx=ctx, payload=payload, ip=_client_ip(request)
    )


@router.get("/subjects")
async def list_subjects(
    class_id: uuid.UUID = Query(..., alias="classId"),
    curriculum_version_id: Optional[uuid.UUID] = Query(
        default=None, alias="curriculumVersionId"
    ),
    ctx: OrgContext = Depends(get_org_context),
    session: AsyncSession = Depends(get_db),
    page: PageParams = Depends(),
) -> list[SubjectOut]:
    items, _ = await service.list_subjects(
        session=session,
        ctx=ctx,
        class_id=class_id,
        curriculum_version_id=curriculum_version_id,
        limit=page.probe_limit,
        offset=page.offset,
    )
    return _paginate(items, page)


@router.get("/subjects/{subject_id}")
async def get_subject(
    subject_id: uuid.UUID = Path(...),
    ctx: OrgContext = Depends(get_org_context),
    session: AsyncSession = Depends(get_db),
) -> SubjectOut:
    return await service.get_subject(
        session=session, ctx=ctx, subject_id=subject_id
    )


@router.patch("/subjects/{subject_id}")
async def update_subject(
    payload: SubjectUpdateIn,
    request: Request,
    subject_id: uuid.UUID = Path(...),
    ctx: OrgContext = Depends(require_roles("super_admin")),
    session: AsyncSession = Depends(get_db),
) -> SubjectOut:
    return await service.update_subject(
        session=session,
        ctx=ctx,
        subject_id=subject_id,
        payload=payload,
        ip=_client_ip(request),
    )


@router.delete("/subjects/{subject_id}")
async def delete_subject(
    payload: SubjectDeleteIn,
    request: Request,
    subject_id: uuid.UUID = Path(...),
    ctx: OrgContext = Depends(require_roles("super_admin")),
    session: AsyncSession = Depends(get_db),
) -> DeletedOut:
    await service.delete_subject(
        session=session,
        ctx=ctx,
        subject_id=subject_id,
        row_version=payload.row_version,
        ip=_client_ip(request),
    )
    return DeletedOut(id=subject_id)


# ---------------------------------------------------------------------------
# Chapters and lineage
# ---------------------------------------------------------------------------
@router.post("/chapters", status_code=status.HTTP_201_CREATED)
async def create_chapter(
    payload: ChapterCreateIn,
    request: Request,
    ctx: OrgContext = Depends(require_roles("super_admin")),
    session: AsyncSession = Depends(get_db),
) -> ChapterOut:
    return await service.create_chapter(
        session=session, ctx=ctx, payload=payload, ip=_client_ip(request)
    )


@router.get("/chapters")
async def list_chapters(
    subject_id: uuid.UUID = Query(..., alias="subjectId"),
    curriculum_version_id: Optional[uuid.UUID] = Query(
        default=None, alias="curriculumVersionId"
    ),
    ctx: OrgContext = Depends(get_org_context),
    session: AsyncSession = Depends(get_db),
    page: PageParams = Depends(),
) -> list[ChapterOut]:
    items, _ = await service.list_chapters(
        session=session,
        ctx=ctx,
        subject_id=subject_id,
        curriculum_version_id=curriculum_version_id,
        limit=page.probe_limit,
        offset=page.offset,
    )
    return _paginate(items, page)


@router.get("/chapters/{chapter_id}")
async def get_chapter(
    chapter_id: uuid.UUID = Path(...),
    ctx: OrgContext = Depends(get_org_context),
    session: AsyncSession = Depends(get_db),
) -> ChapterOut:
    return await service.get_chapter(
        session=session, ctx=ctx, chapter_id=chapter_id
    )


@router.get("/chapters/{chapter_id}/lineage")
async def get_chapter_lineage(
    chapter_id: uuid.UUID = Path(...),
    ctx: OrgContext = Depends(get_org_context),
    session: AsyncSession = Depends(get_db),
) -> ChapterLineageOut:
    return await service.get_chapter_lineage(
        session=session, ctx=ctx, chapter_id=chapter_id
    )


@router.patch("/chapters/{chapter_id}")
async def update_chapter(
    payload: ChapterUpdateIn,
    request: Request,
    chapter_id: uuid.UUID = Path(...),
    ctx: OrgContext = Depends(require_roles("super_admin")),
    session: AsyncSession = Depends(get_db),
) -> ChapterOut:
    return await service.update_chapter(
        session=session,
        ctx=ctx,
        chapter_id=chapter_id,
        payload=payload,
        ip=_client_ip(request),
    )


@router.delete("/chapters/{chapter_id}")
async def delete_chapter(
    payload: ChapterDeleteIn,
    request: Request,
    chapter_id: uuid.UUID = Path(...),
    ctx: OrgContext = Depends(require_roles("super_admin")),
    session: AsyncSession = Depends(get_db),
) -> DeletedOut:
    await service.delete_chapter(
        session=session,
        ctx=ctx,
        chapter_id=chapter_id,
        row_version=payload.row_version,
        ip=_client_ip(request),
    )
    return DeletedOut(id=chapter_id)


# ---------------------------------------------------------------------------
# Outcomes
# ---------------------------------------------------------------------------
@router.post("/outcomes", status_code=status.HTTP_201_CREATED)
async def create_outcome(
    payload: OutcomeCreateIn,
    request: Request,
    ctx: OrgContext = Depends(require_roles("super_admin")),
    session: AsyncSession = Depends(get_db),
) -> OutcomeOut:
    return await service.create_outcome(
        session=session, ctx=ctx, payload=payload, ip=_client_ip(request)
    )


@router.get("/outcomes")
async def list_outcomes(
    chapter_id: uuid.UUID = Query(..., alias="chapterId"),
    ctx: OrgContext = Depends(get_org_context),
    session: AsyncSession = Depends(get_db),
    page: PageParams = Depends(),
) -> list[OutcomeOut]:
    items, _ = await service.list_outcomes(
        session=session,
        ctx=ctx,
        chapter_id=chapter_id,
        limit=page.probe_limit,
        offset=page.offset,
    )
    return _paginate(items, page)


@router.get("/outcomes/{outcome_id}")
async def get_outcome(
    outcome_id: uuid.UUID = Path(...),
    ctx: OrgContext = Depends(get_org_context),
    session: AsyncSession = Depends(get_db),
) -> OutcomeOut:
    return await service.get_outcome(
        session=session, ctx=ctx, outcome_id=outcome_id
    )


@router.patch("/outcomes/{outcome_id}")
async def update_outcome(
    payload: OutcomeUpdateIn,
    request: Request,
    outcome_id: uuid.UUID = Path(...),
    ctx: OrgContext = Depends(require_roles("super_admin")),
    session: AsyncSession = Depends(get_db),
) -> OutcomeOut:
    return await service.update_outcome(
        session=session,
        ctx=ctx,
        outcome_id=outcome_id,
        payload=payload,
        ip=_client_ip(request),
    )


@router.delete("/outcomes/{outcome_id}")
async def delete_outcome(
    payload: OutcomeDeleteIn,
    request: Request,
    outcome_id: uuid.UUID = Path(...),
    ctx: OrgContext = Depends(require_roles("super_admin")),
    session: AsyncSession = Depends(get_db),
) -> DeletedOut:
    await service.delete_outcome(
        session=session,
        ctx=ctx,
        outcome_id=outcome_id,
        row_version=payload.row_version,
        ip=_client_ip(request),
    )
    return DeletedOut(id=outcome_id)


# ---------------------------------------------------------------------------
# Curriculum versions
# ---------------------------------------------------------------------------
@router.post("/curriculum-versions", status_code=status.HTTP_201_CREATED)
async def create_curriculum_version(
    payload: CurriculumVersionCreateIn,
    request: Request,
    ctx: OrgContext = Depends(require_roles("super_admin")),
    session: AsyncSession = Depends(get_db),
) -> CurriculumVersionOut:
    return await service.create_curriculum_version(
        session=session, ctx=ctx, payload=payload, ip=_client_ip(request)
    )


@router.get("/curriculum-versions")
async def list_curriculum_versions(
    board_id: uuid.UUID = Query(..., alias="boardId"),
    version_status: Optional[CurriculumVersionStatus] = Query(
        default=None, alias="status"
    ),
    ctx: OrgContext = Depends(get_org_context),
    session: AsyncSession = Depends(get_db),
    page: PageParams = Depends(),
) -> list[CurriculumVersionOut]:
    items, _ = await service.list_curriculum_versions(
        session=session,
        ctx=ctx,
        board_id=board_id,
        status=version_status,
        limit=page.probe_limit,
        offset=page.offset,
    )
    return _paginate(items, page)


@router.get("/curriculum-versions/{curriculum_version_id}")
async def get_curriculum_version(
    curriculum_version_id: uuid.UUID = Path(...),
    ctx: OrgContext = Depends(get_org_context),
    session: AsyncSession = Depends(get_db),
) -> CurriculumVersionOut:
    return await service.get_curriculum_version(
        session=session,
        ctx=ctx,
        curriculum_version_id=curriculum_version_id,
    )


@router.patch("/curriculum-versions/{curriculum_version_id}")
async def update_curriculum_version(
    payload: CurriculumVersionUpdateIn,
    request: Request,
    curriculum_version_id: uuid.UUID = Path(...),
    ctx: OrgContext = Depends(require_roles("super_admin")),
    session: AsyncSession = Depends(get_db),
) -> CurriculumVersionOut:
    return await service.update_curriculum_version(
        session=session,
        ctx=ctx,
        curriculum_version_id=curriculum_version_id,
        payload=payload,
        ip=_client_ip(request),
    )


@router.delete("/curriculum-versions/{curriculum_version_id}")
async def delete_curriculum_version(
    payload: CurriculumVersionDeleteIn,
    request: Request,
    curriculum_version_id: uuid.UUID = Path(...),
    ctx: OrgContext = Depends(require_roles("super_admin")),
    session: AsyncSession = Depends(get_db),
) -> DeletedOut:
    await service.delete_curriculum_version(
        session=session,
        ctx=ctx,
        curriculum_version_id=curriculum_version_id,
        row_version=payload.row_version,
        ip=_client_ip(request),
    )
    return DeletedOut(id=curriculum_version_id)


__all__ = ["router"]
