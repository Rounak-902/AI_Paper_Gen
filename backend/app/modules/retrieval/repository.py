"""Adapter from the retrieval pipeline to P2's Question Bank read service."""

from __future__ import annotations

import uuid
from dataclasses import dataclass, replace
from typing import Optional

from sqlalchemy.ext.asyncio import AsyncSession

from app.common.deps import RequestScope
from app.modules.curriculum.repository import (
    ChapterRepository,
    ClassRepository,
    SubjectRepository,
)
from app.modules.questions import service as question_service
from app.modules.retrieval import schemas
from app.modules.retrieval.stages import (
    DuplicateEdge,
    LexicalSignal,
    MetadataCandidate,
)


@dataclass(frozen=True)
class ModePolicy:
    sources: Optional[tuple[str, ...]]
    question_scope: Optional[str]
    required_meta_key: Optional[str] = None


# The PRD's GM-1..GM-7 descriptions are the authority for these source/scope
# restrictions. ``mixed`` intentionally leaves both unrestricted; its percentage
# constraints belong to P4's paper solver, not to candidate eligibility.
MODE_POLICIES: dict[str, ModePolicy] = {
    "textual": ModePolicy(("textbook",), None),
    "intext": ModePolicy(("intext",), None),
    "ai_generated": ModePolicy(("ai_generated",), None),
    "mixed": ModePolicy(None, None),
    # PRD GM-5 rejects provenance-free PYQs. A year-range selector belongs in a
    # later blueprint contract, but the minimum eligibility rule is knowable now.
    "previous_year": ModePolicy(("previous_year",), None, "year"),
    # PRD GM-6 is broader than the source label: it includes the institute_bank
    # feed *and any approved question owned by the organization*. Scope is the
    # hard discriminator; restricting source here would wrongly hide an org's
    # approved textbook, in-text, PYQ, or AI-authored material.
    "institute_bank": ModePolicy(None, "org"),
    "global_bank": ModePolicy(None, "global"),
}


class QuestionBankCandidateSource:
    """Read-only P2 adapter; it owns no session and never commits or flushes."""

    def __init__(
        self,
        *,
        session: AsyncSession,
        scope: RequestScope,
        org_id: uuid.UUID,
    ) -> None:
        self._session = session
        self._scope = scope
        self._org_id = org_id
        self._authorized_source_subject_ids: set[uuid.UUID] = set()

    async def _chapter_sources(
        self,
        slot: schemas.BlueprintSlot,
    ) -> tuple[dict[uuid.UUID, set[uuid.UUID]], set[uuid.UUID]]:
        """Resolve requested current chapters to their confirmed ancestors.

        The current subject is checked against the caller's ordinary P2 teacher
        scope *before* widening. Only ancestors reached by following a requested
        chapter's ``derived_from_chapter_id`` chain are admitted, so a dropped or
        merely same-named chapter from an older syllabus never enters the pool.
        """

        if (
            self._scope.subject_ids is not None
            and slot.subject_id not in self._scope.subject_ids
        ):
            return {}, set()

        subject_repo = SubjectRepository(self._session, self._org_id)
        class_repo = ClassRepository(self._session, self._org_id)
        chapter_repo = ChapterRepository(self._session, self._org_id)
        requested_subject = await subject_repo.get_by_id(slot.subject_id)
        school_class = await class_repo.get_by_id(slot.class_id)
        if (
            requested_subject is None
            or school_class is None
            or requested_subject.class_id != slot.class_id
            or requested_subject.curriculum_version_id
            != slot.curriculum_version_id
            or school_class.board_id != slot.board_id
        ):
            return {}, set()

        requested_by_source: dict[uuid.UUID, set[uuid.UUID]] = {}
        source_subject_ids: set[uuid.UUID] = set()
        subjects = {requested_subject.id: requested_subject}
        for requested_chapter_id in slot.chapter_ids:
            chapter = await chapter_repo.get_by_id(requested_chapter_id)
            if (
                chapter is None
                or chapter.subject_id != slot.subject_id
                or chapter.curriculum_version_id != slot.curriculum_version_id
            ):
                # Metadata filters fail closed when the slot combines unrelated
                # curriculum ids; returning a partial pool would hide bad input.
                return {}, set()

            for source_chapter in await chapter_repo.get_lineage(
                requested_chapter_id
            ):
                source_subject = subjects.get(source_chapter.subject_id)
                if source_subject is None:
                    source_subject = await subject_repo.get_by_id(
                        source_chapter.subject_id
                    )
                    if source_subject is None:
                        return {}, set()
                    subjects[source_subject.id] = source_subject

                # P2-T1's lineage write path already enforces same board/class/
                # subject identity. Repeat the cheap read-side checks so corrupt
                # or manually inserted edges fail closed at retrieval time.
                if (
                    source_subject.class_id != slot.class_id
                    or source_subject.name.casefold()
                    != requested_subject.name.casefold()
                ):
                    return {}, set()
                requested_by_source.setdefault(source_chapter.id, set()).add(
                    requested_chapter_id
                )
                source_subject_ids.add(source_chapter.subject_id)

        return requested_by_source, source_subject_ids

    def _signal_scope(self) -> RequestScope:
        if self._scope.subject_ids is None:
            return self._scope
        return replace(
            self._scope,
            subject_ids=tuple(
                sorted(self._authorized_source_subject_ids, key=str)
            ),
        )

    async def metadata_candidates(
        self,
        slot: schemas.BlueprintSlot,
        excluded_question_ids: tuple[uuid.UUID, ...],
    ) -> tuple[MetadataCandidate, ...]:
        policy = MODE_POLICIES[slot.generation_mode]
        requested_by_source, source_subject_ids = await self._chapter_sources(slot)
        if not requested_by_source:
            return ()
        self._authorized_source_subject_ids.update(source_subject_ids)
        questions = await question_service.retrieval_metadata_candidates(
            session=self._session,
            scope=self._signal_scope(),
            org_id=self._org_id,
            board_id=slot.board_id,
            class_id=slot.class_id,
            source_subject_ids=tuple(sorted(source_subject_ids, key=str)),
            chapter_ids=tuple(sorted(requested_by_source, key=str)),
            learning_outcome_ids=tuple(slot.learning_outcome_ids),
            qtype=slot.qtype,
            marks=slot.marks,
            difficulty=slot.difficulty,
            bloom=slot.bloom,
            allowed_sources=policy.sources,
            question_scope=policy.question_scope,
            required_meta_key=policy.required_meta_key,
            excluded_question_ids=excluded_question_ids,
        )
        return tuple(
            MetadataCandidate(
                question=question,
                coverage_chapter_ids=tuple(
                    sorted(
                        requested_by_source.get(question.chapter_id, set()),
                        key=str,
                    )
                ),
            )
            for question in questions
        )

    async def lexical_signals(
        self,
        question_ids: tuple[uuid.UUID, ...],
        query_text: Optional[str],
    ) -> tuple[LexicalSignal, ...]:
        values = await question_service.retrieval_lexical_scores(
            session=self._session,
            scope=self._signal_scope(),
            org_id=self._org_id,
            question_ids=question_ids,
            query_text=query_text,
        )
        return tuple(
            LexicalSignal(
                question_id=value.question_id,
                fts_score=value.fts_score,
                trigram_score=value.trigram_score,
            )
            for value in values
        )

    async def duplicate_edges(
        self,
        question_ids: tuple[uuid.UUID, ...],
        threshold: float,
    ) -> tuple[DuplicateEdge, ...]:
        values = await question_service.retrieval_duplicate_pairs(
            session=self._session,
            scope=self._signal_scope(),
            org_id=self._org_id,
            question_ids=question_ids,
            near_duplicate_threshold=threshold,
        )
        return tuple(
            DuplicateEdge(
                left_question_id=value.left_question_id,
                right_question_id=value.right_question_id,
                exact=value.exact,
                similarity=value.similarity,
            )
            for value in values
        )


__all__ = ["MODE_POLICIES", "ModePolicy", "QuestionBankCandidateSource"]
