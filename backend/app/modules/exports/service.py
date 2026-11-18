"""Export request orchestration, polling resources, and worker execution."""

from __future__ import annotations

import hashlib
import uuid
from datetime import datetime, timezone
from decimal import Decimal

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.common.deps import RequestScope
from app.common.errors import AuthorizationError, NotFoundError, ValidationError
from app.core.config import Settings
from app.models.billing import Usage
from app.models.export import ExportArtifact
from app.models.paper import PaperVersion
from app.modules.exports.contracts import ExportRenderRequest, RendererRegistry
from app.modules.exports.docx_renderer import DocxRenderer
from app.modules.exports.pdf_renderer import PlaywrightPdfRenderer
from app.modules.exports.repository import ExportRepository
from app.modules.exports.schemas import ExportCreateIn, ExportJobOut, SignedDownloadOut
from app.modules.exports.storage import S3ArtifactStore
from app.workers.outbox import add_outbox_task


def export_object_key(job: ExportArtifact) -> str:
    """Tenant namespace from the established storage contract, with immutable IDs."""

    return (
        f"{job.org_id}/exports/{job.paper_version_id}/"
        f"{job.artifact_type}/{job.id}.{job.format}"
    )


def _resource(row: ExportArtifact) -> ExportJobOut:
    return ExportJobOut.model_validate(row, from_attributes=True)


class ExportService:
    def __init__(self, *, session: AsyncSession, settings: Settings) -> None:
        self._session = session
        self._settings = settings

    async def create(
        self,
        *,
        paper_id: uuid.UUID,
        version_id: uuid.UUID,
        payload: ExportCreateIn,
        scope: RequestScope,
    ) -> ExportJobOut:
        self._authorize_answer_key(payload.artifact_type, scope)
        repository = ExportRepository(self._session, scope.org_id)
        source = await repository.source(
            paper_id=paper_id,
            version_id=version_id,
            template_id=payload.template_id,
        )
        if source is None:
            raise NotFoundError("Paper version or template not found.")
        _paper, version, template = source
        if template.status != "active":
            raise ValidationError(
                "Exports require an active template so layout history cannot change after rendering."
            )
        if payload.format not in {"pdf", "docx"}:
            raise ValidationError(
                "The requested export format has no installed renderer.",
                code="EXPORT_FORMAT_UNAVAILABLE",
            )
        if payload.artifact_type == "answer_key" and payload.format != "docx":
            raise ValidationError(
                "Answer-key export is currently available only as DOCX.",
                code="EXPORT_FORMAT_UNAVAILABLE",
            )
        if payload.artifact_type == "answer_key" and not version.answer_key:
            raise ValidationError(
                "This paper version has no answer key to export.",
                code="ANSWER_KEY_UNAVAILABLE",
            )
        row = await repository.create(
            paper_id=paper_id,
            paper_version_id=version_id,
            template_id=template.id,
            requested_by=scope.user_id,
            format=payload.format,
            artifact_type=payload.artifact_type,
            status="queued",
        )
        await add_outbox_task(
            self._session,
            org_id=scope.org_id,
            task_name="papergen.workers.render_export",
            idempotency_key=f"export:{row.id}:render",
            payload={"org_id": str(scope.org_id), "export_id": str(row.id)},
        )
        return _resource(row)

    async def get(self, *, export_id: uuid.UUID, scope: RequestScope) -> ExportJobOut:
        row = await ExportRepository(self._session, scope.org_id).get_by_id(export_id)
        if row is None:
            raise NotFoundError("Export job not found.")
        self._authorize_answer_key(row.artifact_type, scope)
        return _resource(row)

    async def list(
        self,
        *,
        paper_id: uuid.UUID,
        version_id: uuid.UUID,
        scope: RequestScope,
        artifact_type: str | None = None,
    ) -> list[ExportJobOut]:
        if artifact_type == "answer_key":
            self._authorize_answer_key(artifact_type, scope)
        elif artifact_type is None and scope.role not in {"coaching_admin", "super_admin"}:
            # A broad history request from a teacher must not reveal that a protected
            # answer-key artifact exists, even as metadata.
            artifact_type = "paper"
        rows = await ExportRepository(self._session, scope.org_id).list_for_version(
            paper_id=paper_id,
            version_id=version_id,
            artifact_type=artifact_type,
        )
        return [_resource(row) for row in rows]

    async def download(
        self, *, export_id: uuid.UUID, scope: RequestScope
    ) -> SignedDownloadOut:
        row = await ExportRepository(self._session, scope.org_id).get_by_id(export_id)
        if row is None:
            raise NotFoundError("Export job not found.")
        self._authorize_answer_key(row.artifact_type, scope)
        if row.status != "done" or not row.object_key:
            raise ValidationError("Export artifact is not ready.", code="EXPORT_NOT_READY")
        url = S3ArtifactStore(self._settings).signed_get_url(
            key=row.object_key,
            expires_seconds=self._settings.EXPORT_SIGNED_URL_TTL_SECONDS,
        )
        return SignedDownloadOut(
            export_id=row.id,
            url=url,
            expires_in_seconds=self._settings.EXPORT_SIGNED_URL_TTL_SECONDS,
        )

    @staticmethod
    def _authorize_answer_key(artifact_type: str, scope: RequestScope) -> None:
        """Close P6-T3's interim shared-guard gap with purpose-aware RBAC.

        Reviewers need the answer key before approval, so approval-state gating would
        make review impossible. The documented v1 review capability belongs to
        coaching_admin (and the existing platform super_admin capability); teachers
        retain paper-export access but receive a real 403 for answer-key metadata,
        creation, and signed-URL minting.
        """

        if artifact_type == "answer_key" and scope.role not in {
            "coaching_admin",
            "super_admin",
        }:
            raise AuthorizationError(
                "You do not have access to answer-key artifacts.",
                log_detail=(
                    f"answer-key artifact denied for role={scope.role} "
                    f"user_id={scope.user_id}"
                ),
            )


class ExportProcessor:
    """Worker-side state machine. Each method runs in its caller's transaction."""

    def __init__(self, *, settings: Settings) -> None:
        self.settings = settings
        self.registry = RendererRegistry(
            (
                PlaywrightPdfRenderer(
                    node_binary=settings.EXPORT_RENDERER_NODE_BINARY,
                    timeout_seconds=settings.EXPORT_RENDER_TIMEOUT_SECONDS,
                ),
                DocxRenderer(),
            )
        )

    async def claim(
        self, *, session: AsyncSession, org_id: uuid.UUID, export_id: uuid.UUID
    ) -> tuple[ExportArtifact, ExportRenderRequest] | None:
        job = (
            await session.execute(
                select(ExportArtifact)
                .where(ExportArtifact.id == export_id, ExportArtifact.org_id == org_id)
                .with_for_update()
            )
        ).scalar_one_or_none()
        if job is None:
            raise LookupError("export job not found")
        if job.status == "done":
            return None
        source = await ExportRepository(session, org_id).source(
            paper_id=job.paper_id,
            version_id=job.paper_version_id,
            template_id=job.template_id,
        )
        if source is None:
            raise LookupError("export source no longer exists")
        paper, version, template = source
        job.status = "processing"
        job.started_at = datetime.now(timezone.utc)
        job.error = None
        request = ExportRenderRequest(
            format=job.format,
            paper_id=paper.id,
            paper_version_id=version.id,
            template_id=template.id,
            title=paper.title,
            version_label=version.label,
            version_created_at=version.created_at,
            duration_minutes=paper.duration_min,
            total_marks=paper.total_marks,
            content=version.content,
            template_layout=template.layout,
            artifact_type=job.artifact_type,
            answer_key=version.answer_key,
        )
        return job, request

    def render_and_store(
        self, *, job: ExportArtifact, request: ExportRenderRequest
    ) -> tuple[str, bytes, str]:
        artifact = self.registry.for_format(job.format).render(request)
        key = export_object_key(job)
        if artifact.format != job.format or artifact.artifact_type != job.artifact_type:
            raise ValueError("renderer returned artifact metadata that does not match its job")
        store = S3ArtifactStore(self.settings)
        if artifact.format == "pdf":
            store.put_pdf(key=key, content=artifact.content)
        elif artifact.format == "docx":
            store.put_docx(
                key=key,
                content=artifact.content,
                artifact_type=artifact.artifact_type,
            )
        else:
            raise ValueError(f"storage adapter missing for format {artifact.format!r}")
        return key, artifact.content, artifact.media_type

    async def complete(
        self,
        *,
        session: AsyncSession,
        org_id: uuid.UUID,
        export_id: uuid.UUID,
        key: str,
        content: bytes,
        media_type: str,
    ) -> ExportArtifact:
        job = (
            await session.execute(
                select(ExportArtifact)
                .where(ExportArtifact.id == export_id, ExportArtifact.org_id == org_id)
                .with_for_update()
            )
        ).scalar_one()
        if job.status == "done":
            return job
        now = datetime.now(timezone.utc)
        job.status = "done"
        job.object_key = key
        job.media_type = media_type
        job.byte_size = len(content)
        job.sha256 = hashlib.sha256(content).hexdigest()
        job.completed_at = now
        job.error = None
        version = (
            await session.execute(
                select(PaperVersion).where(PaperVersion.id == job.paper_version_id).with_for_update()
            )
        ).scalar_one()
        if job.artifact_type == "paper" and job.format == "pdf":
            version.pdf_key = key
            version.updated_at = now
        elif job.artifact_type == "paper" and job.format == "docx":
            version.docx_key = key
            version.updated_at = now
        session.add(
            Usage(
                org_id=org_id,
                user_id=job.requested_by,
                metric="exports_generated",
                qty=Decimal(1),
            )
        )
        return job

    async def fail(
        self, *, session: AsyncSession, org_id: uuid.UUID, export_id: uuid.UUID, error: str
    ) -> None:
        job = (
            await session.execute(
                select(ExportArtifact)
                .where(ExportArtifact.id == export_id, ExportArtifact.org_id == org_id)
                .with_for_update()
            )
        ).scalar_one_or_none()
        if job is not None and job.status != "done":
            job.status = "failed"
            job.error = error[:2000]
            job.completed_at = datetime.now(timezone.utc)


__all__ = ["ExportProcessor", "ExportService", "export_object_key"]
