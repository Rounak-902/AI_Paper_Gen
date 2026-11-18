"""Facade for the read-only P3-T1 retrieval pipeline."""

from __future__ import annotations

import uuid
from typing import Optional, Sequence

from app.core.config import Settings
from app.modules.retrieval import schemas
from app.modules.retrieval.stages import (
    CandidateSource,
    CoverageOptimizerStage,
    DeduplicationStage,
    FinalSelectionStage,
    FtsTrigramStage,
    HybridRankStage,
    HybridWeights,
    MetadataFilterStage,
    VectorScorer,
    VectorSearchStage,
)


class RetrievalService:
    """Run each documented retrieval stage behind a stable caller contract.

    The service receives an already-scoped read adapter; it never opens, commits,
    or rolls back a database connection.  ``select_candidates`` is the ADR-007
    single-slot contract.  ``select_for_blueprint`` is the batch form needed by the
    coverage optimizer, which the same architecture says must see all slots.
    """

    def __init__(
        self,
        *,
        source: CandidateSource,
        top_k: int,
        dedup_threshold: float,
        weights: HybridWeights,
        vector_enabled: bool = False,
        vector_scorer: Optional[VectorScorer] = None,
    ) -> None:
        if top_k < 1:
            raise ValueError("top_k must be positive")
        if not 0.0 <= dedup_threshold <= 1.0:
            raise ValueError("dedup_threshold must be in [0, 1]")

        self.top_k = top_k
        self.metadata_filter = MetadataFilterStage(source)
        self.fts_trigram = FtsTrigramStage(source)
        self.vector_search = VectorSearchStage(
            enabled=vector_enabled,
            scorer=vector_scorer,
        )
        self.hybrid_rank = HybridRankStage(weights)
        self.deduplication = DeduplicationStage(
            source,
            threshold=dedup_threshold,
        )
        self.coverage_optimizer = CoverageOptimizerStage()
        self.final_selection = FinalSelectionStage()

    @classmethod
    def from_settings(
        cls,
        *,
        source: CandidateSource,
        settings: Settings,
        vector_scorer: Optional[VectorScorer] = None,
    ) -> "RetrievalService":
        """Build the pipeline entirely from deployment configuration."""

        return cls(
            source=source,
            top_k=settings.AI_TOP_K_RETRIEVAL,
            # Retrieval section 6.1 specifies the strict 0.95 MVP boundary;
            # DEDUP_SIMILARITY_THRESHOLD is the existing configurable 0.95 value.
            dedup_threshold=settings.DEDUP_SIMILARITY_THRESHOLD,
            weights=HybridWeights(
                metadata=settings.AI_HYBRID_WEIGHT_METADATA,
                fts=settings.AI_HYBRID_WEIGHT_FTS,
                trigram=settings.AI_HYBRID_WEIGHT_TRIGRAM,
                vector=settings.AI_HYBRID_WEIGHT_VECTOR,
            ),
            vector_enabled=settings.AI_VECTOR_SEARCH_ENABLED,
            vector_scorer=vector_scorer,
        )

    async def select_candidates(
        self,
        slot: schemas.BlueprintSlot,
        *,
        top_k: Optional[int] = None,
        chapter_coverage: Sequence[
            schemas.ChapterCoverageRequirement
        ] = (),
        outcome_coverage: Sequence[
            schemas.OutcomeCoverageRequirement
        ] = (),
        excluded_question_ids: Sequence[uuid.UUID] = (),
    ) -> schemas.SlotRetrievalResult:
        """Return ranked candidates for one slot without changing its caller shape."""

        request = schemas.BlueprintRetrievalRequest(
            slots=[slot],
            chapter_coverage=list(chapter_coverage),
            outcome_coverage=list(outcome_coverage),
            excluded_question_ids=list(excluded_question_ids),
            top_k_per_slot=top_k,
        )
        return (await self.select_for_blueprint(request)).slots[0]

    async def select_for_blueprint(
        self,
        request: schemas.BlueprintRetrievalRequest,
    ) -> schemas.BlueprintRetrievalResult:
        """Run the complete pipeline over every slot in one blueprint batch."""

        top_k = request.top_k_per_slot or self.top_k
        if top_k < 1:
            raise ValueError("top_k_per_slot must be positive")

        metadata_counts: dict[str, int] = {}
        ranked_pools: dict[str, tuple[RankedCandidate, ...]] = {}
        global_exclusions = tuple(request.excluded_question_ids)

        for slot in request.slots:
            exclusions = tuple(
                dict.fromkeys((*global_exclusions, *slot.excluded_question_ids))
            )
            metadata = await self.metadata_filter.run(slot, exclusions)
            metadata_counts[slot.slot_id] = len(metadata)
            lexical = await self.fts_trigram.run(slot, metadata)
            vector_scored = await self.vector_search.run(slot, lexical)
            ranked_pools[slot.slot_id] = self.hybrid_rank.run(vector_scored)

        deduplicated, duplicate_counts = await self.deduplication.run(ranked_pools)
        optimized, coverage_counts = self.coverage_optimizer.run(
            pools=deduplicated,
            request=request,
            top_k=top_k,
        )
        slot_results = self.final_selection.run(
            pools=optimized,
            request=request,
            top_k=top_k,
            metadata_counts=metadata_counts,
            duplicate_counts=duplicate_counts,
        )

        return schemas.BlueprintRetrievalResult(
            stages=[
                schemas.PipelineStageTrace(name="metadata_filter", state="active"),
                schemas.PipelineStageTrace(name="fts_trigram", state="active"),
                schemas.PipelineStageTrace(
                    name="vector_search",
                    state="active" if self.vector_search.enabled else "disabled",
                ),
                schemas.PipelineStageTrace(name="hybrid_rank", state="active"),
                schemas.PipelineStageTrace(name="deduplication", state="active"),
                schemas.PipelineStageTrace(
                    name="coverage_optimizer", state="active"
                ),
                schemas.PipelineStageTrace(name="final_selection", state="active"),
            ],
            slots=slot_results,
            coverage=[
                schemas.CoverageResult(
                    dimension=count.dimension,
                    value_id=count.target_id,
                    weight=count.weight,
                    minimum_candidates=count.minimum_candidates,
                    selected_candidates=count.selected_count,
                    satisfied=count.selected_count >= count.minimum_candidates,
                )
                for count in coverage_counts
            ],
            vector_enabled=self.vector_search.enabled,
        )


__all__ = ["RetrievalService"]
