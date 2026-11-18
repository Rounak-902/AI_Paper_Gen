"""Typed input/output contract for the P3 retrieval pipeline.

The architecture only promises "candidate ids + scores".  These models make that
contract concrete while retaining the component signals and coverage facts needed
to explain and test a final rank.  They are transport-neutral: P4 can instantiate
them directly; the non-production diagnostics route serializes the same objects.
"""

from __future__ import annotations

import decimal
import uuid
from typing import Literal, Optional

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator
from pydantic.alias_generators import to_camel

from app.modules.questions.schemas import (
    QUESTION_BLOOM_LEVELS,
    QUESTION_DIFFICULTIES,
    QUESTION_TYPES,
)


GenerationMode = Literal[
    "textual",
    "intext",
    "ai_generated",
    "mixed",
    "previous_year",
    "institute_bank",
    "global_bank",
]


class RetrievalModel(BaseModel):
    """Camel-case wire format with ordinary snake-case Python attributes."""

    model_config = ConfigDict(
        alias_generator=to_camel,
        populate_by_name=True,
        extra="forbid",
    )


class ChapterCoverageRequirement(RetrievalModel):
    chapter_id: uuid.UUID
    weight: float = Field(default=1.0, gt=0.0)
    minimum_candidates: int = Field(default=1, ge=0)


class OutcomeCoverageRequirement(RetrievalModel):
    learning_outcome_id: uuid.UUID
    weight: float = Field(default=1.0, gt=0.0)
    minimum_candidates: int = Field(default=0, ge=0)


class BlueprintSlot(RetrievalModel):
    """One assembler slot and the chapters from which it may be filled.

    A section with count N is represented by N slots.  This keeps the documented
    ``selectCandidates(slot)`` contract useful while the blueprint wrapper gives
    coverage optimization visibility of all slots together.
    """

    slot_id: str = Field(min_length=1, max_length=100)
    board_id: uuid.UUID
    class_id: uuid.UUID
    subject_id: uuid.UUID
    curriculum_version_id: uuid.UUID
    chapter_ids: list[uuid.UUID] = Field(min_length=1)
    qtype: str
    marks: decimal.Decimal = Field(gt=0)
    difficulty: str
    bloom: Optional[str] = None
    generation_mode: GenerationMode = "mixed"
    topic: Optional[str] = Field(default=None, max_length=500)
    keywords: list[str] = Field(default_factory=list, max_length=50)
    learning_outcome_ids: list[uuid.UUID] = Field(default_factory=list)
    excluded_question_ids: list[uuid.UUID] = Field(default_factory=list)
    required_candidates: int = Field(default=1, ge=1, le=100)

    @field_validator("qtype")
    @classmethod
    def validate_qtype(cls, value: str) -> str:
        if value not in QUESTION_TYPES:
            raise ValueError(f"qtype must be one of {QUESTION_TYPES}")
        return value

    @field_validator("difficulty")
    @classmethod
    def validate_difficulty(cls, value: str) -> str:
        if value not in QUESTION_DIFFICULTIES:
            raise ValueError(
                f"difficulty must be one of {QUESTION_DIFFICULTIES}"
            )
        return value

    @field_validator("bloom")
    @classmethod
    def validate_bloom(cls, value: Optional[str]) -> Optional[str]:
        if value is not None and value not in QUESTION_BLOOM_LEVELS:
            raise ValueError(f"bloom must be one of {QUESTION_BLOOM_LEVELS}")
        return value

    @field_validator("topic")
    @classmethod
    def normalize_topic(cls, value: Optional[str]) -> Optional[str]:
        if value is None:
            return None
        return value.strip() or None

    @field_validator("keywords")
    @classmethod
    def normalize_keywords(cls, values: list[str]) -> list[str]:
        normalized: list[str] = []
        seen: set[str] = set()
        for raw in values:
            value = " ".join(raw.split())
            key = value.casefold()
            if value and key not in seen:
                normalized.append(value)
                seen.add(key)
        return normalized

    @model_validator(mode="after")
    def require_unique_ids(self) -> "BlueprintSlot":
        if len(set(self.chapter_ids)) != len(self.chapter_ids):
            raise ValueError("chapter_ids must not contain duplicates")
        if len(set(self.learning_outcome_ids)) != len(self.learning_outcome_ids):
            raise ValueError("learning_outcome_ids must not contain duplicates")
        if len(set(self.excluded_question_ids)) != len(self.excluded_question_ids):
            raise ValueError("excluded_question_ids must not contain duplicates")
        return self

    @property
    def lexical_query(self) -> Optional[str]:
        """Build the explicit MVP lexical query from topic then keywords.

        The docs do not define this construction.  Terms are joined once and fed
        to P2-T6's ``plainto_tsquery``; the decision is surfaced in the P3 handoff
        rather than hidden in SQL.
        """

        parts = ([self.topic] if self.topic else []) + self.keywords
        return " ".join(parts) or None


class BlueprintRetrievalRequest(RetrievalModel):
    """A whole retrieval batch so coverage can observe every slot."""

    slots: list[BlueprintSlot] = Field(min_length=1, max_length=200)
    chapter_coverage: list[ChapterCoverageRequirement] = Field(default_factory=list)
    outcome_coverage: list[OutcomeCoverageRequirement] = Field(default_factory=list)
    excluded_question_ids: list[uuid.UUID] = Field(default_factory=list)
    top_k_per_slot: Optional[int] = Field(default=None, ge=1, le=100)

    @model_validator(mode="after")
    def validate_blueprint(self) -> "BlueprintRetrievalRequest":
        slot_ids = [slot.slot_id for slot in self.slots]
        if len(set(slot_ids)) != len(slot_ids):
            raise ValueError("slot_id values must be unique within a blueprint")

        chapter_ids = [item.chapter_id for item in self.chapter_coverage]
        if len(set(chapter_ids)) != len(chapter_ids):
            raise ValueError("chapter_coverage must not repeat a chapter")
        allowed_chapters = {
            chapter_id for slot in self.slots for chapter_id in slot.chapter_ids
        }
        unknown_chapters = set(chapter_ids) - allowed_chapters
        if unknown_chapters:
            raise ValueError(
                "chapter_coverage contains chapters not allowed by any slot"
            )

        outcome_ids = [item.learning_outcome_id for item in self.outcome_coverage]
        if len(set(outcome_ids)) != len(outcome_ids):
            raise ValueError("outcome_coverage must not repeat an outcome")
        allowed_outcomes = {
            outcome_id
            for slot in self.slots
            for outcome_id in slot.learning_outcome_ids
        }
        if set(outcome_ids) - allowed_outcomes:
            raise ValueError(
                "outcome_coverage contains outcomes not requested by any slot"
            )
        if len(set(self.excluded_question_ids)) != len(self.excluded_question_ids):
            raise ValueError("excluded_question_ids must not contain duplicates")
        return self


class PipelineStageTrace(RetrievalModel):
    name: Literal[
        "metadata_filter",
        "fts_trigram",
        "vector_search",
        "hybrid_rank",
        "deduplication",
        "coverage_optimizer",
        "final_selection",
    ]
    state: Literal["active", "disabled"]


class ScoredCandidate(RetrievalModel):
    question_id: uuid.UUID
    rank: int = Field(ge=1)
    pre_coverage_rank: int = Field(ge=1)
    score: float = Field(ge=0.0)
    metadata_score: float = Field(ge=0.0, le=1.0)
    fts_score: float = Field(ge=0.0, le=1.0)
    trigram_score: float = Field(ge=0.0, le=1.0)
    vector_score: float = Field(ge=0.0, le=1.0)
    coverage_priority: float = Field(ge=0.0)
    dedup_cluster_size: int = Field(default=1, ge=1)
    chapter_id: Optional[uuid.UUID]
    matched_chapter_ids: list[uuid.UUID]
    learning_outcome_ids: list[uuid.UUID]
    qtype: str
    marks: decimal.Decimal
    difficulty: str
    bloom: Optional[str]
    source: str
    scope: str
    stem: str


class SlotRetrievalResult(RetrievalModel):
    slot_id: str
    candidates: list[ScoredCandidate]
    metadata_candidate_count: int = Field(ge=0)
    duplicate_candidates_removed: int = Field(ge=0)
    required_candidates: int = Field(ge=1)
    gap: bool
    gap_reason: Optional[str] = None


class CoverageResult(RetrievalModel):
    """Availability inside the returned alternative pools.

    This is deliberately not a claim that a paper has been assembled: P4 owns
    final one-question-per-slot assignment and cross-slot uniqueness.
    """

    dimension: Literal["chapter", "learning_outcome"]
    value_id: uuid.UUID
    weight: float = Field(gt=0.0)
    minimum_candidates: int = Field(ge=0)
    selected_candidates: int = Field(
        ge=0,
        description="Unique eligible questions present across returned slot pools.",
    )
    satisfied: bool = Field(
        description="Whether the returned alternative pools meet minimumCandidates."
    )


class BlueprintRetrievalResult(RetrievalModel):
    stages: list[PipelineStageTrace]
    slots: list[SlotRetrievalResult]
    coverage: list[CoverageResult]
    vector_enabled: bool


__all__ = [
    "BlueprintRetrievalRequest",
    "BlueprintRetrievalResult",
    "BlueprintSlot",
    "ChapterCoverageRequirement",
    "CoverageResult",
    "GenerationMode",
    "OutcomeCoverageRequirement",
    "PipelineStageTrace",
    "ScoredCandidate",
    "SlotRetrievalResult",
]
