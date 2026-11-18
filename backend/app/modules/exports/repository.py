"""Tenant-scoped export job persistence."""

from __future__ import annotations

import uuid
from collections.abc import Sequence

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.db.repository import BaseRepository
from app.models.export import ExportArtifact
from app.models.paper import GeneratedPaper, PaperVersion
from app.models.template import Template


class ExportRepository(BaseRepository[ExportArtifact]):
    model_class = ExportArtifact

    async def source(
        self, *, paper_id: uuid.UUID, version_id: uuid.UUID, template_id: uuid.UUID
    ) -> tuple[GeneratedPaper, PaperVersion, Template] | None:
        row = (
            await self._session.execute(
                select(GeneratedPaper, PaperVersion, Template)
                .join(
                    PaperVersion,
                    (PaperVersion.paper_id == GeneratedPaper.id)
                    & (PaperVersion.id == version_id),
                )
                .join(
                    Template,
                    (Template.id == template_id)
                    & (Template.org_id == self._org_id)
                    & (Template.deleted_at.is_(None)),
                )
                .where(
                    GeneratedPaper.id == paper_id,
                    GeneratedPaper.org_id == self._org_id,
                    GeneratedPaper.deleted_at.is_(None),
                )
            )
        ).one_or_none()
        return row.t if row is not None else None

    async def list_for_version(
        self,
        *,
        paper_id: uuid.UUID,
        version_id: uuid.UUID,
        artifact_type: str | None = None,
    ) -> Sequence[ExportArtifact]:
        filters = [
            ExportArtifact.paper_id == paper_id,
            ExportArtifact.paper_version_id == version_id,
        ]
        if artifact_type is not None:
            filters.append(ExportArtifact.artifact_type == artifact_type)
        return await self.find_many(
            *filters,
            order_by=(ExportArtifact.created_at.desc(), ExportArtifact.id.desc()),
        )


__all__ = ["ExportRepository"]
