"""Database adapter for P4-T4; transaction ownership stays with the worker."""

from __future__ import annotations

import json
import uuid
from typing import Any

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.curriculum import Chapter, LearningOutcome
from app.models.membership import Membership
from app.models.paper import GeneratedPaper, PaperVersion
from app.models.textbook import TextbookAsset


class PaperAssemblyRepository:
    def __init__(self, session: AsyncSession, org_id: uuid.UUID) -> None:
        self.session = session
        self.org_id = org_id

    async def get_paper(self, paper_id: uuid.UUID) -> GeneratedPaper | None:
        result = await self.session.execute(
            select(GeneratedPaper).where(
                GeneratedPaper.id == paper_id,
                GeneratedPaper.org_id == self.org_id,
                GeneratedPaper.deleted_at.is_(None),
            )
        )
        return result.scalar_one_or_none()

    async def get_active_membership(
        self, user_id: uuid.UUID
    ) -> Membership | None:
        result = await self.session.execute(
            select(Membership).where(
                Membership.user_id == user_id,
                Membership.org_id == self.org_id,
                Membership.status == "active",
                Membership.deleted_at.is_(None),
            )
        )
        return result.scalar_one_or_none()

    async def get_version(
        self, paper_id: uuid.UUID, label: str
    ) -> PaperVersion | None:
        result = await self.session.execute(
            select(PaperVersion).where(
                PaperVersion.paper_id == paper_id,
                PaperVersion.label == label,
            )
        )
        return result.scalar_one_or_none()

    async def get_version_by_id(
        self, paper_id: uuid.UUID, version_id: uuid.UUID
    ) -> PaperVersion | None:
        """Load one immutable source version inside the already-scoped paper."""

        result = await self.session.execute(
            select(PaperVersion).where(
                PaperVersion.id == version_id,
                PaperVersion.paper_id == paper_id,
            )
        )
        return result.scalar_one_or_none()

    async def curriculum_context(
        self,
        *,
        chapter_ids: tuple[uuid.UUID, ...],
        learning_outcome_ids: tuple[uuid.UUID, ...],
    ) -> tuple[str, list[str]]:
        chapters = (
            await self.session.execute(
                select(Chapter)
                .where(
                    Chapter.id.in_(chapter_ids),
                    Chapter.deleted_at.is_(None),
                )
                .order_by(Chapter.seq, Chapter.id)
            )
        ).scalars().all()
        outcomes = []
        if learning_outcome_ids:
            outcomes = (
                await self.session.execute(
                    select(LearningOutcome)
                    .where(
                        LearningOutcome.id.in_(learning_outcome_ids),
                        LearningOutcome.deleted_at.is_(None),
                    )
                    .order_by(LearningOutcome.code, LearningOutcome.id)
                )
            ).scalars().all()
        description = "; ".join(
            [f"Chapter {chapter.seq}: {chapter.title}" for chapter in chapters]
            + [
                f"Learning outcome {outcome.code or outcome.id}: {outcome.description}"
                for outcome in outcomes
            ]
        )
        snippets: list[str] = []
        assets = (
            await self.session.execute(
                select(TextbookAsset)
                .where(
                    TextbookAsset.chapter_id.in_(chapter_ids),
                    TextbookAsset.deleted_at.is_(None),
                )
                .order_by(TextbookAsset.page, TextbookAsset.id)
                .limit(20)
            )
        ).scalars().all()
        for asset in assets:
            if asset.data is None:
                continue
            if isinstance(asset.data, str):
                text = asset.data
            elif isinstance(asset.data, dict):
                text = str(
                    asset.data.get("text")
                    or asset.data.get("content")
                    or json.dumps(asset.data, ensure_ascii=False, default=str)
                )
            else:
                text = json.dumps(asset.data, ensure_ascii=False, default=str)
            if text.strip():
                snippets.append(text.strip())
        return description or "Curriculum-scoped paper generation", snippets

    async def create_version(
        self,
        *,
        paper: GeneratedPaper,
        label: str,
        content: dict[str, Any],
        answer_key: dict[str, Any],
        total_marks,
    ) -> PaperVersion:
        version = PaperVersion(
            paper_id=paper.id,
            label=label,
            content=content,
            answer_key=answer_key,
        )
        paper.total_marks = total_marks
        paper.row_version += 1
        self.session.add(version)
        await self.session.flush()
        return version


__all__ = ["PaperAssemblyRepository"]
