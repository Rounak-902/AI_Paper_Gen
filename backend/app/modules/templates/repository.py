"""Tenant-scoped persistence for templates."""

from __future__ import annotations

import uuid
from typing import Optional, Sequence

from sqlalchemy import func, select, text, update

from app.db.repository import BaseRepository
from app.models.template import Template


class TemplateRepository(BaseRepository[Template]):
    model_class = Template

    async def list_templates(
        self, *, limit: int, offset: int, status: Optional[str] = None
    ) -> Sequence[Template]:
        filters = () if status is None else (Template.status == status,)
        return await self.find_many(
            *filters,
            order_by=(Template.name.asc(), Template.created_at.desc(), Template.id),
            limit=limit,
            offset=offset,
        )

    async def name_exists(self, name: str) -> bool:
        result = await self._session.execute(
            select(Template.id)
            .where(
                Template.org_id == self._org_id,
                Template.name == name,
                Template.deleted_at.is_(None),
            )
            .limit(1)
        )
        return result.scalar_one_or_none() is not None

    async def lock_default_assignment(self) -> None:
        # A transaction-scoped advisory lock gives each org one serialization point,
        # including the first-default case where no default row exists to row-lock.
        await self._session.execute(
            text("SELECT pg_advisory_xact_lock(hashtextextended(:org_id, 0))"),
            {"org_id": str(self._org_id)},
        )

    async def has_default(self) -> bool:
        result = await self._session.execute(
            select(Template.id)
            .where(
                Template.org_id == self._org_id,
                Template.is_default.is_(True),
                Template.deleted_at.is_(None),
            )
            .limit(1)
        )
        return result.scalar_one_or_none() is not None

    async def clear_defaults(self, *, except_id: uuid.UUID) -> None:
        await self._session.execute(
            update(Template)
            .where(
                Template.org_id == self._org_id,
                Template.id != except_id,
                Template.is_default.is_(True),
                Template.deleted_at.is_(None),
            )
            .values(
                is_default=False,
                row_version=Template.row_version + 1,
                updated_at=func.now(),
            )
        )

