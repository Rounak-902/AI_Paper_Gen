"""Read-only adapter from the real P2 question bank to the pure solver."""

from __future__ import annotations

import uuid
from typing import Optional, Sequence

from sqlalchemy import or_, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.curriculum import Chapter
from app.models.question import QuestionBank


class FeasibilityQuestionRepository:
    def __init__(self, session: AsyncSession, org_id: uuid.UUID) -> None:
        self._session = session
        self._org_id = org_id

    async def list_eligible(
        self,
        *,
        board_id: uuid.UUID,
        class_id: uuid.UUID,
        subject_id: uuid.UUID,
        curriculum_version_id: uuid.UUID,
        chapter_ids: tuple[uuid.UUID, ...],
        language: str,
        subject_scope_ids: Optional[tuple[uuid.UUID, ...]],
    ) -> Sequence[QuestionBank]:
        if subject_scope_ids is not None and subject_id not in subject_scope_ids:
            return ()
        statement = (
            select(QuestionBank)
            .join(Chapter, Chapter.id == QuestionBank.chapter_id)
            .where(
                QuestionBank.deleted_at.is_(None),
                QuestionBank.status == "approved",
                QuestionBank.is_current.is_(True),
                or_(
                    QuestionBank.org_id == self._org_id,
                    QuestionBank.org_id.is_(None),
                ),
                QuestionBank.board_id == board_id,
                QuestionBank.class_id == class_id,
                QuestionBank.subject_id == subject_id,
                QuestionBank.chapter_id.in_(chapter_ids),
                QuestionBank.language == language,
                Chapter.subject_id == subject_id,
                Chapter.curriculum_version_id == curriculum_version_id,
            )
            .order_by(QuestionBank.id.asc())
        )
        result = await self._session.execute(statement)
        return result.scalars().all()


__all__ = ["FeasibilityQuestionRepository"]
