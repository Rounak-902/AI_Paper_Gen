"""Repositories for the global curriculum catalog.

The authenticated organization UUID remains constructor context, but the six owned
tables are global reference data and deliberately have neither ``org_id`` columns nor
RLS policies. Consequently these repositories never add an organization predicate.
They still inherit the central soft-delete and optimistic-locking implementation.
"""

from __future__ import annotations

import uuid
from typing import Any, Generic, Optional, Sequence, TypeVar

from sqlalchemy import func, literal, select

from app.db.repository import BaseRepository
from app.models.curriculum import (
    Board,
    Chapter,
    Class as SchoolClass,
    CurriculumVersion,
    LearningOutcome,
    Subject,
)
CatalogModelT = TypeVar("CatalogModelT")
_BOARD_LOCK_SEED = 0x50325431


class ChapterLineageCycleError(ValueError):
    """Raised when stored chapter lineage contains a cycle."""

    def __init__(self, chapter_id: uuid.UUID) -> None:
        super().__init__(f"Chapter lineage contains a cycle at chapter id={chapter_id}")
        self.chapter_id = chapter_id


class GlobalCatalogRepository(BaseRepository[CatalogModelT], Generic[CatalogModelT]):
    """Base for global rows accessed inside an authenticated org transaction."""

    def _org_id_column(self) -> Any:
        # Explicit rather than relying on the models' current lack of ``org_id``:
        # adding an ownership-looking column later must not silently alter semantics.
        return None

    async def _exists(self, *filters: Any) -> bool:
        stmt = self._base_query().with_only_columns(literal(True)).where(*filters).limit(1)
        result = await self._session.execute(stmt)
        return result.scalar_one_or_none() is not None

    async def soft_delete_with_version(
        self,
        id: uuid.UUID,
        expected_version: int,
    ) -> CatalogModelT:
        """Soft-delete atomically through the base optimistic-lock UPDATE."""

        return await self.update(id, expected_version, deleted_at=func.now())

    async def lock_board(self, board_id: uuid.UUID) -> None:
        """Serialize publication, lineage changes, and writes beneath a board."""

        stmt = select(
            func.pg_advisory_xact_lock(
                func.hashtextextended(
                    literal(str(board_id)), literal(_BOARD_LOCK_SEED)
                )
            )
        )
        await self._session.execute(stmt)


class BoardRepository(GlobalCatalogRepository[Board]):
    model_class = Board

    async def list_boards(
        self,
        *,
        include_deleted: bool = False,
        limit: Optional[int] = None,
        offset: Optional[int] = None,
    ) -> Sequence[Board]:
        return await self.find_many(
            include_deleted=include_deleted,
            order_by=(Board.code, Board.id),
            limit=limit,
            offset=offset,
        )

    async def code_exists(
        self,
        code: str,
        *,
        exclude_id: Optional[uuid.UUID] = None,
    ) -> bool:
        filters: list[Any] = [Board.code == code]
        if exclude_id is not None:
            filters.append(Board.id != exclude_id)
        return await self._exists(*filters)

    async def has_dependencies(self, board_id: uuid.UUID) -> bool:
        """Whether live global curriculum rows still reference the board."""

        class_stmt = (
            select(literal(True))
            .select_from(SchoolClass)
            .where(SchoolClass.board_id == board_id, SchoolClass.deleted_at.is_(None))
            .limit(1)
        )
        version_stmt = (
            select(literal(True))
            .select_from(CurriculumVersion)
            .where(
                CurriculumVersion.board_id == board_id,
                CurriculumVersion.deleted_at.is_(None),
            )
            .limit(1)
        )
        return await _any_result(self, class_stmt, version_stmt)

    has_dependents = has_dependencies


class ClassRepository(GlobalCatalogRepository[SchoolClass]):
    model_class = SchoolClass

    async def list_for_board(
        self,
        board_id: uuid.UUID,
        *,
        include_deleted: bool = False,
        limit: Optional[int] = None,
        offset: Optional[int] = None,
    ) -> Sequence[SchoolClass]:
        return await self.find_many(
            SchoolClass.board_id == board_id,
            include_deleted=include_deleted,
            order_by=(SchoolClass.name, SchoolClass.id),
            limit=limit,
            offset=offset,
        )

    async def name_exists(
        self,
        board_id: uuid.UUID,
        name: str,
        *,
        exclude_id: Optional[uuid.UUID] = None,
    ) -> bool:
        filters: list[Any] = [
            SchoolClass.board_id == board_id,
            SchoolClass.name == name,
        ]
        if exclude_id is not None:
            filters.append(SchoolClass.id != exclude_id)
        return await self._exists(*filters)

    async def has_dependencies(self, class_id: uuid.UUID) -> bool:
        subject_stmt = (
            select(literal(True))
            .select_from(Subject)
            .where(Subject.class_id == class_id, Subject.deleted_at.is_(None))
            .limit(1)
        )
        return await _any_result(self, subject_stmt)

    has_dependents = has_dependencies


class SubjectRepository(GlobalCatalogRepository[Subject]):
    model_class = Subject

    async def list_for_class(
        self,
        class_id: uuid.UUID,
        *,
        curriculum_version_id: Optional[uuid.UUID] = None,
        include_deleted: bool = False,
        limit: Optional[int] = None,
        offset: Optional[int] = None,
    ) -> Sequence[Subject]:
        filters: list[Any] = [Subject.class_id == class_id]
        if curriculum_version_id is not None:
            filters.append(Subject.curriculum_version_id == curriculum_version_id)
        return await self.find_many(
            *filters,
            include_deleted=include_deleted,
            order_by=(Subject.name, Subject.id),
            limit=limit,
            offset=offset,
        )

    async def name_exists(
        self,
        class_id: uuid.UUID,
        name: str,
        *,
        curriculum_version_id: Optional[uuid.UUID] = None,
        exclude_id: Optional[uuid.UUID] = None,
    ) -> bool:
        filters: list[Any] = [Subject.class_id == class_id, Subject.name == name]
        if curriculum_version_id is None:
            filters.append(Subject.curriculum_version_id.is_(None))
        else:
            filters.append(Subject.curriculum_version_id == curriculum_version_id)
        if exclude_id is not None:
            filters.append(Subject.id != exclude_id)
        return await self._exists(*filters)

    async def has_dependencies(self, subject_id: uuid.UUID) -> bool:
        chapter_stmt = (
            select(literal(True))
            .select_from(Chapter)
            .where(Chapter.subject_id == subject_id, Chapter.deleted_at.is_(None))
            .limit(1)
        )
        # Tenant-owned modules may attach only to published snapshots. Published
        # subjects are immutable, so a valid tenant dependency cannot exist while
        # this draft-only delete path is open.
        return await _any_result(self, chapter_stmt)

    async def has_lineage_links(self, subject_id: uuid.UUID) -> bool:
        """Whether a live chapter already records a confirmed provenance edge."""

        stmt = (
            select(literal(True))
            .select_from(Chapter)
            .where(
                Chapter.subject_id == subject_id,
                Chapter.derived_from_chapter_id.is_not(None),
                Chapter.deleted_at.is_(None),
            )
            .limit(1)
        )
        result = await self._session.execute(stmt)
        return result.scalar_one_or_none() is not None

    has_dependents = has_dependencies


class ChapterRepository(GlobalCatalogRepository[Chapter]):
    model_class = Chapter

    async def list_for_subject(
        self,
        subject_id: uuid.UUID,
        *,
        curriculum_version_id: Optional[uuid.UUID] = None,
        include_deleted: bool = False,
        limit: Optional[int] = None,
        offset: Optional[int] = None,
    ) -> Sequence[Chapter]:
        filters: list[Any] = [Chapter.subject_id == subject_id]
        if curriculum_version_id is not None:
            filters.append(Chapter.curriculum_version_id == curriculum_version_id)
        return await self.find_many(
            *filters,
            include_deleted=include_deleted,
            order_by=(Chapter.seq, Chapter.id),
            limit=limit,
            offset=offset,
        )

    async def list_derived_from(
        self,
        chapter_id: uuid.UUID,
        *,
        include_deleted: bool = False,
        limit: Optional[int] = None,
        offset: Optional[int] = None,
    ) -> Sequence[Chapter]:
        return await self.find_many(
            Chapter.derived_from_chapter_id == chapter_id,
            include_deleted=include_deleted,
            order_by=(Chapter.created_at, Chapter.id),
            limit=limit,
            offset=offset,
        )

    async def seq_exists(
        self,
        subject_id: uuid.UUID,
        seq: int,
        *,
        exclude_id: Optional[uuid.UUID] = None,
    ) -> bool:
        filters: list[Any] = [Chapter.subject_id == subject_id, Chapter.seq == seq]
        if exclude_id is not None:
            filters.append(Chapter.id != exclude_id)
        return await self._exists(*filters)

    async def has_dependencies(self, chapter_id: uuid.UUID) -> bool:
        """Whether live global outcomes or lineage preserve a dependency."""

        outcome_stmt = (
            select(literal(True))
            .select_from(LearningOutcome)
            .where(
                LearningOutcome.chapter_id == chapter_id,
                LearningOutcome.deleted_at.is_(None),
            )
            .limit(1)
        )
        derived_stmt = (
            select(literal(True))
            .select_from(Chapter)
            .where(
                Chapter.derived_from_chapter_id == chapter_id,
                Chapter.deleted_at.is_(None),
            )
            .limit(1)
        )
        return await _any_result(self, outcome_stmt, derived_stmt)

    has_dependents = has_dependencies

    async def get_lineage(
        self,
        chapter_id: uuid.UUID,
        *,
        include_self: bool = True,
        root_first: bool = True,
        include_deleted: bool = False,
        max_depth: int = 100,
    ) -> Sequence[Chapter]:
        """Traverse immediate ``derived_from`` edges, detecting corrupt cycles.

        The default result is root-first and ends at the requested chapter. A missing
        source edge stops traversal; the requested row itself still uses the normal
        ``get_by_id_or_raise`` contract.
        """

        if max_depth < 1:
            raise ValueError("max_depth must be at least 1")

        current = await self.get_by_id_or_raise(
            chapter_id,
            include_deleted=include_deleted,
        )
        lineage: list[Chapter] = []
        visited: set[uuid.UUID] = set()

        for _ in range(max_depth):
            if current.id in visited:
                raise ChapterLineageCycleError(current.id)
            visited.add(current.id)
            lineage.append(current)

            parent_id = current.derived_from_chapter_id
            if parent_id is None:
                break
            parent = await self.get_by_id(parent_id, include_deleted=include_deleted)
            if parent is None:
                break
            current = parent
        else:
            raise ValueError(f"Chapter lineage exceeds max_depth={max_depth}")

        if not include_self:
            lineage = lineage[1:]
        if root_first:
            lineage.reverse()
        return lineage

    async def would_create_cycle(
        self,
        chapter_id: uuid.UUID,
        derived_from_chapter_id: Optional[uuid.UUID],
        *,
        max_depth: int = 100,
    ) -> bool:
        """Return true if assigning the proposed immediate source closes a cycle."""

        if derived_from_chapter_id is None:
            return False
        if max_depth < 1:
            raise ValueError("max_depth must be at least 1")

        cursor: Optional[uuid.UUID] = derived_from_chapter_id
        visited: set[uuid.UUID] = set()
        for _ in range(max_depth):
            if cursor is None:
                return False
            if cursor == chapter_id or cursor in visited:
                return True
            visited.add(cursor)
            node = await self.get_by_id(cursor)
            if node is None:
                return False
            cursor = node.derived_from_chapter_id
        # A chain too deep to prove acyclic is rejected conservatively.
        return True


class OutcomeRepository(GlobalCatalogRepository[LearningOutcome]):
    model_class = LearningOutcome

    async def list_for_chapters(
        self,
        chapter_ids: Sequence[uuid.UUID],
        *,
        include_deleted: bool = False,
    ) -> Sequence[LearningOutcome]:
        if not chapter_ids:
            return []
        return await self.find_many(
            LearningOutcome.chapter_id.in_(chapter_ids),
            include_deleted=include_deleted,
            order_by=(
                LearningOutcome.chapter_id,
                LearningOutcome.code,
                LearningOutcome.id,
            ),
        )

    async def list_for_chapter(
        self,
        chapter_id: uuid.UUID,
        *,
        include_deleted: bool = False,
        limit: Optional[int] = None,
        offset: Optional[int] = None,
    ) -> Sequence[LearningOutcome]:
        return await self.find_many(
            LearningOutcome.chapter_id == chapter_id,
            include_deleted=include_deleted,
            order_by=(LearningOutcome.code, LearningOutcome.id),
            limit=limit,
            offset=offset,
        )

    async def code_exists(
        self,
        chapter_id: uuid.UUID,
        code: Optional[str],
        *,
        exclude_id: Optional[uuid.UUID] = None,
    ) -> bool:
        # PostgreSQL's unique index uses the default NULLS DISTINCT semantics.
        if code is None:
            return False
        filters: list[Any] = [
            LearningOutcome.chapter_id == chapter_id,
            LearningOutcome.code == code,
        ]
        if exclude_id is not None:
            filters.append(LearningOutcome.id != exclude_id)
        return await self._exists(*filters)


# Model-oriented alias for callers that prefer the table/entity name.
LearningOutcomeRepository = OutcomeRepository


class CurriculumVersionRepository(GlobalCatalogRepository[CurriculumVersion]):
    model_class = CurriculumVersion

    async def list_for_board(
        self,
        board_id: uuid.UUID,
        *,
        status: Optional[str] = None,
        include_deleted: bool = False,
        limit: Optional[int] = None,
        offset: Optional[int] = None,
    ) -> Sequence[CurriculumVersion]:
        filters: list[Any] = [CurriculumVersion.board_id == board_id]
        if status is not None:
            filters.append(CurriculumVersion.status == status)
        return await self.find_many(
            *filters,
            include_deleted=include_deleted,
            order_by=(CurriculumVersion.effective_from.desc(), CurriculumVersion.id),
            limit=limit,
            offset=offset,
        )

    async def academic_year_exists(
        self,
        board_id: uuid.UUID,
        academic_year: str,
        *,
        exclude_id: Optional[uuid.UUID] = None,
    ) -> bool:
        filters: list[Any] = [
            CurriculumVersion.board_id == board_id,
            CurriculumVersion.academic_year == academic_year,
        ]
        if exclude_id is not None:
            filters.append(CurriculumVersion.id != exclude_id)
        return await self._exists(*filters)

    async def has_dependencies(self, curriculum_version_id: uuid.UUID) -> bool:
        subject_stmt = (
            select(literal(True))
            .select_from(Subject)
            .where(
                Subject.curriculum_version_id == curriculum_version_id,
                Subject.deleted_at.is_(None),
            )
            .limit(1)
        )
        chapter_stmt = (
            select(literal(True))
            .select_from(Chapter)
            .where(
                Chapter.curriculum_version_id == curriculum_version_id,
                Chapter.deleted_at.is_(None),
            )
            .limit(1)
        )
        return await _any_result(self, subject_stmt, chapter_stmt)

    has_dependents = has_dependencies


async def _any_result(
    repository: GlobalCatalogRepository[Any],
    *statements: Any,
) -> bool:
    """Execute existence statements sequentially and short-circuit on the first row."""

    for stmt in statements:
        result = await repository._session.execute(stmt)
        if result.scalar_one_or_none() is not None:
            return True
    return False


__all__ = [
    "BoardRepository",
    "ChapterLineageCycleError",
    "ChapterRepository",
    "ClassRepository",
    "CurriculumVersionRepository",
    "GlobalCatalogRepository",
    "LearningOutcomeRepository",
    "OutcomeRepository",
    "SubjectRepository",
]
