"""P3-T1 retrieval stages and the real P2 Question Bank read adapter.

The pure tests pin the caller-visible pipeline contract without a database.  The
database tests deliberately use PostgreSQL's generated search document and pg_trgm
functions through :class:`QuestionBankCandidateSource`; a fake cannot prove that P3
still delegates lexical scoring and teacher narrowing to P2's read path.
"""

from __future__ import annotations

import datetime as dt
import decimal
import uuid
from collections.abc import Mapping, Sequence
from typing import Optional

import pytest
from pydantic import ValidationError

from app.common.deps import RequestScope
from app.models import Chapter, CurriculumVersion, QuestionBank, Subject
from app.modules.questions.schemas import QuestionOut
from app.modules.retrieval import schemas
from app.modules.retrieval.repository import QuestionBankCandidateSource
from app.modules.retrieval.service import RetrievalService
from app.modules.retrieval.stages import (
    CoverageOptimizerStage,
    DeduplicationStage,
    DuplicateEdge,
    HybridWeights,
    LexicalSignal,
    MetadataCandidate,
    RankedCandidate,
    VectorSearchStage,
)


BOARD_ID = uuid.UUID(int=101)
CLASS_ID = uuid.UUID(int=102)
SUBJECT_ID = uuid.UUID(int=103)
CURRICULUM_VERSION_ID = uuid.UUID(int=104)
CHAPTER_A_ID = uuid.UUID(int=105)
CHAPTER_B_ID = uuid.UUID(int=106)
OUTCOME_ID = uuid.UUID(int=107)
NOW = dt.datetime(2026, 1, 1, tzinfo=dt.timezone.utc)


def _slot(
    slot_id: str = "slot-a",
    *,
    board_id: uuid.UUID = BOARD_ID,
    class_id: uuid.UUID = CLASS_ID,
    subject_id: uuid.UUID = SUBJECT_ID,
    curriculum_version_id: uuid.UUID = CURRICULUM_VERSION_ID,
    chapter_ids: Sequence[uuid.UUID] = (CHAPTER_A_ID,),
    topic: Optional[str] = "kinetic force",
    keywords: Sequence[str] = (),
    generation_mode: schemas.GenerationMode = "mixed",
    required_candidates: int = 1,
) -> schemas.BlueprintSlot:
    return schemas.BlueprintSlot(
        slot_id=slot_id,
        board_id=board_id,
        class_id=class_id,
        subject_id=subject_id,
        curriculum_version_id=curriculum_version_id,
        chapter_ids=list(chapter_ids),
        qtype="short_answer",
        marks=decimal.Decimal("2.0"),
        difficulty="medium",
        bloom="understand",
        generation_mode=generation_mode,
        topic=topic,
        keywords=list(keywords),
        required_candidates=required_candidates,
    )


def _question_out(
    *,
    question_id: Optional[uuid.UUID] = None,
    chapter_id: uuid.UUID = CHAPTER_A_ID,
    subject_id: uuid.UUID = SUBJECT_ID,
    stem: str = "Explain kinetic force.",
    learning_outcome_ids: Sequence[uuid.UUID] = (),
    created_at: dt.datetime = NOW,
) -> QuestionOut:
    question_id = question_id or uuid.uuid4()
    return QuestionOut(
        id=question_id,
        root_question_id=question_id,
        previous_version_id=None,
        version_number=1,
        is_current=True,
        org_id=uuid.UUID(int=108),
        scope="org",
        source="textbook",
        board_id=BOARD_ID,
        class_id=CLASS_ID,
        subject_id=subject_id,
        chapter_id=chapter_id,
        qtype="short_answer",
        marks=decimal.Decimal("2.0"),
        difficulty="medium",
        bloom="understand",
        language="en",
        stem=stem,
        options=None,
        answer={"text": "A deterministic answer."},
        rubric={"key_points": ["force"]},
        learning_outcome_ids=list(learning_outcome_ids),
        competency_ids=[],
        meta={},
        content_hash=uuid.uuid4().hex,
        status="approved",
        created_by=None,
        prompt_version_id=None,
        created_at=created_at,
        updated_at=created_at,
        deleted_at=None,
        row_version=1,
    )


class StubCandidateSource:
    """Small in-memory implementation of the retrieval read port."""

    def __init__(
        self,
        *,
        questions_by_slot: Optional[Mapping[str, Sequence[QuestionOut]]] = None,
        lexical_signals: Sequence[LexicalSignal] = (),
        duplicate_edges: Sequence[DuplicateEdge] = (),
    ) -> None:
        self.questions_by_slot = {
            slot_id: tuple(questions)
            for slot_id, questions in (questions_by_slot or {}).items()
        }
        self.signals_by_id = {
            signal.question_id: signal for signal in lexical_signals
        }
        self.edges = tuple(duplicate_edges)
        self.metadata_calls: list[tuple[str, tuple[uuid.UUID, ...]]] = []
        self.lexical_calls: list[tuple[tuple[uuid.UUID, ...], Optional[str]]] = []
        self.duplicate_calls: list[tuple[tuple[uuid.UUID, ...], float]] = []

    async def metadata_candidates(
        self,
        slot: schemas.BlueprintSlot,
        excluded_question_ids: tuple[uuid.UUID, ...],
    ) -> tuple[MetadataCandidate, ...]:
        self.metadata_calls.append((slot.slot_id, excluded_question_ids))
        excluded = set(excluded_question_ids)
        return tuple(
            MetadataCandidate(
                question=question,
                coverage_chapter_ids=(question.chapter_id,)
                if question.chapter_id is not None
                else (),
            )
            for question in self.questions_by_slot.get(slot.slot_id, ())
            if question.id not in excluded
        )

    async def lexical_signals(
        self,
        question_ids: tuple[uuid.UUID, ...],
        query_text: Optional[str],
    ) -> tuple[LexicalSignal, ...]:
        self.lexical_calls.append((question_ids, query_text))
        return tuple(
            self.signals_by_id[question_id]
            for question_id in question_ids
            if question_id in self.signals_by_id
        )

    async def duplicate_edges(
        self,
        question_ids: tuple[uuid.UUID, ...],
        threshold: float,
    ) -> tuple[DuplicateEdge, ...]:
        self.duplicate_calls.append((question_ids, threshold))
        visible = set(question_ids)
        return tuple(
            edge
            for edge in self.edges
            if edge.left_question_id in visible
            and edge.right_question_id in visible
        )


def _service(
    source,
    *,
    top_k: int = 20,
    vector_scorer=None,
) -> RetrievalService:
    return RetrievalService(
        source=source,
        top_k=top_k,
        dedup_threshold=0.95,
        weights=HybridWeights(
            metadata=0.4,
            fts=0.4,
            trigram=0.2,
            vector=0.0,
        ),
        vector_enabled=False,
        vector_scorer=vector_scorer,
    )


def test_retrieval_request_schema_accepts_camel_case_and_rejects_ambiguity() -> None:
    excluded_id = uuid.uuid4()
    request = schemas.BlueprintRetrievalRequest.model_validate(
        {
            "slots": [
                {
                    "slotId": "section-a-1",
                    "boardId": str(BOARD_ID),
                    "classId": str(CLASS_ID),
                    "subjectId": str(SUBJECT_ID),
                    "curriculumVersionId": str(CURRICULUM_VERSION_ID),
                    "chapterIds": [str(CHAPTER_A_ID)],
                    "qtype": "short_answer",
                    "marks": "2.0",
                    "difficulty": "medium",
                    "bloom": "understand",
                    "generationMode": "textual",
                    "topic": "  kinetic force  ",
                    "keywords": [" motion ", "MOTION", "net   force"],
                    "excludedQuestionIds": [str(excluded_id)],
                    "requiredCandidates": 2,
                }
            ],
            "chapterCoverage": [
                {
                    "chapterId": str(CHAPTER_A_ID),
                    "weight": 2.0,
                    "minimumCandidates": 1,
                }
            ],
            "topKPerSlot": 3,
        }
    )

    slot = request.slots[0]
    assert slot.topic == "kinetic force"
    assert slot.keywords == ["motion", "net force"]
    assert slot.lexical_query == "kinetic force motion net force"
    dumped = request.model_dump(by_alias=True, mode="json")
    assert dumped["topKPerSlot"] == 3
    assert dumped["slots"][0]["excludedQuestionIds"] == [str(excluded_id)]

    invalid = dumped.copy()
    invalid["slots"] = [
        {**dumped["slots"][0], "chapterIds": [str(CHAPTER_A_ID)] * 2}
    ]
    with pytest.raises(ValidationError, match="chapter_ids must not contain duplicates"):
        schemas.BlueprintRetrievalRequest.model_validate(invalid)

    with pytest.raises(ValidationError, match="Extra inputs are not permitted"):
        schemas.BlueprintRetrievalRequest.model_validate(
            {**dumped, "silentlyIgnored": True}
        )


async def test_service_pins_exact_stage_trace_and_weighted_hybrid_score() -> None:
    question = _question_out()
    source = StubCandidateSource(
        questions_by_slot={"slot-a": (question,)},
        lexical_signals=(
            LexicalSignal(
                question_id=question.id,
                fts_score=0.75,
                trigram_score=0.25,
            ),
        ),
    )

    result = await _service(source).select_for_blueprint(
        schemas.BlueprintRetrievalRequest(slots=[_slot()])
    )

    assert [(stage.name, stage.state) for stage in result.stages] == [
        ("metadata_filter", "active"),
        ("fts_trigram", "active"),
        ("vector_search", "disabled"),
        ("hybrid_rank", "active"),
        ("deduplication", "active"),
        ("coverage_optimizer", "active"),
        ("final_selection", "active"),
    ]
    candidate = result.slots[0].candidates[0]
    assert candidate.question_id == question.id
    # 0.4 * binary metadata + 0.4 * FTS + 0.2 * trigram + 0.0 * vector.
    assert candidate.score == pytest.approx(0.75)
    assert candidate.metadata_score == 1.0
    assert candidate.vector_score == 0.0


async def test_disabled_vector_slot_never_calls_an_injected_hostile_scorer() -> None:
    class HostileVectorScorer:
        calls = 0

        async def score(self, slot, candidates):
            self.calls += 1
            raise AssertionError("disabled vector scorer was called")

    question = _question_out()
    candidate = RankedCandidate(question=question, vector_score=0.99)
    scorer = HostileVectorScorer()

    result = await VectorSearchStage(enabled=False, scorer=scorer).run(
        _slot(), (candidate,)
    )

    assert scorer.calls == 0
    assert result[0].question is question
    assert result[0].vector_score == 0.0


async def test_deduplication_keeps_the_best_hybrid_ranked_cluster_member() -> None:
    lower = _question_out(stem="Lower ranked duplicate")
    winner = _question_out(stem="Higher ranked duplicate")
    independent = _question_out(stem="Independent question")
    source = StubCandidateSource(
        duplicate_edges=(
            DuplicateEdge(
                left_question_id=lower.id,
                right_question_id=winner.id,
                exact=False,
                similarity=0.97,
            ),
        )
    )
    pool = (
        RankedCandidate(question=lower, hybrid_score=0.71, pre_coverage_rank=2),
        RankedCandidate(question=winner, hybrid_score=0.93, pre_coverage_rank=1),
        RankedCandidate(
            question=independent, hybrid_score=0.60, pre_coverage_rank=3
        ),
    )

    deduplicated, removed = await DeduplicationStage(
        source, threshold=0.95
    ).run({"slot-a": pool})

    assert [candidate.question.id for candidate in deduplicated["slot-a"]] == [
        winner.id,
        independent.id,
    ]
    assert deduplicated["slot-a"][0].dedup_cluster_size == 2
    assert deduplicated["slot-a"][1].dedup_cluster_size == 1
    assert removed == {"slot-a": 1}
    assert source.duplicate_calls[0][1] == pytest.approx(0.95)


def test_coverage_optimizer_spreads_selection_across_required_chapters() -> None:
    best_a = RankedCandidate(
        question=_question_out(chapter_id=CHAPTER_A_ID, stem="Best chapter A"),
        hybrid_score=0.95,
        pre_coverage_rank=1,
    )
    second_a = RankedCandidate(
        question=_question_out(chapter_id=CHAPTER_A_ID, stem="Second chapter A"),
        hybrid_score=0.90,
        pre_coverage_rank=2,
    )
    lower_b = RankedCandidate(
        question=_question_out(chapter_id=CHAPTER_B_ID, stem="Lower chapter B"),
        hybrid_score=0.20,
        pre_coverage_rank=3,
    )
    request = schemas.BlueprintRetrievalRequest(
        slots=[_slot(chapter_ids=(CHAPTER_A_ID, CHAPTER_B_ID))],
        chapter_coverage=[
            schemas.ChapterCoverageRequirement(
                chapter_id=CHAPTER_A_ID, minimum_candidates=1
            ),
            schemas.ChapterCoverageRequirement(
                chapter_id=CHAPTER_B_ID, minimum_candidates=1
            ),
        ],
        top_k_per_slot=2,
    )

    selected, counts = CoverageOptimizerStage().run(
        pools={"slot-a": (best_a, second_a, lower_b)},
        request=request,
        top_k=2,
    )

    # A relevance-only top two would both be chapter A; the required spread wins.
    assert [item.question.id for item in selected["slot-a"]] == [
        best_a.question.id,
        lower_b.question.id,
    ]
    assert {item.question.chapter_id for item in selected["slot-a"]} == {
        CHAPTER_A_ID,
        CHAPTER_B_ID,
    }
    assert [(count.target_id, count.selected_count) for count in counts] == [
        (CHAPTER_A_ID, 1),
        (CHAPTER_B_ID, 1),
    ]


def test_coverage_optimizer_keeps_per_slot_alternatives_without_false_gap() -> None:
    shared = RankedCandidate(
        question=_question_out(stem="Shared candidate"),
        hybrid_score=0.90,
        pre_coverage_rank=1,
    )
    flexible_only = RankedCandidate(
        question=_question_out(stem="Flexible-slot alternative"),
        hybrid_score=0.80,
        pre_coverage_rank=2,
    )
    request = schemas.BlueprintRetrievalRequest(
        slots=[_slot("flexible"), _slot("constrained")],
        top_k_per_slot=1,
    )

    selected, _ = CoverageOptimizerStage().run(
        pools={
            "flexible": (shared, flexible_only),
            "constrained": (shared,),
        },
        request=request,
        top_k=1,
    )

    # Top-K is an alternatives list per slot. Reserving the shared question for
    # the first slot would manufacture a false gap before P4's joint solver runs.
    assert len(selected["flexible"]) == 1
    assert [item.question.id for item in selected["constrained"]] == [
        shared.question.id
    ]


def test_coverage_minima_are_weighted_not_client_list_ordered() -> None:
    lower_weight = RankedCandidate(
        question=_question_out(chapter_id=CHAPTER_A_ID, stem="Low weight"),
        hybrid_score=0.99,
        pre_coverage_rank=1,
    )
    higher_weight = RankedCandidate(
        question=_question_out(chapter_id=CHAPTER_B_ID, stem="High weight"),
        hybrid_score=0.10,
        pre_coverage_rank=2,
    )
    request = schemas.BlueprintRetrievalRequest(
        slots=[_slot(chapter_ids=(CHAPTER_A_ID, CHAPTER_B_ID))],
        chapter_coverage=[
            schemas.ChapterCoverageRequirement(
                chapter_id=CHAPTER_A_ID, weight=1.0, minimum_candidates=1
            ),
            schemas.ChapterCoverageRequirement(
                chapter_id=CHAPTER_B_ID, weight=10.0, minimum_candidates=1
            ),
        ],
        top_k_per_slot=1,
    )

    selected, _ = CoverageOptimizerStage().run(
        pools={"slot-a": (lower_weight, higher_weight)},
        request=request,
        top_k=1,
    )

    assert selected["slot-a"][0].question.id == higher_weight.question.id


def test_coverage_counts_shared_lineage_candidate_in_each_eligible_slot() -> None:
    chapter_c_id = uuid.UUID(int=110)
    inherited = RankedCandidate(
        question=_question_out(stem="Inherited by two current chapters"),
        coverage_chapter_ids=(CHAPTER_A_ID, CHAPTER_B_ID),
        hybrid_score=0.50,
        pre_coverage_rank=2,
    )
    chapter_c_distractor = RankedCandidate(
        question=_question_out(
            chapter_id=chapter_c_id,
            stem="Higher relevance but does not satisfy chapter B",
        ),
        coverage_chapter_ids=(chapter_c_id,),
        hybrid_score=0.99,
        pre_coverage_rank=1,
    )
    request = schemas.BlueprintRetrievalRequest(
        slots=[
            _slot("chapter-a", chapter_ids=(CHAPTER_A_ID,)),
            _slot("chapter-b", chapter_ids=(CHAPTER_B_ID, chapter_c_id)),
        ],
        chapter_coverage=[
            schemas.ChapterCoverageRequirement(
                chapter_id=CHAPTER_A_ID, minimum_candidates=1
            ),
            schemas.ChapterCoverageRequirement(
                chapter_id=CHAPTER_B_ID, minimum_candidates=1
            ),
        ],
        top_k_per_slot=1,
    )

    selected, counts = CoverageOptimizerStage().run(
        pools={
            "chapter-a": (inherited,),
            "chapter-b": (chapter_c_distractor, inherited),
        },
        request=request,
        top_k=1,
    )

    # A lineage ancestor is one unique question, but it must be present in each
    # compatible slot pool to make both current-chapter alternatives available.
    assert selected["chapter-a"][0].question.id == inherited.question.id
    assert selected["chapter-b"][0].question.id == inherited.question.id
    assert [count.selected_count for count in counts] == [1, 1]


def _db_question(
    *,
    org_id: Optional[uuid.UUID],
    board_id: uuid.UUID,
    class_id: uuid.UUID,
    subject_id: uuid.UUID,
    chapter_id: uuid.UUID,
    stem: str,
    status: str = "approved",
    scope: str = "org",
    source: str = "textbook",
    difficulty: str = "medium",
    is_current: bool = True,
    meta: Optional[dict[str, object]] = None,
) -> QuestionBank:
    question_id = uuid.uuid4()
    return QuestionBank(
        id=question_id,
        root_question_id=question_id,
        previous_version_id=None,
        version_number=1,
        is_current=is_current,
        org_id=org_id,
        scope=scope,
        source=source,
        board_id=board_id,
        class_id=class_id,
        subject_id=subject_id,
        chapter_id=chapter_id,
        qtype="short_answer",
        marks=decimal.Decimal("2.0"),
        difficulty=difficulty,
        bloom="understand",
        language="en",
        stem=stem,
        options=None,
        answer={"text": "A deterministic P3 answer."},
        rubric={"key_points": ["retrieval"]},
        learning_outcome_ids=[],
        competency_ids=[],
        meta=meta or {},
        content_hash=uuid.uuid4().hex,
        status=status,
    )


def _scope(
    tenant,
    *,
    role: str = "coaching_admin",
    subject_ids: Optional[tuple[uuid.UUID, ...]] = None,
) -> RequestScope:
    return RequestScope(
        org_id=tenant.org_id,
        user_id=tenant.user_id,
        role=role,
        subject_ids=subject_ids,
    )


@pytest.mark.db
async def test_real_adapter_hard_filters_metadata_and_reuses_p2_lexical_scores(
    db_session, tenant, curriculum
) -> None:
    term = f"p3t1lex{uuid.uuid4().hex[:12]}"
    other_chapter = Chapter(
        subject_id=curriculum.subject_id,
        curriculum_version_id=curriculum.curriculum_version_id,
        seq=2,
        title=f"P3-T1 excluded chapter {uuid.uuid4()}",
    )
    db_session.add(other_chapter)
    await db_session.flush()

    common = {
        "org_id": tenant.org_id,
        "board_id": curriculum.board_id,
        "class_id": curriculum.class_id,
        "subject_id": curriculum.subject_id,
        "chapter_id": curriculum.chapter_id,
    }
    relevant = _db_question(
        **common,
        stem=f"{term} {term} {term}: explain the retrieval concept",
    )
    irrelevant = _db_question(
        **common,
        stem="Explain an intentionally unrelated geometric construction",
    )
    excluded = _db_question(**common, stem=f"{term}: explicitly excluded")
    draft = _db_question(**common, stem=f"{term}: draft", status="draft")
    wrong_difficulty = _db_question(
        **common, stem=f"{term}: wrong difficulty", difficulty="hard"
    )
    wrong_source = _db_question(
        **common, stem=f"{term}: wrong source", source="institute_bank"
    )
    wrong_chapter = _db_question(
        **{**common, "chapter_id": other_chapter.id},
        stem=f"{term}: wrong chapter",
    )
    stale = _db_question(**common, stem=f"{term}: stale", is_current=False)
    db_session.add_all(
        [
            relevant,
            irrelevant,
            excluded,
            draft,
            wrong_difficulty,
            wrong_source,
            wrong_chapter,
            stale,
        ]
    )
    await db_session.flush()

    source = QuestionBankCandidateSource(
        session=db_session,
        scope=_scope(tenant),
        org_id=tenant.org_id,
    )
    slot = _slot(
        board_id=curriculum.board_id,
        class_id=curriculum.class_id,
        subject_id=curriculum.subject_id,
        curriculum_version_id=curriculum.curriculum_version_id,
        chapter_ids=(curriculum.chapter_id,),
        topic=term,
        generation_mode="textual",
    ).model_copy(update={"excluded_question_ids": [excluded.id]})

    metadata = await source.metadata_candidates(slot, (excluded.id,))
    assert {candidate.question.id for candidate in metadata} == {
        relevant.id,
        irrelevant.id,
    }

    lexical = {
        signal.question_id: signal
        for signal in await source.lexical_signals(
            (relevant.id, irrelevant.id), slot.lexical_query
        )
    }
    assert set(lexical) == {relevant.id, irrelevant.id}
    assert lexical[relevant.id].fts_score > lexical[irrelevant.id].fts_score
    assert lexical[relevant.id].trigram_score > lexical[irrelevant.id].trigram_score

    result = await _service(source, top_k=5).select_candidates(slot)
    assert [candidate.question_id for candidate in result.candidates] == [
        relevant.id,
        irrelevant.id,
    ]
    assert result.candidates[0].score > result.candidates[1].score
    assert result.metadata_candidate_count == 2


@pytest.mark.db
async def test_real_adapter_hard_filters_requested_learning_outcomes(
    db_session, tenant, curriculum
) -> None:
    requested_outcome_id = uuid.uuid4()
    common = {
        "org_id": tenant.org_id,
        "board_id": curriculum.board_id,
        "class_id": curriculum.class_id,
        "subject_id": curriculum.subject_id,
        "chapter_id": curriculum.chapter_id,
    }
    matching = _db_question(**common, stem="Matches the requested outcome")
    matching.learning_outcome_ids = [requested_outcome_id]
    other = _db_question(**common, stem="Same metadata, different outcome")
    other.learning_outcome_ids = [uuid.uuid4()]
    db_session.add_all([matching, other])
    await db_session.flush()

    source = QuestionBankCandidateSource(
        session=db_session,
        scope=_scope(tenant),
        org_id=tenant.org_id,
    )
    slot = _slot(
        board_id=curriculum.board_id,
        class_id=curriculum.class_id,
        subject_id=curriculum.subject_id,
        curriculum_version_id=curriculum.curriculum_version_id,
        chapter_ids=(curriculum.chapter_id,),
        topic=None,
        generation_mode="textual",
    ).model_copy(update={"learning_outcome_ids": [requested_outcome_id]})

    metadata = await source.metadata_candidates(slot, ())
    assert [candidate.question.id for candidate in metadata] == [matching.id]


@pytest.mark.db
async def test_previous_year_mode_requires_year_provenance(
    db_session, tenant, curriculum
) -> None:
    common = {
        "org_id": tenant.org_id,
        "board_id": curriculum.board_id,
        "class_id": curriculum.class_id,
        "subject_id": curriculum.subject_id,
        "chapter_id": curriculum.chapter_id,
        "source": "previous_year",
    }
    with_year = _db_question(
        **common,
        stem="A previous-year question with provenance",
        meta={"year": 2024},
    )
    blank_year = _db_question(
        **common,
        stem="A previous-year question with blank provenance",
        meta={"year": "   "},
    )
    missing_year = _db_question(
        **common,
        stem="A previous-year question without provenance",
    )
    db_session.add_all([with_year, blank_year, missing_year])
    await db_session.flush()

    source = QuestionBankCandidateSource(
        session=db_session,
        scope=_scope(tenant),
        org_id=tenant.org_id,
    )
    slot = _slot(
        board_id=curriculum.board_id,
        class_id=curriculum.class_id,
        subject_id=curriculum.subject_id,
        curriculum_version_id=curriculum.curriculum_version_id,
        chapter_ids=(curriculum.chapter_id,),
        topic=None,
        generation_mode="previous_year",
    )

    metadata = await source.metadata_candidates(slot, ())
    assert [candidate.question.id for candidate in metadata] == [with_year.id]


@pytest.mark.db
async def test_real_adapter_inherits_only_confirmed_chapter_lineage(
    db_session, tenant, curriculum
) -> None:
    older_version = CurriculumVersion(
        board_id=curriculum.board_id,
        academic_year=f"older-{uuid.uuid4()}",
        label="Older test syllabus",
        status="superseded",
    )
    db_session.add(older_version)
    await db_session.flush()
    older_subject = Subject(
        class_id=curriculum.class_id,
        curriculum_version_id=older_version.id,
        name="Mathematics",
    )
    db_session.add(older_subject)
    await db_session.flush()
    ancestor = Chapter(
        subject_id=older_subject.id,
        curriculum_version_id=older_version.id,
        seq=1,
        title="Real Numbers (older)",
    )
    dropped = Chapter(
        subject_id=older_subject.id,
        curriculum_version_id=older_version.id,
        seq=2,
        title="Dropped old chapter",
    )
    db_session.add_all([ancestor, dropped])
    await db_session.flush()
    current = await db_session.get(Chapter, curriculum.chapter_id)
    assert current is not None
    current.derived_from_chapter_id = ancestor.id

    inherited = _db_question(
        org_id=tenant.org_id,
        board_id=curriculum.board_id,
        class_id=curriculum.class_id,
        subject_id=older_subject.id,
        chapter_id=ancestor.id,
        stem="Inherited from the confirmed ancestor",
    )
    dropped_question = _db_question(
        org_id=tenant.org_id,
        board_id=curriculum.board_id,
        class_id=curriculum.class_id,
        subject_id=older_subject.id,
        chapter_id=dropped.id,
        stem="Must not leak from a dropped chapter",
    )
    db_session.add_all([inherited, dropped_question])
    await db_session.flush()

    source = QuestionBankCandidateSource(
        session=db_session,
        scope=_scope(
            tenant,
            role="teacher",
            subject_ids=(curriculum.subject_id,),
        ),
        org_id=tenant.org_id,
    )
    slot = _slot(
        board_id=curriculum.board_id,
        class_id=curriculum.class_id,
        subject_id=curriculum.subject_id,
        curriculum_version_id=curriculum.curriculum_version_id,
        chapter_ids=(curriculum.chapter_id,),
        topic=None,
        generation_mode="textual",
    )

    result = await _service(source, top_k=5).select_candidates(slot)
    ids = [candidate.question_id for candidate in result.candidates]
    assert inherited.id in ids
    assert dropped_question.id not in ids
    inherited_out = next(
        candidate
        for candidate in result.candidates
        if candidate.question_id == inherited.id
    )
    assert inherited_out.chapter_id == ancestor.id
    assert inherited_out.matched_chapter_ids == [curriculum.chapter_id]


@pytest.mark.db
async def test_real_adapter_applies_teacher_subject_scope_to_same_blueprint(
    db_session, tenant, curriculum
) -> None:
    outside_subject = Subject(
        class_id=curriculum.class_id,
        curriculum_version_id=curriculum.curriculum_version_id,
        name=f"P3-T1 outside teacher scope {uuid.uuid4()}",
    )
    db_session.add(outside_subject)
    await db_session.flush()
    outside_chapter = Chapter(
        subject_id=outside_subject.id,
        curriculum_version_id=curriculum.curriculum_version_id,
        seq=1,
        title=f"P3-T1 outside chapter {uuid.uuid4()}",
    )
    db_session.add(outside_chapter)
    await db_session.flush()

    assigned_question = _db_question(
        org_id=tenant.org_id,
        board_id=curriculum.board_id,
        class_id=curriculum.class_id,
        subject_id=curriculum.subject_id,
        chapter_id=curriculum.chapter_id,
        stem=f"P3-T1 assigned subject {uuid.uuid4()}",
    )
    outside_question = _db_question(
        org_id=tenant.org_id,
        board_id=curriculum.board_id,
        class_id=curriculum.class_id,
        subject_id=outside_subject.id,
        chapter_id=outside_chapter.id,
        stem=f"P3-T1 outside subject {uuid.uuid4()}",
    )
    db_session.add_all([assigned_question, outside_question])
    await db_session.flush()

    request = schemas.BlueprintRetrievalRequest(
        slots=[
            _slot(
                "assigned",
                board_id=curriculum.board_id,
                class_id=curriculum.class_id,
                subject_id=curriculum.subject_id,
                curriculum_version_id=curriculum.curriculum_version_id,
                chapter_ids=(curriculum.chapter_id,),
                topic=None,
                generation_mode="textual",
            ),
            _slot(
                "outside",
                board_id=curriculum.board_id,
                class_id=curriculum.class_id,
                subject_id=outside_subject.id,
                curriculum_version_id=curriculum.curriculum_version_id,
                chapter_ids=(outside_chapter.id,),
                topic=None,
                generation_mode="textual",
            ),
        ],
        top_k_per_slot=5,
    )

    admin_source = QuestionBankCandidateSource(
        session=db_session,
        scope=_scope(tenant),
        org_id=tenant.org_id,
    )
    teacher_source = QuestionBankCandidateSource(
        session=db_session,
        scope=_scope(
            tenant,
            role="teacher",
            subject_ids=(curriculum.subject_id,),
        ),
        org_id=tenant.org_id,
    )

    admin = await _service(admin_source, top_k=5).select_for_blueprint(request)
    teacher = await _service(teacher_source, top_k=5).select_for_blueprint(request)
    admin_by_slot = {slot.slot_id: slot for slot in admin.slots}
    teacher_by_slot = {slot.slot_id: slot for slot in teacher.slots}

    assert [candidate.question_id for candidate in admin_by_slot["assigned"].candidates] == [
        assigned_question.id
    ]
    assert [candidate.question_id for candidate in admin_by_slot["outside"].candidates] == [
        outside_question.id
    ]
    assert [
        candidate.question_id
        for candidate in teacher_by_slot["assigned"].candidates
    ] == [assigned_question.id]
    assert teacher_by_slot["outside"].candidates == []
    assert teacher_by_slot["outside"].metadata_candidate_count == 0
    assert teacher_by_slot["outside"].gap is True
