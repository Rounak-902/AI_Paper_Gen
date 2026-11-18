"""Transactional Celery outbox used by P4-T5 producers and slot chaining."""

from __future__ import annotations

import uuid
from typing import Any

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.assembly import TaskOutbox


async def add_outbox_task(
    session: AsyncSession,
    *,
    org_id: uuid.UUID,
    task_name: str,
    idempotency_key: str,
    payload: dict[str, Any],
) -> TaskOutbox:
    """Persist publication intent in the caller's current DB transaction."""

    existing = (
        await session.execute(
            select(TaskOutbox).where(TaskOutbox.idempotency_key == idempotency_key)
        )
    ).scalar_one_or_none()
    if existing is not None:
        return existing
    row = TaskOutbox(
        org_id=org_id,
        task_name=task_name,
        idempotency_key=idempotency_key,
        payload=payload,
    )
    session.add(row)
    await session.flush()
    return row


async def schedule_paper_generation(
    session: AsyncSession,
    *,
    org_id: uuid.UUID,
    paper_id: uuid.UUID,
    label: str = "Generated Set A",
) -> TaskOutbox:
    """Schedule generation without publishing before the paper transaction commits.

    The outbox FK rejects a nonexistent paper, and the reconciler uses a separate
    transaction, so an uncommitted paper/outbox pair is structurally invisible.
    """

    return await add_outbox_task(
        session,
        org_id=org_id,
        task_name="papergen.workers.prepare_paper",
        idempotency_key=f"paper:{paper_id}:prepare:{label}",
        payload={"org_id": str(org_id), "paper_id": str(paper_id), "label": label},
    )


__all__ = ["add_outbox_task", "schedule_paper_generation"]
