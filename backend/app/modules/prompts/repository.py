"""Database access and release safety for the global prompt-version registry.

This module deliberately lives outside :mod:`app.modules.ai`.  The future P3-T4
pipeline receives an already-resolved prompt; it does not open a database connection
and the P3-T2 provider boundary remains provider-only.
"""

from __future__ import annotations

import uuid

from sqlalchemy import func, select, update
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.prompt import PromptVersion
from packages.prompts import PromptFileStore


class PromptVersionRepository:
    """Read and activate global prompt metadata without owning the transaction."""

    def __init__(
        self,
        session: AsyncSession,
        *,
        file_store: PromptFileStore | None = None,
    ) -> None:
        self._session = session
        self._file_store = file_store or PromptFileStore()

    async def get_active(self, *, task: str, model: str) -> PromptVersion | None:
        """Return the sole live active revision for an exact ``(task, model)`` pair."""

        result = await self._session.execute(
            select(PromptVersion)
            .where(
                PromptVersion.task == task,
                PromptVersion.model == model,
                PromptVersion.status == "active",
                PromptVersion.deleted_at.is_(None),
            )
            .execution_options(populate_existing=True)
        )
        return result.scalar_one_or_none()

    async def get_by_id(self, prompt_version_id: uuid.UUID) -> PromptVersion | None:
        """Return one non-deleted registry row by id."""

        result = await self._session.execute(
            select(PromptVersion)
            .where(
                PromptVersion.id == prompt_version_id,
                PromptVersion.deleted_at.is_(None),
            )
            .execution_options(populate_existing=True)
        )
        return result.scalar_one_or_none()

    async def activate(self, prompt_version_id: uuid.UUID) -> PromptVersion | None:
        """Promote or roll back with one externally-issued ``UPDATE``.

        The target's immutable file is loaded and bound to a snapshot of its
        task/version/reference before any UPDATE is issued, so a missing or malformed
        candidate cannot retire a working incumbent.  The UPDATE compares that complete
        release snapshot (including model and row_version), so a concurrent metadata
        edit fails closed instead of activating content that was not validated.
        Revision 0032's BEFORE trigger then retires the incumbent for the same exact
        ``(task, model)`` pair inside the target UPDATE.  The caller owns commit/rollback.
        ``None`` means the target is missing, soft-deleted, already active, concurrently
        changed, or otherwise not promotable.  A direct or concurrent attempt that
        would leave two active rows is still rejected by
        ``uq_prompt_versions_task_model_active``.
        """

        target = await self.get_by_id(prompt_version_id)
        if target is None or target.status not in ("candidate", "retired"):
            return None

        # Snapshot every field that binds this database release to its deployed file
        # and activation pair.  Comparing the snapshot in the UPDATE closes the gap
        # between this read/file validation and the mutating statement without holding
        # a database row lock across filesystem I/O.
        expected_task = target.task
        expected_version = target.version
        expected_template_ref = target.template_ref
        expected_model = target.model
        expected_row_version = target.row_version

        # File validation belongs before the mutating statement.  Direct SQL can still
        # bypass it (the database cannot inspect deployed files), so every application
        # release path must come through this method.
        self._file_store.load(
            expected_template_ref,
            expected_task=expected_task,
            expected_version=expected_version,
        )

        statement = (
            update(PromptVersion)
            .where(
                PromptVersion.id == prompt_version_id,
                PromptVersion.deleted_at.is_(None),
                PromptVersion.status.in_(("candidate", "retired")),
                PromptVersion.task == expected_task,
                PromptVersion.version == expected_version,
                PromptVersion.template_ref == expected_template_ref,
                PromptVersion.model == expected_model,
                PromptVersion.row_version == expected_row_version,
            )
            .values(
                status="active",
                updated_at=func.now(),
                row_version=PromptVersion.row_version + 1,
            )
            .returning(PromptVersion)
        )
        result = await self._session.execute(
            statement.execution_options(populate_existing=True)
        )
        activated = result.scalar_one_or_none()
        if activated is None:
            return None

        # The trigger's incumbent UPDATE is invisible to SQLAlchemy.  Refresh every
        # loaded PromptVersion (activation is rare and this identity set is normally
        # tiny) so expire_on_commit=False callers cannot observe a phantom second
        # ``active`` row or an old row_version after this method returns.
        for instance in tuple(self._session.identity_map.values()):
            if isinstance(instance, PromptVersion) and instance is not activated:
                await self._session.refresh(instance)
        return activated


__all__ = ["PromptVersionRepository"]
