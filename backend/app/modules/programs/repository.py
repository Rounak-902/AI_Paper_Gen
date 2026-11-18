"""Repository for the programs module."""

from __future__ import annotations

import uuid
from typing import Optional, Sequence

from sqlalchemy import delete, select

from app.db.repository import BaseRepository
from app.models.program import Program, ProgramSubject, ProgramTeacher


class ProgramRepository(BaseRepository[Program]):
    model_class = Program

    async def list_programs(
        self,
        *,
        include_deleted: bool = False,
        limit: Optional[int] = None,
        offset: Optional[int] = None,
    ) -> Sequence[Program]:
        return await self.find_many(
            include_deleted=include_deleted,
            order_by=(Program.created_at.desc(), Program.id),
            limit=limit,
            offset=offset,
        )

    async def assign_teachers(
        self,
        program_id: uuid.UUID,
        teacher_ids: list[uuid.UUID],
    ) -> None:
        """Full replacement of teachers assigned to this program."""
        await self._session.execute(
            delete(ProgramTeacher).where(ProgramTeacher.program_id == program_id)
        )
        if teacher_ids:
            self._session.add_all(
                [
                    ProgramTeacher(program_id=program_id, user_id=tid)
                    for tid in set(teacher_ids)
                ]
            )
        await self._session.flush()

    async def assign_subjects(
        self,
        program_id: uuid.UUID,
        subject_ids: list[uuid.UUID],
    ) -> None:
        """Full replacement of subjects assigned to this program."""
        await self._session.execute(
            delete(ProgramSubject).where(ProgramSubject.program_id == program_id)
        )
        if subject_ids:
            self._session.add_all(
                [
                    ProgramSubject(program_id=program_id, subject_id=sid)
                    for sid in set(subject_ids)
                ]
            )
        await self._session.flush()

    async def get_assignment_ids(
        self,
        program_id: uuid.UUID,
    ) -> tuple[tuple[uuid.UUID, ...], tuple[uuid.UUID, ...]]:
        """Return the teacher and subject IDs currently assigned to a live program."""
        program_scope = (
            Program.id == program_id,
            Program.org_id == self._org_id,
            Program.deleted_at.is_(None),
        )
        teacher_result = await self._session.execute(
            select(ProgramTeacher.user_id)
            .join(Program, Program.id == ProgramTeacher.program_id)
            .where(*program_scope)
            .order_by(ProgramTeacher.user_id)
        )
        subject_result = await self._session.execute(
            select(ProgramSubject.subject_id)
            .join(Program, Program.id == ProgramSubject.program_id)
            .where(*program_scope)
            .order_by(ProgramSubject.subject_id)
        )
        return (
            tuple(teacher_result.scalars().all()),
            tuple(subject_result.scalars().all()),
        )

    async def get_teacher_subject_ids(
        self,
        user_id: uuid.UUID,
    ) -> tuple[uuid.UUID, ...]:
        """Fetch all subject IDs assigned to a teacher across all active programs in this org."""
        stmt = (
            select(ProgramSubject.subject_id)
            .select_from(ProgramTeacher)
            .join(Program, Program.id == ProgramTeacher.program_id)
            .join(ProgramSubject, ProgramSubject.program_id == Program.id)
            .where(
                ProgramTeacher.user_id == user_id,
                Program.org_id == self._org_id,
                Program.status == "active",
                Program.deleted_at.is_(None),
            )
            .distinct()
        )
        result = await self._session.execute(stmt)
        return tuple(result.scalars().all())


__all__ = ["ProgramRepository"]
