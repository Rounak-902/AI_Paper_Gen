"""Concrete stages in the P3-T1 read-only retrieval pipeline."""

from __future__ import annotations

import math
import uuid
from dataclasses import dataclass, replace
from typing import Mapping, Optional, Protocol, Sequence

from app.modules.questions.schemas import QuestionOut
from app.modules.retrieval import schemas


@dataclass(frozen=True)
class LexicalSignal:
    question_id: uuid.UUID
    fts_score: float
    trigram_score: float


@dataclass(frozen=True)
class MetadataCandidate:
    """A bank question plus current-blueprint chapters it can satisfy.

    A question stored on a lineage ancestor keeps its historical ``chapter_id``.
    ``coverage_chapter_ids`` records the requested current-version descendants
    through which it became eligible, without rewriting that provenance.
    """

    question: QuestionOut
    coverage_chapter_ids: tuple[uuid.UUID, ...]


@dataclass(frozen=True)
class DuplicateEdge:
    left_question_id: uuid.UUID
    right_question_id: uuid.UUID
    exact: bool
    similarity: float


class CandidateSource(Protocol):
    """Read-side port backed by the P2 Question Bank service."""

    async def metadata_candidates(
        self,
        slot: schemas.BlueprintSlot,
        excluded_question_ids: tuple[uuid.UUID, ...],
    ) -> tuple[MetadataCandidate, ...]: ...

    async def lexical_signals(
        self,
        question_ids: tuple[uuid.UUID, ...],
        query_text: Optional[str],
    ) -> tuple[LexicalSignal, ...]: ...

    async def duplicate_edges(
        self,
        question_ids: tuple[uuid.UUID, ...],
        threshold: float,
    ) -> tuple[DuplicateEdge, ...]: ...


@dataclass(frozen=True)
class RankedCandidate:
    question: QuestionOut
    coverage_chapter_ids: tuple[uuid.UUID, ...] = ()
    metadata_score: float = 1.0
    fts_score: float = 0.0
    trigram_score: float = 0.0
    vector_score: float = 0.0
    hybrid_score: float = 0.0
    pre_coverage_rank: int = 1
    coverage_priority: float = 0.0
    dedup_cluster_size: int = 1


class MetadataFilterStage:
    """Apply every non-negotiable bank/scope/slot predicate first."""

    name = "metadata_filter"

    def __init__(self, source: CandidateSource) -> None:
        self._source = source

    async def run(
        self,
        slot: schemas.BlueprintSlot,
        excluded_question_ids: tuple[uuid.UUID, ...],
    ) -> tuple[RankedCandidate, ...]:
        candidates = await self._source.metadata_candidates(
            slot, excluded_question_ids
        )
        # Metadata is deliberately binary at MVP: every survivor satisfies every
        # hard predicate and receives 1.0; a non-match never reaches later stages.
        return tuple(
            RankedCandidate(
                question=candidate.question,
                coverage_chapter_ids=candidate.coverage_chapter_ids,
            )
            for candidate in candidates
        )


class FtsTrigramStage:
    """Attach P2-T6 FTS and P2-T4 trigram signals without changing the pool."""

    name = "fts_trigram"

    def __init__(self, source: CandidateSource) -> None:
        self._source = source

    async def run(
        self,
        slot: schemas.BlueprintSlot,
        candidates: tuple[RankedCandidate, ...],
    ) -> tuple[RankedCandidate, ...]:
        signals = await self._source.lexical_signals(
            tuple(candidate.question.id for candidate in candidates),
            slot.lexical_query,
        )
        by_id = {signal.question_id: signal for signal in signals}
        return tuple(
            replace(
                candidate,
                fts_score=by_id.get(
                    candidate.question.id,
                    LexicalSignal(candidate.question.id, 0.0, 0.0),
                ).fts_score,
                trigram_score=by_id.get(
                    candidate.question.id,
                    LexicalSignal(candidate.question.id, 0.0, 0.0),
                ).trigram_score,
            )
            for candidate in candidates
        )


class VectorScorer(Protocol):
    """Future ANN seam; implementing this does not change a retrieval caller."""

    async def score(
        self,
        slot: schemas.BlueprintSlot,
        candidates: Sequence[QuestionOut],
    ) -> Mapping[uuid.UUID, float]: ...


class VectorSearchStage:
    """Structurally present vector stage, disabled under ADR-007 at MVP."""

    name = "vector_search"

    def __init__(
        self,
        *,
        enabled: bool = False,
        scorer: Optional[VectorScorer] = None,
    ) -> None:
        if enabled and scorer is None:
            raise ValueError("an enabled vector stage requires a VectorScorer")
        self.enabled = enabled
        self._scorer = scorer

    async def run(
        self,
        slot: schemas.BlueprintSlot,
        candidates: tuple[RankedCandidate, ...],
    ) -> tuple[RankedCandidate, ...]:
        if not self.enabled:
            # Do not call an injected scorer.  This makes disabled mean disabled,
            # and is the executable no-op guarantee required by ADR-007.
            return tuple(replace(candidate, vector_score=0.0) for candidate in candidates)

        assert self._scorer is not None  # established by __init__
        raw_scores = await self._scorer.score(
            slot, tuple(candidate.question for candidate in candidates)
        )
        output: list[RankedCandidate] = []
        for candidate in candidates:
            score = float(raw_scores.get(candidate.question.id, 0.0))
            if not math.isfinite(score) or not 0.0 <= score <= 1.0:
                raise ValueError("vector scores must be finite values in [0, 1]")
            output.append(replace(candidate, vector_score=score))
        return tuple(output)


@dataclass(frozen=True)
class HybridWeights:
    metadata: float
    fts: float
    trigram: float
    vector: float

    def __post_init__(self) -> None:
        values = (self.metadata, self.fts, self.trigram, self.vector)
        if any(not math.isfinite(value) or value < 0 for value in values):
            raise ValueError("hybrid weights must be finite and non-negative")
        if not any(values):
            raise ValueError("at least one hybrid weight must be positive")


class HybridRankStage:
    """Compute the configured weighted sum and a deterministic relevance rank."""

    name = "hybrid_rank"

    def __init__(self, weights: HybridWeights) -> None:
        self.weights = weights

    def run(
        self, candidates: tuple[RankedCandidate, ...]
    ) -> tuple[RankedCandidate, ...]:
        scored = [
            replace(
                candidate,
                hybrid_score=(
                    self.weights.metadata * candidate.metadata_score
                    + self.weights.fts * candidate.fts_score
                    + self.weights.trigram * candidate.trigram_score
                    + self.weights.vector * candidate.vector_score
                ),
            )
            for candidate in candidates
        ]

        def recency(candidate: RankedCandidate) -> float:
            value = candidate.question.created_at
            timestamp = getattr(value, "timestamp", None)
            return float(timestamp()) if callable(timestamp) else 0.0

        scored.sort(
            key=lambda candidate: (
                -candidate.hybrid_score,
                -candidate.fts_score,
                -candidate.trigram_score,
                -recency(candidate),
                str(candidate.question.id),
            )
        )
        return tuple(
            replace(candidate, pre_coverage_rank=index)
            for index, candidate in enumerate(scored, start=1)
        )


class DeduplicationStage:
    """Cluster exact/near duplicates and preserve each cluster's best rank."""

    name = "deduplication"

    def __init__(self, source: CandidateSource, *, threshold: float) -> None:
        self._source = source
        self.threshold = threshold

    async def run(
        self,
        pools: Mapping[str, tuple[RankedCandidate, ...]],
    ) -> tuple[dict[str, tuple[RankedCandidate, ...]], dict[str, int]]:
        ids = tuple(
            dict.fromkeys(
                candidate.question.id
                for candidates in pools.values()
                for candidate in candidates
            )
        )
        edges = await self._source.duplicate_edges(ids, self.threshold)

        parent: dict[uuid.UUID, uuid.UUID] = {question_id: question_id for question_id in ids}

        def find(question_id: uuid.UUID) -> uuid.UUID:
            while parent[question_id] != question_id:
                parent[question_id] = parent[parent[question_id]]
                question_id = parent[question_id]
            return question_id

        def union(left: uuid.UUID, right: uuid.UUID) -> None:
            left_root = find(left)
            right_root = find(right)
            if left_root != right_root:
                if str(left_root) <= str(right_root):
                    parent[right_root] = left_root
                else:
                    parent[left_root] = right_root

        for edge in edges:
            if edge.left_question_id in parent and edge.right_question_id in parent:
                union(edge.left_question_id, edge.right_question_id)

        deduplicated: dict[str, tuple[RankedCandidate, ...]] = {}
        removed: dict[str, int] = {}
        for slot_id, candidates in pools.items():
            by_question = {
                candidate.question.id: candidate for candidate in candidates
            }
            clusters: dict[uuid.UUID, list[uuid.UUID]] = {}
            for question_id in by_question:
                clusters.setdefault(find(question_id), []).append(question_id)

            winners: set[uuid.UUID] = set()
            cluster_sizes: dict[uuid.UUID, int] = {}
            for members in clusters.values():
                winner = min(
                    members,
                    key=lambda question_id: (
                        -by_question[question_id].hybrid_score,
                        by_question[question_id].pre_coverage_rank,
                        str(question_id),
                    ),
                )
                winners.add(winner)
                cluster_sizes[winner] = len(members)

            kept = tuple(
                replace(
                    candidate,
                    dedup_cluster_size=cluster_sizes[candidate.question.id],
                )
                for candidate in candidates
                if candidate.question.id in winners
            )
            deduplicated[slot_id] = kept
            removed[slot_id] = len(candidates) - len(kept)
        return deduplicated, removed


@dataclass(frozen=True)
class CoverageCount:
    dimension: str
    target_id: uuid.UUID
    weight: float
    minimum_candidates: int
    selected_count: int


class CoverageOptimizerStage:
    """Balance final candidate pools against whole-blueprint coverage targets."""

    name = "coverage_optimizer"

    def run(
        self,
        *,
        pools: Mapping[str, tuple[RankedCandidate, ...]],
        request: schemas.BlueprintRetrievalRequest,
        top_k: int,
    ) -> tuple[dict[str, tuple[RankedCandidate, ...]], tuple[CoverageCount, ...]]:
        slot_order = [slot.slot_id for slot in request.slots]
        slots_by_id = {slot.slot_id: slot for slot in request.slots}
        selected: dict[str, list[RankedCandidate]] = {
            slot_id: [] for slot_id in slot_order
        }
        selected_ids: dict[str, set[uuid.UUID]] = {
            slot_id: set() for slot_id in slot_order
        }

        def unique_question_ids() -> set[uuid.UUID]:
            return {
                candidate.question.id
                for values in selected.values()
                for candidate in values
            }

        def matches_chapter(
            candidate: RankedCandidate, target_id: uuid.UUID
        ) -> bool:
            return target_id in (
                candidate.coverage_chapter_ids
                or (
                    (candidate.question.chapter_id,)
                    if candidate.question.chapter_id is not None
                    else ()
                )
            )

        def matches_outcome(
            candidate: RankedCandidate, target_id: uuid.UUID
        ) -> bool:
            return target_id in candidate.question.learning_outcome_ids

        def selected_match_ids(
            matcher,
            target_id: uuid.UUID,
            *,
            eligible_slot,
        ) -> set[uuid.UUID]:
            # The same alternative can appear in multiple compatible slots. Count
            # it once, and only through a slot that actually requested the target.
            matched_ids: set[uuid.UUID] = set()
            for slot_id, values in selected.items():
                if not eligible_slot(slots_by_id[slot_id], target_id):
                    continue
                matched_ids.update(
                    candidate.question.id
                    for candidate in values
                    if matcher(candidate, target_id)
                )
            return matched_ids

        def selected_count(
            matcher,
            target_id: uuid.UUID,
            *,
            eligible_slot,
        ) -> int:
            return len(
                selected_match_ids(
                    matcher,
                    target_id,
                    eligible_slot=eligible_slot,
                )
            )

        def chapter_count(target_id: uuid.UUID) -> int:
            return selected_count(
                matches_chapter,
                target_id,
                eligible_slot=lambda slot, value: value in slot.chapter_ids,
            )

        def outcome_count(target_id: uuid.UUID) -> int:
            return selected_count(
                matches_outcome,
                target_id,
                eligible_slot=lambda slot, value: value in slot.learning_outcome_ids,
            )

        def choose(candidate: RankedCandidate, slot_id: str, priority: float) -> None:
            selected[slot_id].append(
                replace(candidate, coverage_priority=max(0.0, priority))
            )
            selected_ids[slot_id].add(candidate.question.id)

        def minimum_gain(candidate: RankedCandidate, slot_id: str) -> float:
            """Reward every still-unmet minimum this unique question advances."""

            slot = slots_by_id[slot_id]
            gain = 0.0
            for requirement in request.chapter_coverage:
                already_counted = selected_match_ids(
                    matches_chapter,
                    requirement.chapter_id,
                    eligible_slot=lambda item, value: value in item.chapter_ids,
                )
                if (
                    requirement.chapter_id in slot.chapter_ids
                    and chapter_count(requirement.chapter_id)
                    < requirement.minimum_candidates
                    and matches_chapter(candidate, requirement.chapter_id)
                    and candidate.question.id not in already_counted
                ):
                    gain += 1.0 + requirement.weight
            for requirement in request.outcome_coverage:
                already_counted = selected_match_ids(
                    matches_outcome,
                    requirement.learning_outcome_id,
                    eligible_slot=lambda item, value: value
                    in item.learning_outcome_ids,
                )
                if (
                    requirement.learning_outcome_id in slot.learning_outcome_ids
                    and outcome_count(requirement.learning_outcome_id)
                    < requirement.minimum_candidates
                    and matches_outcome(
                        candidate, requirement.learning_outcome_id
                    )
                    and candidate.question.id not in already_counted
                ):
                    gain += 1.0 + requirement.weight
            return gain

        # Hard minima precede proportional balancing. All unmet requirements are
        # considered together so client list order cannot trump weight, and one
        # candidate can advance both a chapter and an outcome requirement.
        while True:
            choices: list[tuple[float, RankedCandidate, str]] = []
            for slot_id in slot_order:
                if len(selected[slot_id]) >= top_k:
                    continue
                for candidate in pools.get(slot_id, ()):
                    if candidate.question.id in selected_ids[slot_id]:
                        continue
                    gain = minimum_gain(candidate, slot_id)
                    if gain > 0.0:
                        choices.append((gain, candidate, slot_id))
            if not choices:
                break
            gain, candidate, slot_id = min(
                choices,
                key=lambda item: (
                    -item[0],
                    -item[1].hybrid_score,
                    len(selected[item[2]]),
                    item[1].pre_coverage_rank,
                    slot_order.index(item[2]),
                    str(item[1].question.id),
                ),
            )
            choose(candidate, slot_id, gain)

        chapter_weight_total = sum(item.weight for item in request.chapter_coverage)
        outcome_weight_total = sum(item.weight for item in request.outcome_coverage)

        def priority(candidate: RankedCandidate, slot_id: str) -> float:
            unique = unique_question_ids()
            slot = slots_by_id[slot_id]
            total_after_choice = len(unique) + int(candidate.question.id not in unique)
            value = 0.0
            if chapter_weight_total:
                for requirement in request.chapter_coverage:
                    if (
                        requirement.chapter_id in slot.chapter_ids
                        and matches_chapter(candidate, requirement.chapter_id)
                        and candidate.question.id
                        not in selected_match_ids(
                            matches_chapter,
                            requirement.chapter_id,
                            eligible_slot=lambda item, target: target
                            in item.chapter_ids,
                        )
                    ):
                        desired = (
                            total_after_choice
                            * requirement.weight
                            / chapter_weight_total
                        )
                        actual = chapter_count(requirement.chapter_id)
                        value += max(0.0, desired - actual)
            if outcome_weight_total:
                for requirement in request.outcome_coverage:
                    if (
                        requirement.learning_outcome_id
                        in slot.learning_outcome_ids
                        and requirement.learning_outcome_id
                        in candidate.question.learning_outcome_ids
                        and candidate.question.id
                        not in selected_match_ids(
                            matches_outcome,
                            requirement.learning_outcome_id,
                            eligible_slot=lambda item, target: target
                            in item.learning_outcome_ids,
                        )
                    ):
                        desired = (
                            total_after_choice
                            * requirement.weight
                            / outcome_weight_total
                        )
                        actual = outcome_count(requirement.learning_outcome_id)
                        value += max(0.0, desired - actual)
            return value

        # Fill every per-slot alternatives list from the highest current coverage
        # deficit, then hybrid relevance. Cross-slot overlap is intentional here:
        # P4's assembler owns the final one-question-per-paper assignment.
        while any(len(selected[slot_id]) < top_k for slot_id in slot_order):
            choices: list[tuple[float, RankedCandidate, str]] = []
            for slot_id in slot_order:
                if len(selected[slot_id]) >= top_k:
                    continue
                for candidate in pools.get(slot_id, ()):
                    if candidate.question.id not in selected_ids[slot_id]:
                        choices.append(
                            (priority(candidate, slot_id), candidate, slot_id)
                        )
            if not choices:
                break
            best_priority, best_candidate, best_slot = min(
                choices,
                key=lambda item: (
                    -item[0],
                    -item[1].hybrid_score,
                    len(selected[item[2]]),
                    item[1].pre_coverage_rank,
                    slot_order.index(item[2]),
                    str(item[1].question.id),
                ),
            )
            choose(best_candidate, best_slot, best_priority)

        counts: list[CoverageCount] = []
        for requirement in request.chapter_coverage:
            counts.append(
                CoverageCount(
                    dimension="chapter",
                    target_id=requirement.chapter_id,
                    weight=requirement.weight,
                    minimum_candidates=requirement.minimum_candidates,
                    selected_count=chapter_count(requirement.chapter_id),
                )
            )
        for requirement in request.outcome_coverage:
            counts.append(
                CoverageCount(
                    dimension="learning_outcome",
                    target_id=requirement.learning_outcome_id,
                    weight=requirement.weight,
                    minimum_candidates=requirement.minimum_candidates,
                    selected_count=outcome_count(requirement.learning_outcome_id),
                )
            )

        return (
            {slot_id: tuple(values) for slot_id, values in selected.items()},
            tuple(counts),
        )


class FinalSelectionStage:
    """Enforce per-slot top-K and materialize the assembler-facing contract."""

    name = "final_selection"

    def run(
        self,
        *,
        pools: Mapping[str, tuple[RankedCandidate, ...]],
        request: schemas.BlueprintRetrievalRequest,
        top_k: int,
        metadata_counts: Mapping[str, int],
        duplicate_counts: Mapping[str, int],
    ) -> tuple[schemas.SlotRetrievalResult, ...]:
        results: list[schemas.SlotRetrievalResult] = []
        for slot in request.slots:
            candidates = pools.get(slot.slot_id, ())[:top_k]
            output = [
                self._candidate_out(candidate, rank)
                for rank, candidate in enumerate(candidates, start=1)
            ]
            gap = len(output) < slot.required_candidates
            results.append(
                schemas.SlotRetrievalResult(
                    slot_id=slot.slot_id,
                    candidates=output,
                    metadata_candidate_count=metadata_counts[slot.slot_id],
                    duplicate_candidates_removed=duplicate_counts[slot.slot_id],
                    required_candidates=slot.required_candidates,
                    gap=gap,
                    gap_reason=(
                        "insufficient approved, current, non-duplicate candidates"
                        if gap
                        else None
                    ),
                )
            )
        return tuple(results)

    @staticmethod
    def _candidate_out(
        candidate: RankedCandidate,
        rank: int,
    ) -> schemas.ScoredCandidate:
        question = candidate.question
        return schemas.ScoredCandidate(
            question_id=question.id,
            rank=rank,
            pre_coverage_rank=candidate.pre_coverage_rank,
            score=candidate.hybrid_score,
            metadata_score=candidate.metadata_score,
            fts_score=candidate.fts_score,
            trigram_score=candidate.trigram_score,
            vector_score=candidate.vector_score,
            coverage_priority=candidate.coverage_priority,
            dedup_cluster_size=candidate.dedup_cluster_size,
            chapter_id=question.chapter_id,
            matched_chapter_ids=list(
                candidate.coverage_chapter_ids
                or ((question.chapter_id,) if question.chapter_id is not None else ())
            ),
            learning_outcome_ids=list(question.learning_outcome_ids),
            qtype=question.qtype,
            marks=question.marks,
            difficulty=question.difficulty,
            bloom=question.bloom,
            source=question.source,
            scope=question.scope,
            stem=question.stem,
        )


__all__ = [
    "CandidateSource",
    "CoverageCount",
    "CoverageOptimizerStage",
    "DeduplicationStage",
    "DuplicateEdge",
    "FinalSelectionStage",
    "FtsTrigramStage",
    "HybridRankStage",
    "HybridWeights",
    "LexicalSignal",
    "MetadataCandidate",
    "MetadataFilterStage",
    "RankedCandidate",
    "VectorScorer",
    "VectorSearchStage",
]
