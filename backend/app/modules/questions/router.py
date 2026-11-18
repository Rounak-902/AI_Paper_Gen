"""Questions router (P2-T3 through P2-T6).

Routes:
    GET    /api/v1/orgs/{org_id}/questions                          list / search
    POST   /api/v1/orgs/{org_id}/questions                          create
    GET    /api/v1/orgs/{org_id}/questions/{id}                     get
    PATCH  /api/v1/orgs/{org_id}/questions/{id}                     update (draft in-place; approved forks)
    POST   /api/v1/orgs/{org_id}/questions/{id}/approve             approve (admin only)
    DELETE /api/v1/orgs/{org_id}/questions/{id}                     soft-delete (admin only)
    GET    /api/v1/orgs/{org_id}/questions/{id}/lineage             full version history

    -- P2-T5 import pipeline --
    GET    /api/v1/orgs/{org_id}/questions/import/template.csv      download CSV template
    POST   /api/v1/orgs/{org_id}/questions/import/preview           parse file, return headers + auto-mapping
    POST   /api/v1/orgs/{org_id}/questions/import                   enqueue a durable import job
    GET    /api/v1/orgs/{org_id}/questions/import-jobs/{id}         poll import progress/results
    GET    /api/v1/orgs/{org_id}/questions/import/report/{token}    download full error-report CSV
    POST   /api/v1/orgs/{org_id}/questions/bulk                     bulk approve/reject/archive

Guard chain pattern
-------------------
* All routes share ``Depends(get_scope)`` at the router level. ``get_scope``
  depends on ``get_org_context`` which depends on ``get_current_user``, so
  the full 401 → 403 chain fires on every route.
* Read routes (GET) depend only on ``get_scope`` and ``get_db``.
* Write routes also depend on ``get_org_context`` via ``require_roles(...)``
  as a role gate. FastAPI's dependency caching means ``get_org_context`` runs
  only once per request even when both ``get_scope`` and ``require_roles``
  declare it as a dependency — the scope resolution database query also runs
  once. Only the 403 check fires additionally.
* Question creation and import both opt in to ``get_global_write_db``.
  Migration 0031 needs the verified actor GUC for global-scope INSERTs;
  every other handler retains the tenant-only ``get_db``.
* Bulk operations use ``get_global_write_db`` (not ``get_db``).
  A bulk batch may contain global-scope questions, which require ``app.user_id``
  to be set for the UPDATE WITH CHECK policy.  ``get_global_write_db`` is a
  strict superset of ``get_db`` (it also sets ``app.org_id``), so org-scoped
  questions in the same batch are unaffected.  The application-layer super_admin
  gate for global questions lives in ``bulk_service.py``, not here.
* The ``scope: RequestScope`` from ``get_scope`` carries ``subject_ids``
  (populated for teachers from program assignments by P2-T2). All service
  calls receive it, completing the P2-T2 carry-forward.
"""

from __future__ import annotations

import json
import uuid
from typing import Annotated, Optional

from fastapi import APIRouter, BackgroundTasks, Depends, File, Form, Path, Query, Request, Response, UploadFile, status
from sqlalchemy.ext.asyncio import AsyncSession

from app.common.deps import (
    OrgContext,
    RequestScope,
    get_db,
    get_global_write_db,
    get_org_context,
    get_scope,
    require_roles,
)
from app.common.envelope import Pagination
from app.common.errors import NotFoundError
from app.common.routing import Enveloped, EnvelopeRoute
from app.core.config import get_settings
from app.modules.questions import schemas, service
from app.modules.questions import import_service, bulk_service
from app.modules.questions.import_schemas import (
    BulkActionIn,
    BulkActionResultOut,
    ColumnMappingEntry,
    ImportJobOut,
    ImportPreviewOut,
    ImportRowResultOut,
)
from app.modules.tenancy.router import client_ip

_ALL_AUTHED_ROLES = ("teacher", "coaching_admin", "super_admin")
_ADMIN_ROLES = ("coaching_admin", "super_admin")


def _dispatch_committed_outbox() -> None:
    """Publish committed work immediately; Celery Beat remains the recovery path."""

    from app.workers.tasks import dispatch_outbox

    dispatch_outbox()


router = APIRouter(
    prefix="/api/v1/orgs/{org_id}/questions",
    tags=["Questions"],
    route_class=EnvelopeRoute,
    # get_scope runs on every route (covers auth + subject_ids resolution)
    dependencies=[Depends(get_scope)],
)


@router.post("", status_code=status.HTTP_201_CREATED)
async def create_question(
    payload: schemas.QuestionCreateIn,
    request: Request,
    org_id: uuid.UUID = Path(...),
    scope: RequestScope = Depends(get_scope),
    _role: OrgContext = Depends(require_roles(*_ALL_AUTHED_ROLES)),
    session: AsyncSession = Depends(get_global_write_db),
) -> schemas.QuestionCreateOut:
    """Create a new question (any of 13 types) with P2-T4 deduplication.

    Sets scope='org' by default. Only super_admin may set scope='global'.
    Exact content is rejected; near matches are returned as warnings.
    """
    return await service.create_question(
        session=session,
        scope=scope,
        org_id=org_id,
        payload=payload,
        ip=client_ip(request),
    )


@router.get("")
async def list_questions(
    params: Annotated[schemas.QuestionSearchParams, Query()],
    org_id: uuid.UUID = Path(...),
    scope: RequestScope = Depends(get_scope),
    session: AsyncSession = Depends(get_db),
) -> list[schemas.QuestionOut]:
    """List questions visible to the caller.

    Teachers: filtered to assigned subjects only (P2-T2 subject scope).
    Admins: see all org questions + global questions.
    Normal searches return approved, current rows; ``status`` and
    ``current_only=false`` are explicit opt-ins to other visibility slices.
    """
    items, total = await service.list_questions(
        session=session,
        scope=scope,
        org_id=org_id,
        params=params,
    )
    return Enveloped(
        items,
        pagination=Pagination(
            page=params.page,
            page_size=params.page_size,
            total=total,
            has_next=params.offset + len(items) < total,
        ),
    )


@router.get("/{question_id}/lineage")
async def get_lineage(
    org_id: uuid.UUID = Path(...),
    question_id: uuid.UUID = Path(...),
    scope: RequestScope = Depends(get_scope),
    session: AsyncSession = Depends(get_db),
) -> list[schemas.QuestionOut]:
    """Return all versions in a question's lineage, oldest first."""
    return await service.get_lineage(
        session=session,
        scope=scope,
        org_id=org_id,
        question_id=question_id,
    )


@router.get("/{question_id}")
async def get_question(
    org_id: uuid.UUID = Path(...),
    question_id: uuid.UUID = Path(...),
    scope: RequestScope = Depends(get_scope),
    session: AsyncSession = Depends(get_db),
) -> schemas.QuestionOut:
    """Get a single question by ID, respecting teacher scope narrowing."""
    return await service.get_question(
        session=session,
        scope=scope,
        org_id=org_id,
        question_id=question_id,
    )


@router.patch("/{question_id}")
async def update_question(
    payload: schemas.QuestionUpdateIn,
    request: Request,
    org_id: uuid.UUID = Path(...),
    question_id: uuid.UUID = Path(...),
    scope: RequestScope = Depends(get_scope),
    _role: OrgContext = Depends(require_roles(*_ALL_AUTHED_ROLES)),
    session: AsyncSession = Depends(get_db),
) -> schemas.QuestionOut:
    """Update a question.

    Draft: updates in place (optimistic locking).
    Approved: forks a new version (BR-8). Old row gets is_current=False.
    """
    return await service.update_question(
        session=session,
        scope=scope,
        org_id=org_id,
        question_id=question_id,
        payload=payload,
        ip=client_ip(request),
    )


@router.post("/{question_id}/approve", status_code=status.HTTP_200_OK)
async def approve_question(
    payload: schemas.QuestionApproveIn,
    request: Request,
    org_id: uuid.UUID = Path(...),
    question_id: uuid.UUID = Path(...),
    scope: RequestScope = Depends(get_scope),
    _role: OrgContext = Depends(require_roles(*_ADMIN_ROLES)),
    session: AsyncSession = Depends(get_db),
) -> schemas.QuestionOut:
    """Approve a draft question (coaching_admin / super_admin only)."""
    return await service.approve_question(
        session=session,
        scope=scope,
        org_id=org_id,
        question_id=question_id,
        payload=payload,
        ip=client_ip(request),
    )


@router.delete("/{question_id}", status_code=status.HTTP_204_NO_CONTENT)
async def delete_question(
    request: Request,
    org_id: uuid.UUID = Path(...),
    question_id: uuid.UUID = Path(...),
    row_version: int = Query(..., description="Current row_version for optimistic locking"),
    scope: RequestScope = Depends(get_scope),
    _role: OrgContext = Depends(require_roles(*_ADMIN_ROLES)),
    session: AsyncSession = Depends(get_db),
) -> None:
    """Soft-delete a question (coaching_admin / super_admin only)."""
    await service.delete_question(
        session=session,
        scope=scope,
        org_id=org_id,
        question_id=question_id,
        row_version=row_version,
        ip=client_ip(request),
    )


# ===========================================================================
# P2-T5 — Import pipeline
# ===========================================================================

@router.get("/import/template.csv", include_in_schema=True)
async def download_import_template(
    org_id: uuid.UUID = Path(...),
    scope: RequestScope = Depends(get_scope),
    _role: OrgContext = Depends(require_roles(*_ADMIN_ROLES)),
) -> Response:
    """Download a filled-example CSV template for bulk question import.

    The template uses the canonical column names expected by the import
    endpoint.  Fill it in and upload via POST /questions/import.
    """
    csv_bytes = import_service.build_template_csv()
    return Response(
        content=csv_bytes,
        media_type="text/csv",
        headers={"Content-Disposition": 'attachment; filename="question_import_template.csv"'},
    )


@router.post("/import/preview", status_code=status.HTTP_200_OK)
async def preview_import(
    request: Request,
    org_id: uuid.UUID = Path(...),
    scope: RequestScope = Depends(get_scope),
    _role: OrgContext = Depends(require_roles(*_ADMIN_ROLES)),
    file: UploadFile = File(..., description="CSV, XLSX, or JSON file to preview"),
) -> ImportPreviewOut:
    """Parse an import file and return headers + first 5 rows + recommended column mapping.

    Use the returned ``recommended_mapping`` as a starting point.  Correct any
    mis-mapped columns, then POST to /questions/import with the adjusted mapping.
    """
    raw_bytes = await file.read()
    return await import_service.preview_import_file(
        scope=scope,
        org_id=org_id,
        raw_bytes=raw_bytes,
        content_type=file.content_type or "",
        filename=file.filename or "",
    )


@router.post(
    "/import",
    response_model=ImportJobOut,
    status_code=status.HTTP_202_ACCEPTED,
)
async def run_import(
    request: Request,
    background_tasks: BackgroundTasks,
    org_id: uuid.UUID = Path(...),
    scope: RequestScope = Depends(get_scope),
    _role: OrgContext = Depends(require_roles(*_ADMIN_ROLES)),
    # get_global_write_db: import rows may be global-scoped (super_admin only),
    # same rationale as the create_question endpoint.
    session: AsyncSession = Depends(get_global_write_db, scope="function"),
    file: UploadFile = File(..., description="CSV, XLSX, or JSON file"),
    mapping: str = Form(
        default="[]",
        description=(
            "JSON array of {source_column, target_field} objects. "
            "Use GET /questions/import/template.csv for the canonical target field names. "
            "Pass [] to use the file's own column names (must match target field names exactly)."
        ),
    ),
    submission_id: Optional[uuid.UUID] = Form(
        default=None,
        description=(
            "Client-generated UUID used to recover or safely retry a submission "
            "when its HTTP response is lost."
        ),
    ),
) -> ImportJobOut:
    """Queue a question import from a CSV, XLSX, or JSON file.

    A durable worker job is returned immediately. Each row is independently
    validated and processed through the question-creation
    pipeline (including P2-T4 dedup enforcement).  Good rows are committed even if
    other rows fail (partial success).  A downloadable error report is generated
    for any rows that errored, were exact duplicates, or had near-duplicate warnings.

    Column mapping
    --------------
    Submit ``mapping`` as a JSON array::

        [{"source_column": "Question", "target_field": "stem"}, ...]

    If ``mapping`` is ``[]`` (the default), column names in the file must exactly
    match the canonical target field names (as in the downloadable template).

    Global scope
    ------------
    Rows with ``scope=global`` require ``super_admin`` role.  Non-admins get a
    per-row error for those rows; the rest of the batch is unaffected.
    """
    raw_bytes = await file.read()

    try:
        mapping_entries = [ColumnMappingEntry(**e) for e in json.loads(mapping)]
    except Exception as exc:
        from app.common.errors import ValidationError as AppValidationError
        raise AppValidationError(f"Invalid mapping JSON: {exc}") from exc

    # If no explicit mapping is provided, build an identity mapping from the
    # file's own column names (source == target for each detected column).
    if not mapping_entries:
        raw_rows = import_service.parse_import_file(
            raw_bytes, file.content_type or "", file.filename or ""
        )
        cols = import_service.get_source_columns(raw_rows)
        mapping_entries = [
            ColumnMappingEntry(source_column=c, target_field=c)
            for c in cols
            if c in import_service.TEMPLATE_COLUMNS
        ]

    job = await import_service.create_import_job(
        session=session,
        scope=scope,
        org_id=org_id,
        raw_bytes=raw_bytes,
        content_type=file.content_type or "",
        filename=file.filename or "",
        column_mapping=mapping_entries,
        ip=client_ip(request),
        job_id=submission_id,
    )
    background_tasks.add_task(_dispatch_committed_outbox)
    return job


@router.get("/import-jobs/{import_id}", response_model=ImportJobOut)
async def get_import_job(
    background_tasks: BackgroundTasks,
    import_id: uuid.UUID = Path(...),
    org_id: uuid.UUID = Path(...),
    scope: RequestScope = Depends(get_scope),
    _role: OrgContext = Depends(require_roles(*_ADMIN_ROLES)),
    session: AsyncSession = Depends(get_db, scope="function"),
) -> ImportJobOut:
    """Return durable progress and the final row-level import result."""

    service.assert_path_org(scope, org_id)
    job = await import_service.get_import_job(
        session=session, org_id=org_id, import_id=import_id
    )
    recovered = await import_service.recover_stalled_import_job(
        session=session,
        org_id=org_id,
        import_id=import_id,
        stale_after_seconds=get_settings().QUESTION_IMPORT_STALE_SECONDS,
    )
    if recovered:
        background_tasks.add_task(_dispatch_committed_outbox)
    return import_service.import_job_resource(job)


@router.get("/import/report/{token}", include_in_schema=True)
async def download_import_report(
    org_id: uuid.UUID = Path(...),
    token: str = Path(..., description="Token from a previous import response"),
    scope: RequestScope = Depends(get_scope),
    _role: OrgContext = Depends(require_roles(*_ADMIN_ROLES)),
    session: AsyncSession = Depends(get_db),
) -> Response:
    """Download the full row-level report for a completed durable import.

    Legacy one-hour in-process report tokens remain readable for imports completed
    before the background-job migration.
    """
    csv_bytes = None
    try:
        import_id = uuid.UUID(token)
    except ValueError:
        import_id = None
    if import_id is not None:
        try:
            job = await import_service.get_import_job(
                session=session, org_id=org_id, import_id=import_id
            )
        except NotFoundError:
            job = None
        if job is not None and job.status == "completed" and job.error_report_available:
            rows = [ImportRowResultOut.model_validate(row) for row in job.row_results]
            csv_bytes = import_service.generate_error_report_csv(rows)
    if csv_bytes is None:
        csv_bytes = import_service.get_cached_report(token)
    if csv_bytes is None:
        raise NotFoundError(
            "Import report not found or expired. Reports expire after 1 hour."
        )
    return Response(
        content=csv_bytes,
        media_type="text/csv",
        headers={"Content-Disposition": 'attachment; filename="import_error_report.csv"'},
    )


# ===========================================================================
# P2-T5 — Bulk approve / reject / archive
# ===========================================================================

@router.post("/bulk", status_code=status.HTTP_200_OK)
async def bulk_question_action(
    payload: BulkActionIn,
    request: Request,
    org_id: uuid.UUID = Path(...),
    scope: RequestScope = Depends(get_scope),
    _role: OrgContext = Depends(require_roles(*_ADMIN_ROLES)),
    # get_global_write_db — not get_db — because a bulk batch may contain
    # global-scope questions (org_id IS NULL), which require app.user_id to be
    # set for migration 0031's UPDATE WITH CHECK policy.  get_global_write_db
    # also sets app.org_id, so org-scoped questions in the same batch work
    # identically to get_db.  The super_admin gate for global questions is
    # enforced in bulk_service._process_one_bulk_row, not by the DB alone.
    session: AsyncSession = Depends(get_global_write_db),
) -> BulkActionResultOut:
    """Bulk approve, reject, or archive a list of questions.

    Each question is processed independently (partial success).  Provide the
    current ``row_version`` for each question to satisfy optimistic locking.

    Mixed scope batches
    -------------------
    Org-scoped and global questions may appear in the same batch.

    * Org-scoped questions: any ``coaching_admin`` or ``super_admin`` may act.
    * Global questions (``scope='global'``, ``org_id IS NULL``): only
      ``super_admin`` may act.  A ``coaching_admin`` receives a clear per-row
      error (``GLOBAL_QUESTION_BULK_ACTION_FORBIDDEN``) for each global
      question in the batch; org-scoped questions in the same call are
      unaffected.
    """
    return await bulk_service.bulk_action(
        session=session,
        scope=scope,
        org_id=org_id,
        payload=payload,
        ip=client_ip(request),
    )
