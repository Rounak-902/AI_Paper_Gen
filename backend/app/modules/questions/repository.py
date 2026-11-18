"""Question Bank repository (P2-T3 CRUD/versioning + P2-T4 dedup + P2-T6 search).

Extends ``QuestionBankRepository`` (``app/db/question_repository.py``) with:

* **Faceted/FTS list/search** — combines all P2-T6 facets with ranked search over
  the generated ``search_document`` vector and applies ``RequestScope.subject_ids``
  when the caller is a teacher (BR-7, P2-T2 carry-forward).
* **fork()** — the BR-8 fork-on-edit operation: INSERT a new version row then
  UPDATE the previous version's ``is_current`` to ``false`` in the same flush.
  The database's partial unique index
  ``uq_question_bank_root_question_id_current WHERE is_current`` is the
  guarantee that both rows cannot be current simultaneously.
* **approve()** — status transition ``draft → approved`` with optimistic locking.
* **soft_delete_question()** — sets ``deleted_at`` with a version guard.
* **dedup on first-version create** — exact ``content_hash`` rejection plus
  non-blocking ``pg_trgm`` stem-similarity candidates.

Why ``fork()`` lives here and not in the service
------------------------------------------------
The fork requires two coordinated writes inside one session flush:

    1. UPDATE old row  ``is_current = False``  (must come first)
    2. INSERT new row  ``is_current = True``

Both must see the same flush boundary so the partial unique index cannot
be violated transiently. The UPDATE is issued first so that at flush time
only one row in the lineage has ``is_current=True``. If the INSERT were
flushed first, the index would see two current rows and raise UniqueViolation.
Putting the session writes in the service layer
would require passing the raw session out to the service, which violates
doc 07 §2.4's "services never touch the session directly". Repository owns
the session; fork is therefore a repository method.

Trigger compatibility check (migration 0020)
--------------------------------------------
The BEFORE UPDATE trigger ``trg_question_bank_reject_approved_edit`` fires on
rows whose *previous* status is ``approved`` and raises SQLSTATE ``23514``
if the UPDATE changes any column outside the mutable set:

    status, updated_at, row_version, is_current, deleted_at, search_document

Step 1 of fork sets ``is_current = False`` on the *approved* previous version.
``is_current`` is explicitly in the mutable set, so the trigger allows this.
No other column on the old version is touched.  Confirmed — no trigger change
needed.
"""

from __future__ import annotations

import decimal
import hashlib
import json
import uuid
from dataclasses import dataclass
from typing import Any, Optional, Sequence

from sqlalchemy import and_, case, false, func, literal, or_, select, text, update
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import aliased

from app.db.exceptions import EntityNotFoundError, OptimisticLockError
from app.db.question_repository import QuestionBankRepository
from app.models.curriculum import Chapter
from app.models.question import QuestionBank


@dataclass(frozen=True)
class NearDuplicateMatch:
    """One visible, current question whose normalised stem is similar."""

    question: QuestionBank
    similarity: float


@dataclass(frozen=True)
class QuestionCreateRepositoryResult:
    """A newly inserted question plus non-blocking near-duplicate matches."""

    question: QuestionBank
    near_duplicates: tuple[NearDuplicateMatch, ...]


@dataclass(frozen=True)
class QuestionSearchPage:
    """One page of questions plus the exact count for the same query."""

    questions: tuple[QuestionBank, ...]
    total: int


@dataclass(frozen=True)
class QuestionLexicalScore:
    """P2 search/dedup signals for one already-authorized question.

    Retrieval consumes this DTO rather than rebuilding either PostgreSQL
    expression.  ``fts_score`` is the same normalized ``ts_rank_cd`` signal used
    by :meth:`QuestionRepository.search_questions`; ``trigram_score`` is the same
    ``pg_trgm.similarity`` signal used by P2-T4 near-duplicate detection.
    """

    question_id: uuid.UUID
    fts_score: float
    trigram_score: float


@dataclass(frozen=True)
class DuplicateQuestionPair:
    """Two visible candidates joined by P2-T4's exact/trigram dedup signal."""

    left_question_id: uuid.UUID
    right_question_id: uuid.UUID
    exact: bool
    similarity: float


class ExactDuplicateQuestionError(Exception):
    """Raised before INSERT when an unrelated question has the same hash."""

    def __init__(self, existing_question: QuestionBank) -> None:
        super().__init__(
            f"question {existing_question.id} has the same content_hash"
        )
        self.existing_question = existing_question


def _compute_content_hash(
    qtype: str,
    stem: str,
    options: Any,
    answer: Any,
    rubric: Any,
) -> str:
    """Compute SHA-256 hex of a canonical JSON representation of question content.

    The hash is deterministic: keys are sorted, floats preserved as-is, NoneType
    becomes JSON null. This is the key P2-T4's dedup logic will build on.
    """
    canonical = json.dumps(
        {"qtype": qtype, "stem": stem, "options": options, "answer": answer, "rubric": rubric},
        sort_keys=True,
        ensure_ascii=False,
        default=str,  # decimals → str; UUIDs → str; datetimes → str
    )
    return hashlib.sha256(canonical.encode("utf-8")).hexdigest()


def _normalise_stem(stem: str) -> str:
    """Collapse whitespace and lowercase a stem for trigram comparison only.

    This deliberately does not alter ``_compute_content_hash``.  P2-T3 already
    established that hash contract (including its exact casing and whitespace),
    while the P2-T4 near-duplicate requirement explicitly calls for comparison
    on a normalised stem.
    """

    return " ".join(stem.split()).lower()


def _fts_query_and_rank(query_text: Optional[str]) -> tuple[Any, Any]:
    """Return P2-T6's canonical tsquery/rank expressions.

    Keeping these expressions here is what makes the question-bank endpoint and
    P3 retrieval use one lexical contract.  A future analyzer or normalization
    change therefore cannot silently alter one read path but not the other.
    """

    normalized_query = query_text.strip() if query_text else None
    if not normalized_query:
        return None, None
    tsquery = func.plainto_tsquery(text("'english'::regconfig"), normalized_query)
    return (
        tsquery,
        func.ts_rank_cd(QuestionBank.search_document, tsquery, 32),
    )


def _stem_similarity(left: Any, right: Any) -> Any:
    """Return P2-T4's canonical ``pg_trgm`` stem-similarity expression."""

    return func.similarity(left, right)


class QuestionRepository(QuestionBankRepository):
    """Repository for Question Bank with scope-filtered reads and fork-on-edit."""

    model_class = QuestionBank

    def __init__(self, session: AsyncSession, org_id: uuid.UUID) -> None:
        super().__init__(session, org_id)

    # ------------------------------------------------------------------
    # Scope-filtered reads (P2-T2 carry-forward)
    # ------------------------------------------------------------------

    async def list_questions(
        self,
        *,
        subject_ids: Optional[tuple[uuid.UUID, ...]] = None,
        board_id: Optional[uuid.UUID] = None,
        class_id: Optional[uuid.UUID] = None,
        curriculum_version_id: Optional[uuid.UUID] = None,
        qtype: Optional[str] = None,
        marks: Optional[decimal.Decimal] = None,
        difficulty: Optional[str] = None,
        bloom: Optional[str] = None,
        subject_id: Optional[uuid.UUID] = None,
        chapter_id: Optional[uuid.UUID] = None,
        source: Optional[str] = None,
        status: Optional[str] = None,
        scope: Optional[str] = None,
        current_only: bool = True,
        limit: Optional[int] = 50,
        offset: int = 0,
    ) -> Sequence[QuestionBank]:
        """Return a filtered page without a full-text predicate.

        This compatibility entry point and :meth:`search_questions` deliberately
        share one implementation. Tenant/global visibility, soft-delete handling,
        teacher subject narrowing, version visibility, facets, and counting must
        not drift merely because a caller omitted ``q``.
        """
        result = await self.search_questions(
            query_text=None,
            subject_ids=subject_ids,
            board_id=board_id,
            class_id=class_id,
            curriculum_version_id=curriculum_version_id,
            qtype=qtype,
            marks=marks,
            difficulty=difficulty,
            bloom=bloom,
            subject_id=subject_id,
            chapter_id=chapter_id,
            source=source,
            status=status,
            scope=scope,
            current_only=current_only,
            limit=limit,
            offset=offset,
        )
        return list(result.questions)

    async def search_questions(
        self,
        *,
        query_text: Optional[str] = None,
        subject_ids: Optional[tuple[uuid.UUID, ...]] = None,
        board_id: Optional[uuid.UUID] = None,
        class_id: Optional[uuid.UUID] = None,
        curriculum_version_id: Optional[uuid.UUID] = None,
        qtype: Optional[str] = None,
        marks: Optional[decimal.Decimal] = None,
        difficulty: Optional[str] = None,
        bloom: Optional[str] = None,
        subject_id: Optional[uuid.UUID] = None,
        chapter_id: Optional[uuid.UUID] = None,
        chapter_ids: Optional[tuple[uuid.UUID, ...]] = None,
        learning_outcome_ids: Optional[tuple[uuid.UUID, ...]] = None,
        source: Optional[str] = None,
        sources: Optional[tuple[str, ...]] = None,
        required_meta_key: Optional[str] = None,
        status: Optional[str] = None,
        scope: Optional[str] = None,
        current_only: bool = True,
        limit: Optional[int] = 50,
        offset: int = 0,
        calculate_total: bool = True,
    ) -> QuestionSearchPage:
        """Return an exactly-counted, optionally ranked faceted search page.

        Uses ``plainto_tsquery`` rather than ``to_tsquery`` so that plain
        space-separated words work without the caller knowing tsquery syntax. The
        query is matched against PostgreSQL's generated ``search_document`` column
        (stem + answer + rubric) and ordered by coverage-density rank. With no
        query text, ordering falls back to newest-first.

        ``subject_ids`` is the existing P2-T2/P2-T3 teacher restriction: ``None``
        means unrestricted (admin), while an empty tuple is represented by SQL
        ``false`` and therefore fails closed. The org/global and soft-delete
        predicates likewise remain identical for FTS and non-FTS reads.
        """
        predicates = [
            # Mirror the question_bank RLS read domain: this org plus platform-global.
            or_(
                QuestionBank.org_id == self._org_id,
                QuestionBank.org_id.is_(None),
            ),
            QuestionBank.deleted_at.is_(None),
        ]

        if current_only:
            predicates.append(QuestionBank.is_current.is_(True))

        if chapter_id is not None and chapter_ids is not None:
            raise ValueError("chapter_id and chapter_ids are mutually exclusive")
        if source is not None and sources is not None:
            raise ValueError("source and sources are mutually exclusive")

        # Reuse the direct-read meaning of RequestScope.subject_ids exactly.
        if subject_ids is not None:
            if len(subject_ids) == 0:
                predicates.append(false())
            else:
                predicates.append(QuestionBank.subject_id.in_(subject_ids))

        facets = (
            (QuestionBank.board_id, board_id),
            (QuestionBank.class_id, class_id),
            (QuestionBank.subject_id, subject_id),
            (QuestionBank.chapter_id, chapter_id),
            (QuestionBank.qtype, qtype),
            (QuestionBank.marks, marks),
            (QuestionBank.difficulty, difficulty),
            (QuestionBank.bloom, bloom),
            (QuestionBank.source, source),
            (QuestionBank.status, status),
            (QuestionBank.scope, scope),
        )
        for column, value in facets:
            if value is not None:
                predicates.append(column == value)
        if chapter_ids is not None:
            predicates.append(
                false()
                if len(chapter_ids) == 0
                else QuestionBank.chapter_id.in_(chapter_ids)
            )
        if learning_outcome_ids is not None:
            predicates.append(
                false()
                if len(learning_outcome_ids) == 0
                else QuestionBank.learning_outcome_ids.overlap(
                    list(learning_outcome_ids)
                )
            )
        if sources is not None:
            predicates.append(
                false()
                if len(sources) == 0
                else QuestionBank.source.in_(sources)
            )
        if required_meta_key is not None:
            # Retrieval mode guardrails may require provenance recorded in the
            # existing JSONB metadata without teaching this generic repository
            # about generation modes. Missing, JSON-null, and blank values all
            # fail closed.
            predicates.append(
                func.nullif(
                    func.btrim(QuestionBank.meta[required_meta_key].astext),
                    "",
                ).is_not(None)
            )
        if curriculum_version_id is not None:
            # Question rows deliberately carry no curriculum_version_id. Chapter is
            # the retrieval anchor, so constrain through that real FK instead of
            # trusting the caller's chapter/version pairing.
            predicates.append(
                QuestionBank.chapter.has(
                    Chapter.curriculum_version_id == curriculum_version_id
                )
            )

        tsquery, rank = _fts_query_and_rank(query_text)
        if tsquery is not None:
            predicates.append(QuestionBank.search_document.op("@@")(tsquery))

        total: Optional[int] = None
        if calculate_total:
            count_stmt = select(func.count(QuestionBank.id)).where(*predicates)
            total = int((await self._session.execute(count_stmt)).scalar_one())

        stmt = select(QuestionBank).where(*predicates)
        if rank is not None:
            stmt = stmt.order_by(
                rank.desc(), QuestionBank.created_at.desc(), QuestionBank.id
            )
        else:
            stmt = stmt.order_by(QuestionBank.created_at.desc(), QuestionBank.id)

        if limit is not None:
            stmt = stmt.limit(limit)
        result = await self._session.execute(stmt.offset(offset))
        questions = tuple(result.scalars().all())
        return QuestionSearchPage(
            questions=questions,
            total=total if total is not None else len(questions),
        )

    async def score_lexical_candidates(
        self,
        *,
        question_ids: Sequence[uuid.UUID],
        query_text: Optional[str],
        subject_ids: Optional[tuple[uuid.UUID, ...]] = None,
    ) -> tuple[QuestionLexicalScore, ...]:
        """Score a metadata-filtered candidate set with P2-T6/P2-T4 signals.

        Unlike :meth:`search_questions`, this method does not apply ``@@`` as an
        additional hard predicate.  Retrieval already has its metadata pool and
        needs zero-score rows to remain available for coverage/gap reporting.  The
        expressions themselves are shared with P2's search and dedup paths.
        """

        if not question_ids:
            return ()

        visibility = [
            QuestionBank.id.in_(tuple(question_ids)),
            or_(
                QuestionBank.org_id == self._org_id,
                QuestionBank.org_id.is_(None),
            ),
            QuestionBank.deleted_at.is_(None),
            QuestionBank.is_current.is_(True),
        ]
        if subject_ids is not None:
            if len(subject_ids) == 0:
                return ()
            visibility.append(QuestionBank.subject_id.in_(subject_ids))

        tsquery, rank = _fts_query_and_rank(query_text)
        if tsquery is None:
            fts_score = literal(0.0).label("fts_score")
            trigram_score = literal(0.0).label("trigram_score")
        else:
            fts_score = rank.label("fts_score")
            trigram_score = _stem_similarity(
                QuestionBank.stem,
                _normalise_stem(query_text or ""),
            ).label("trigram_score")

        stmt = (
            select(QuestionBank.id, fts_score, trigram_score)
            .where(*visibility)
            .order_by(QuestionBank.id)
        )
        rows = (await self._session.execute(stmt)).all()
        return tuple(
            QuestionLexicalScore(
                question_id=row[0],
                fts_score=float(row[1] or 0.0),
                trigram_score=float(row[2] or 0.0),
            )
            for row in rows
        )

    async def find_duplicate_candidate_pairs(
        self,
        *,
        question_ids: Sequence[uuid.UUID],
        near_duplicate_threshold: float,
        subject_ids: Optional[tuple[uuid.UUID, ...]] = None,
    ) -> tuple[DuplicateQuestionPair, ...]:
        """Return exact/near duplicate edges within a visible candidate set.

        P2-T4 established ``content_hash`` and PostgreSQL stem trigram similarity
        as the two MVP signals.  P3 calls this method and clusters the returned
        edges, preserving the highest hybrid-ranked candidate in each cluster.
        """

        unique_ids = tuple(dict.fromkeys(question_ids))
        if len(unique_ids) < 2:
            return ()
        if subject_ids is not None and len(subject_ids) == 0:
            return ()

        left = aliased(QuestionBank)
        right = aliased(QuestionBank)
        similarity = _stem_similarity(left.stem, right.stem)
        same_dedup_domain = or_(
            and_(left.org_id == self._org_id, right.org_id == self._org_id),
            and_(left.org_id.is_(None), right.org_id.is_(None)),
        )

        def visible(model: Any) -> list[Any]:
            predicates = [
                model.id.in_(unique_ids),
                or_(model.org_id == self._org_id, model.org_id.is_(None)),
                model.deleted_at.is_(None),
                model.is_current.is_(True),
            ]
            if subject_ids is not None:
                predicates.append(model.subject_id.in_(subject_ids))
            return predicates

        # PostgreSQL's UUID btree ordering gives one orientation per unordered pair.
        stmt = (
            select(
                left.id,
                right.id,
                (left.content_hash == right.content_hash).label("exact"),
                similarity.label("similarity"),
            )
            .where(
                *visible(left),
                *visible(right),
                left.id < right.id,
                same_dedup_domain,
                left.subject_id.is_not_distinct_from(right.subject_id),
                or_(
                    left.content_hash == right.content_hash,
                    # P2-T4's live insert path is inclusive at the configured
                    # boundary. Reuse that executable contract even though doc 06
                    # writes the prose rule with a strict greater-than sign.
                    similarity >= near_duplicate_threshold,
                ),
            )
            .order_by(left.id, right.id)
        )
        rows = (await self._session.execute(stmt)).all()
        return tuple(
            DuplicateQuestionPair(
                left_question_id=row[0],
                right_question_id=row[1],
                exact=bool(row[2]),
                similarity=float(row[3] or 0.0),
            )
            for row in rows
        )

    async def get_question(
        self,
        question_id: uuid.UUID,
        *,
        subject_ids: Optional[tuple[uuid.UUID, ...]] = None,
        include_deleted: bool = False,
    ) -> Optional[QuestionBank]:
        """Fetch a single question, respecting teacher scope narrowing.

        Returns None if: not found, wrong org, soft-deleted (unless
        include_deleted=True), or teacher is not assigned to that subject.
        """
        stmt = select(QuestionBank).where(
            QuestionBank.id == question_id,
            or_(
                QuestionBank.org_id == self._org_id,
                QuestionBank.org_id.is_(None),
            ),
        )
        if not include_deleted:
            stmt = stmt.where(QuestionBank.deleted_at.is_(None))

        # Teacher scope narrowing on reads
        if subject_ids is not None:
            if len(subject_ids) == 0:
                return None
            stmt = stmt.where(QuestionBank.subject_id.in_(subject_ids))

        result = await self._session.execute(stmt)
        return result.scalar_one_or_none()

    async def get_question_unscoped(
        self,
        question_id: uuid.UUID,
        *,
        include_deleted: bool = False,
    ) -> Optional[QuestionBank]:
        """Fetch a question without teacher scope narrowing.

        Used internally by fork() and approve() which need the actual row
        regardless of subject assignment.
        """
        stmt = select(QuestionBank).where(
            QuestionBank.id == question_id,
            or_(
                QuestionBank.org_id == self._org_id,
                QuestionBank.org_id.is_(None),
            ),
        )
        if not include_deleted:
            stmt = stmt.where(QuestionBank.deleted_at.is_(None))
        result = await self._session.execute(stmt)
        return result.scalar_one_or_none()

    # ------------------------------------------------------------------
    # Write operations
    # ------------------------------------------------------------------

    async def create_question(
        self,
        *,
        qtype: str,
        marks: decimal.Decimal,
        difficulty: str,
        language: str,
        stem: str,
        source: str,
        scope: str,
        org_id: Optional[uuid.UUID],
        created_by: Optional[uuid.UUID] = None,
        bloom: Optional[str] = None,
        board_id: Optional[uuid.UUID] = None,
        class_id: Optional[uuid.UUID] = None,
        subject_id: Optional[uuid.UUID] = None,
        chapter_id: Optional[uuid.UUID] = None,
        options: Optional[Any] = None,
        answer: Optional[Any] = None,
        rubric: Optional[Any] = None,
        learning_outcome_ids: Optional[list[uuid.UUID]] = None,
        competency_ids: Optional[list[uuid.UUID]] = None,
        meta: Optional[dict[str, Any]] = None,
        prompt_version_id: Optional[uuid.UUID] = None,
        near_duplicate_threshold: float = 0.80,
        near_duplicate_limit: int = 5,
    ) -> QuestionCreateRepositoryResult:
        """Insert a first-version question row.

        Before the INSERT, serialises checks for the same hash, rejects an exact
        duplicate, and collects current near-duplicate candidates.  This method
        is used only for first-version creation.  ``fork()`` intentionally does
        not call it, so BR-8 versions may inherit content from their own lineage.

        Dedup domains follow doc 06 section 11.4: org-scoped questions compare
        within their organization, while global questions compare within the
        platform-global bank.  Both checks are narrowed to the same subject, as
        specified by the ingestion dedup contract.  Soft-deleted rows do not
        participate.
        """
        content_hash = _compute_content_hash(qtype, stem, options, answer, rubric)

        # Serialise first-version checks within one scope/subject domain.  A lock
        # keyed only by content_hash would protect exact collisions but allow two
        # differently-hashed near duplicates to check concurrently, miss each
        # other's uncommitted INSERT, and both return without a warning.  The
        # coarser key makes both exact rejection and advisory near-match results
        # deterministic.  It is transaction-scoped (therefore PgBouncer-safe) and
        # released automatically on commit/rollback.
        dedup_domain = str(org_id) if org_id is not None else "global"
        dedup_lock_key = f"question:{dedup_domain}:{subject_id or 'no-subject'}"
        await self._session.execute(
            text(
                "SELECT pg_advisory_xact_lock("
                "hashtextextended(:dedup_lock_key, 0))"
            ),
            {"dedup_lock_key": dedup_lock_key},
        )

        dedup_scope = (
            QuestionBank.org_id.is_(None)
            if org_id is None
            else QuestionBank.org_id == org_id
        )
        same_subject = QuestionBank.subject_id.is_not_distinct_from(subject_id)

        # Exact checks include historical (non-current) versions so starting a
        # new lineage cannot recreate content already preserved in bank history.
        # Prefer an org-owned current row when more than one pre-P2-T4 collision
        # already exists, making the returned candidate deterministic.
        exact_stmt = (
            select(QuestionBank)
            .where(
                dedup_scope,
                same_subject,
                QuestionBank.deleted_at.is_(None),
                QuestionBank.content_hash == content_hash,
            )
            .order_by(
                case(
                    (QuestionBank.org_id == org_id, 0),
                    else_=1,
                ),
                QuestionBank.is_current.desc(),
                QuestionBank.created_at.asc(),
                QuestionBank.id.asc(),
            )
            .limit(1)
        )
        exact_result = await self._session.execute(exact_stmt)
        exact_duplicate = exact_result.scalar_one_or_none()
        if exact_duplicate is not None:
            raise ExactDuplicateQuestionError(exact_duplicate)

        # Near matches are advisory and therefore only surface the current row
        # of each lineage.  pg_trgm ignores non-word characters and compares
        # case-insensitively in the stock PostgreSQL build; normalising the bound
        # input makes that contract explicit without wrapping the indexed column
        # in a function.  The ``%`` predicate can therefore use the existing
        # ``gin_trgm_ops`` index, while the explicit similarity comparison fixes
        # the inclusive threshold at the value returned to the client.
        normalised_stem = _normalise_stem(stem)
        await self._session.execute(
            text(
                "SELECT set_config("
                "'pg_trgm.similarity_threshold', :threshold, true)"
            ),
            {"threshold": str(near_duplicate_threshold)},
        )
        similarity = _stem_similarity(
            QuestionBank.stem,
            normalised_stem,
        ).label("similarity")
        near_stmt = (
            select(QuestionBank, similarity)
            .where(
                dedup_scope,
                same_subject,
                QuestionBank.deleted_at.is_(None),
                QuestionBank.is_current.is_(True),
                QuestionBank.content_hash != content_hash,
                QuestionBank.stem.op("%")(normalised_stem),
                similarity >= near_duplicate_threshold,
            )
            .order_by(similarity.desc(), QuestionBank.id.asc())
            .limit(near_duplicate_limit)
        )
        near_result = await self._session.execute(near_stmt)
        near_duplicates = tuple(
            NearDuplicateMatch(question=row[0], similarity=float(row[1]))
            for row in near_result.all()
        )

        kwargs: dict[str, Any] = {
            # Pass None explicitly for global rows.  Omitting the key would let
            # BaseRepository inject the request tenant and contradict scope.
            "org_id": org_id,
            "qtype": qtype,
            "marks": marks,
            "difficulty": difficulty,
            "language": language,
            "stem": stem,
            "source": source,
            "scope": scope,
            "content_hash": content_hash,
            "status": "draft",
            "version_number": 1,
            "is_current": True,
        }
        # Optional fields
        if bloom is not None:
            kwargs["bloom"] = bloom
        if board_id is not None:
            kwargs["board_id"] = board_id
        if class_id is not None:
            kwargs["class_id"] = class_id
        if subject_id is not None:
            kwargs["subject_id"] = subject_id
        if chapter_id is not None:
            kwargs["chapter_id"] = chapter_id
        if options is not None:
            kwargs["options"] = options
        if answer is not None:
            kwargs["answer"] = answer
        if rubric is not None:
            kwargs["rubric"] = rubric
        if learning_outcome_ids:
            kwargs["learning_outcome_ids"] = learning_outcome_ids
        if competency_ids:
            kwargs["competency_ids"] = competency_ids
        if meta:
            kwargs["meta"] = meta
        if created_by is not None:
            kwargs["created_by"] = created_by
        if prompt_version_id is not None:
            kwargs["prompt_version_id"] = prompt_version_id

        # Super() handles self-rooting: root_question_id = id after flush.
        question = await super().create(**kwargs)
        return QuestionCreateRepositoryResult(
            question=question,
            near_duplicates=near_duplicates,
        )

    async def approve_question(
        self,
        question_id: uuid.UUID,
        expected_version: int,
    ) -> QuestionBank:
        """Approve a draft question (draft → approved) with optimistic locking.

        Works for both org-scoped (org_id IS NOT NULL) and global (org_id IS NULL)
        questions.  BaseRepository.update() is NOT used here because it always adds
        ``WHERE org_id = :org_id``, which silently excludes global rows.

        Instead we issue a direct UPDATE that mirrors the scope of
        get_question_unscoped(): ``WHERE id=? AND (org_id=? OR org_id IS NULL)``.
        RLS (migration 0031's ``global_question_super_admin_update`` WITH CHECK policy)
        is the actual write-gate for global rows; the application-layer super_admin
        check in bulk_service._process_one_bulk_row fires first and gives a clear
        per-row error before any DB write is attempted.

        :raises EntityNotFoundError: if not found in this org (or global).
        :raises OptimisticLockError: if row_version doesn't match.
        :raises ValueError: if question is not in draft status.
        """
        q = await self.get_question_unscoped(question_id)
        if q is None:
            raise EntityNotFoundError(entity="question_bank", entity_id=question_id)
        if q.status != "draft":
            raise ValueError(
                f"Only draft questions can be approved (current status: {q.status})"
            )
        if q.row_version != expected_version:
            raise OptimisticLockError(
                entity="question_bank",
                entity_id=question_id,
                expected_version=expected_version,
            )

        # Direct UPDATE: match the same org scope as get_question_unscoped() so that
        # global questions (org_id IS NULL) are reachable.
        stmt = (
            update(QuestionBank)
            .where(
                QuestionBank.id == question_id,
                or_(
                    QuestionBank.org_id == self._org_id,
                    QuestionBank.org_id.is_(None),
                ),
                QuestionBank.row_version == expected_version,
                QuestionBank.deleted_at.is_(None),
            )
            .values(
                status="approved",
                row_version=expected_version + 1,
                updated_at=func.now(),
            )
            .returning(QuestionBank)
        )
        result = await self._session.execute(stmt)
        updated = result.scalar_one_or_none()
        if updated is None:
            # Row vanished between the pre-flight SELECT and the UPDATE
            # (concurrent modification); treat as optimistic-lock failure.
            raise OptimisticLockError(
                entity="question_bank",
                entity_id=question_id,
                expected_version=expected_version,
            )
        return updated

    async def transition_status_unscoped(
        self,
        question_id: uuid.UUID,
        expected_version: int,
        target_status: str,
    ) -> QuestionBank:
        """Transition a question to any target status (reject, archive, …) with optimistic locking.

        Works for both org-scoped (org_id IS NOT NULL) and global (org_id IS NULL)
        questions.  BaseRepository.update() is NOT used here because it always adds
        ``WHERE org_id = :org_id``, which silently excludes global rows — the same
        bug that was fixed in approve_question().

        Exact WHERE clause:
            id = :question_id
            AND (org_id = :self._org_id OR org_id IS NULL)
            AND row_version = :expected_version      ← optimistic lock
            AND deleted_at IS NULL

        RLS (migration 0031's ``global_question_super_admin_update`` WITH CHECK policy)
        is the actual write-gate for global rows.  The application-layer super_admin
        gate in bulk_service._process_one_bulk_row fires before any DB write.

        :raises EntityNotFoundError: if not found (org-scoped or global).
        :raises OptimisticLockError: if row_version doesn't match (pre-flight check
            AND the UPDATE WHERE clause — both guard against the same race).
        """
        q = await self.get_question_unscoped(question_id)
        if q is None:
            raise EntityNotFoundError(entity="question_bank", entity_id=question_id)
        if q.row_version != expected_version:
            raise OptimisticLockError(
                entity="question_bank",
                entity_id=question_id,
                expected_version=expected_version,
            )

        stmt = (
            update(QuestionBank)
            .where(
                QuestionBank.id == question_id,
                or_(
                    QuestionBank.org_id == self._org_id,
                    QuestionBank.org_id.is_(None),
                ),
                QuestionBank.row_version == expected_version,   # optimistic lock
                QuestionBank.deleted_at.is_(None),
            )
            .values(
                status=target_status,
                row_version=expected_version + 1,
                updated_at=func.now(),
            )
            .returning(QuestionBank)
        )
        result = await self._session.execute(stmt)
        updated = result.scalar_one_or_none()
        if updated is None:
            # Row vanished between the pre-flight SELECT and the UPDATE
            # (concurrent modification); treat as optimistic-lock failure.
            raise OptimisticLockError(
                entity="question_bank",
                entity_id=question_id,
                expected_version=expected_version,
            )
        return updated

    async def fork(
        self,
        parent: QuestionBank,
        *,
        caller_version: int,
        stem: Optional[str] = None,
        options: Optional[Any] = None,
        answer: Optional[Any] = None,
        rubric: Optional[Any] = None,
        marks: Optional[decimal.Decimal] = None,
        difficulty: Optional[str] = None,
        bloom: Optional[str] = None,
        language: Optional[str] = None,
        board_id: Optional[uuid.UUID] = None,
        class_id: Optional[uuid.UUID] = None,
        subject_id: Optional[uuid.UUID] = None,
        chapter_id: Optional[uuid.UUID] = None,
        learning_outcome_ids: Optional[list[uuid.UUID]] = None,
        competency_ids: Optional[list[uuid.UUID]] = None,
        meta: Optional[dict[str, Any]] = None,
        created_by: Optional[uuid.UUID] = None,
    ) -> QuestionBank:
        """BR-8 fork-on-edit: INSERT a new version, flip old version's is_current.

        The database's partial unique index ``uq_question_bank_root_question_id_current
        WHERE is_current`` enforces exactly-one-current within the same flush — if
        the UPDATE in step 2 fails, the index violation surfaces before commit.

        The BEFORE UPDATE trigger (migration 0020) allows updating ``is_current`` on
        an approved row — it's explicitly in the _MUTABLE_ON_APPROVED set.

        :param parent: the approved question being superseded.
        :param caller_version: the caller's expected row_version of parent (optimistic lock).
        :raises ValueError: if parent is not approved.
        :raises OptimisticLockError: if caller_version != parent.row_version.
        """
        if parent.status != "approved":
            raise ValueError(
                f"fork() requires an approved parent (current status: {parent.status}). "
                "Draft questions should be updated in place."
            )
        if parent.row_version != caller_version:
            raise OptimisticLockError(
                entity="question_bank",
                entity_id=parent.id,
                expected_version=caller_version,
            )

        # --- Build the new version's content (inherit parent values for omitted fields)
        new_stem = stem if stem is not None else parent.stem
        new_options = options if options is not None else parent.options
        new_answer = answer if answer is not None else parent.answer
        new_rubric = rubric if rubric is not None else parent.rubric
        new_marks = marks if marks is not None else parent.marks
        new_difficulty = difficulty if difficulty is not None else parent.difficulty
        new_bloom = bloom if bloom is not None else parent.bloom
        new_language = language if language is not None else parent.language
        new_board_id = board_id if board_id is not None else parent.board_id
        new_class_id = class_id if class_id is not None else parent.class_id
        new_subject_id = subject_id if subject_id is not None else parent.subject_id
        new_chapter_id = chapter_id if chapter_id is not None else parent.chapter_id
        new_lo_ids = learning_outcome_ids if learning_outcome_ids is not None else parent.learning_outcome_ids
        new_comp_ids = competency_ids if competency_ids is not None else parent.competency_ids
        new_meta = meta if meta is not None else parent.meta

        new_content_hash = _compute_content_hash(
            parent.qtype, new_stem, new_options, new_answer, new_rubric
        )

        # Step 1: Flip parent's is_current → False BEFORE inserting the new row.
        # This must happen first so that the partial unique index
        # ``uq_question_bank_root_question_id_current WHERE is_current`` does not
        # see two rows with is_current=True simultaneously at flush time.
        # The trigger (migration 0020) allows this: is_current is in the mutable set.
        stmt = (
            update(QuestionBank)
            .where(QuestionBank.id == parent.id)
            .values(
                is_current=False,
                updated_at=func.now(),
                row_version=parent.row_version + 1,
            )
        )
        await self._session.execute(stmt)

        # Step 2: INSERT new version row (is_current=True) and flush both together.
        # Note: we call session.add() directly (not super().create()) because this IS a
        # forked version (previous_version_id is set), so we inherit root_question_id.
        new_q = QuestionBank(
            root_question_id=parent.root_question_id,
            previous_version_id=parent.id,
            version_number=parent.version_number + 1,
            is_current=True,
            org_id=parent.org_id,
            scope=parent.scope,
            source=parent.source,
            board_id=new_board_id,
            class_id=new_class_id,
            subject_id=new_subject_id,
            chapter_id=new_chapter_id,
            qtype=parent.qtype,
            marks=new_marks,
            difficulty=new_difficulty,
            bloom=new_bloom,
            language=new_language,
            stem=new_stem,
            options=new_options,
            answer=new_answer,
            rubric=new_rubric,
            learning_outcome_ids=new_lo_ids,
            competency_ids=new_comp_ids,
            meta=new_meta,
            content_hash=new_content_hash,
            status="draft",
            created_by=created_by or parent.created_by,
            prompt_version_id=parent.prompt_version_id,
        )
        self._session.add(new_q)
        # Single flush: clears old is_current and assigns new row's id in one round-trip.
        await self._session.flush()

        return new_q

    async def update_question(
        self,
        question_id: uuid.UUID,
        expected_version: int,
        **fields: Any,
    ) -> QuestionBank:
        """In-place update for *draft* questions with optimistic locking.

        If content fields change, recomputes content_hash.
        """
        # Fetch current to get existing content for hash recomputation
        q = await self.get_question_unscoped(question_id)
        if q is None:
            raise EntityNotFoundError(entity="question_bank", entity_id=question_id)
        if q.status == "approved":
            raise ValueError(
                "Cannot update an approved question in place — use fork() instead."
            )

        # Recompute hash if any content field changed
        content_fields = ("stem", "options", "answer", "rubric", "qtype")
        if any(k in fields for k in content_fields):
            new_stem = fields.get("stem", q.stem)
            new_options = fields.get("options", q.options)
            new_answer = fields.get("answer", q.answer)
            new_rubric = fields.get("rubric", q.rubric)
            fields["content_hash"] = _compute_content_hash(
                q.qtype, new_stem, new_options, new_answer, new_rubric
            )

        return await self.update(question_id, expected_version, **fields)

    async def soft_delete_question(
        self,
        question_id: uuid.UUID,
        expected_version: int,
    ) -> None:
        """Soft-delete a question (ADR-004: no hard DELETE in application code)."""
        q = await self.get_question_unscoped(question_id)
        if q is None:
            raise EntityNotFoundError(entity="question_bank", entity_id=question_id)
        if q.row_version != expected_version:
            raise OptimisticLockError(
                entity="question_bank",
                entity_id=question_id,
                expected_version=expected_version,
            )
        # Use the base class soft_delete which sets deleted_at
        await self.soft_delete(question_id)

    async def get_lineage(
        self,
        root_question_id: uuid.UUID,
        *,
        subject_ids: Optional[tuple[uuid.UUID, ...]] = None,
    ) -> Sequence[QuestionBank]:
        """Return visible versions in a lineage, oldest first.

        ``subject_ids`` has the same meaning as it does on direct question reads:
        ``None`` is an unrestricted admin scope, while an empty tuple fails closed
        for a teacher with no program assignments.  Filtering every version also
        prevents a lineage whose curriculum placement changed over time from
        leaking an out-of-scope historical row.
        """
        if subject_ids is not None and len(subject_ids) == 0:
            return []

        predicates = [
            QuestionBank.root_question_id == root_question_id,
            or_(
                QuestionBank.org_id == self._org_id,
                QuestionBank.org_id.is_(None),
            ),
            QuestionBank.deleted_at.is_(None),
        ]
        if subject_ids is not None:
            predicates.append(QuestionBank.subject_id.in_(subject_ids))

        stmt = (
            select(QuestionBank)
            .where(*predicates)
            .order_by(QuestionBank.version_number.asc())
        )
        result = await self._session.execute(stmt)
        return result.scalars().all()
