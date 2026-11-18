"""HTTP contract for the synchronous P4-T2 feasibility gate."""

from __future__ import annotations

import decimal
import uuid
from typing import ClassVar, Literal, Optional

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator
from pydantic.alias_generators import to_camel

from app.modules.questions.schemas import (
    QUESTION_BLOOM_LEVELS,
    QUESTION_DIFFICULTIES,
    QUESTION_SOURCES,
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
Outcome = Literal["feasible", "feasible-with-compromise", "infeasible"]


class FeasibilityModel(BaseModel):
    model_config = ConfigDict(
        alias_generator=to_camel,
        populate_by_name=True,
        extra="forbid",
    )


class MixTargetIn(FeasibilityModel):
    target_percent: decimal.Decimal = Field(ge=0, le=100)
    tolerance_percentage_points: decimal.Decimal = Field(default=0, ge=0, le=100)


class ChapterCoverageIn(FeasibilityModel):
    chapter_id: uuid.UUID
    minimum_percent: decimal.Decimal = Field(gt=0, le=100)
    tolerance_percentage_points: decimal.Decimal = Field(default=0, ge=0, le=100)


class LearningOutcomeCoverageIn(FeasibilityModel):
    learning_outcome_id: uuid.UUID
    minimum_percent: decimal.Decimal = Field(gt=0, le=100)
    tolerance_percentage_points: decimal.Decimal = Field(default=0, ge=0, le=100)


class SectionIn(FeasibilityModel):
    id: str = Field(min_length=1, max_length=100)
    question_count: int = Field(ge=1, le=200)
    marks_per_question: decimal.Decimal = Field(gt=0, max_digits=4, decimal_places=1)
    allowed_question_types: list[str] = Field(min_length=1)
    chapter_ids: list[uuid.UUID] = Field(default_factory=list)

    @field_validator("allowed_question_types")
    @classmethod
    def validate_question_types(cls, values: list[str]) -> list[str]:
        if len(set(values)) != len(values):
            raise ValueError("allowed_question_types must not contain duplicates")
        invalid = set(values) - set(QUESTION_TYPES)
        if invalid:
            raise ValueError(f"unknown question types: {sorted(invalid)}")
        return values

    @field_validator("chapter_ids")
    @classmethod
    def unique_chapters(cls, values: list[uuid.UUID]) -> list[uuid.UUID]:
        if len(set(values)) != len(values):
            raise ValueError("section chapter_ids must not contain duplicates")
        return values


class FeasibilityRequest(FeasibilityModel):
    require_balanced_sections: ClassVar[bool] = True
    board_id: uuid.UUID
    class_id: uuid.UUID
    subject_id: uuid.UUID
    curriculum_version_id: uuid.UUID
    chapter_ids: list[uuid.UUID] = Field(min_length=1)
    language: str = Field(default="en", min_length=2, max_length=20)
    total_marks: decimal.Decimal = Field(gt=0, max_digits=5, decimal_places=1)
    sections: list[SectionIn] = Field(min_length=1, max_length=50)
    difficulty_mix: dict[str, MixTargetIn] = Field(default_factory=dict)
    source_mix: dict[str, MixTargetIn] = Field(default_factory=dict)
    bloom_mix: dict[str, MixTargetIn] = Field(default_factory=dict)
    chapter_coverage: list[ChapterCoverageIn] = Field(default_factory=list)
    learning_outcome_coverage: list[LearningOutcomeCoverageIn] = Field(
        default_factory=list
    )
    generation_mode: GenerationMode = "mixed"
    textbook_floor_percent: decimal.Decimal = Field(default=70, ge=0, le=100)

    @model_validator(mode="after")
    def validate_blueprint(self) -> "FeasibilityRequest":
        if len(set(self.chapter_ids)) != len(self.chapter_ids):
            raise ValueError("chapter_ids must not contain duplicates")
        section_ids = [section.id for section in self.sections]
        if len(set(section_ids)) != len(section_ids):
            raise ValueError("section ids must be unique")
        requested_chapters = set(self.chapter_ids)
        for section in self.sections:
            if set(section.chapter_ids) - requested_chapters:
                raise ValueError("section chapter_ids must be in blueprint chapter_ids")
        coverage_ids = [entry.chapter_id for entry in self.chapter_coverage]
        if len(set(coverage_ids)) != len(coverage_ids):
            raise ValueError("chapter_coverage must not repeat a chapter")
        if set(coverage_ids) - requested_chapters:
            raise ValueError("chapter_coverage chapters must be in chapter_ids")
        outcome_ids = [
            entry.learning_outcome_id for entry in self.learning_outcome_coverage
        ]
        if len(set(outcome_ids)) != len(outcome_ids):
            raise ValueError(
                "learning_outcome_coverage must not repeat a learning outcome"
            )

        section_marks = sum(
            (
                decimal.Decimal(section.question_count)
                * section.marks_per_question
                for section in self.sections
            ),
            decimal.Decimal(0),
        )
        if self.require_balanced_sections and section_marks != self.total_marks:
            raise ValueError(
                f"section marks ({section_marks}) must equal total_marks ({self.total_marks})"
            )

        vocabularies = (
            ("difficulty_mix", self.difficulty_mix, set(QUESTION_DIFFICULTIES)),
            ("source_mix", self.source_mix, set(QUESTION_SOURCES)),
            ("bloom_mix", self.bloom_mix, set(QUESTION_BLOOM_LEVELS)),
        )
        for name, mix, vocabulary in vocabularies:
            invalid = set(mix) - vocabulary
            if invalid:
                raise ValueError(f"{name} has unknown values: {sorted(invalid)}")
            if mix and sum(
                (entry.target_percent for entry in mix.values()), decimal.Decimal(0)
            ) != decimal.Decimal(100):
                raise ValueError(f"{name} target_percent values must sum to 100")
        return self


class FeasibilityDraftRequest(FeasibilityRequest):
    """Builder check payload that may still need its section shape auto-fitted."""

    require_balanced_sections: ClassVar[bool] = False


class AssignmentOut(FeasibilityModel):
    question_id: uuid.UUID
    section_id: str


class CompromiseOut(FeasibilityModel):
    tier: Literal[
        "difficulty",
        "source",
        "bloom",
        "chapter_coverage",
        "learning_outcome_coverage",
        "textbook_floor",
    ]
    constraint: str
    target: str
    actual: str
    deviation: str
    reason: str


class CoverageFactOut(FeasibilityModel):
    dimension: Literal[
        "difficulty",
        "source",
        "bloom",
        "chapter",
        "learning_outcome",
        "textbook_floor",
    ]
    target: str
    target_value: str
    actual: str
    satisfied: bool


class InfeasibilityReasonOut(FeasibilityModel):
    code: str
    constraint: str
    required: str
    available: str
    detail: str


class SolverMetricsOut(FeasibilityModel):
    engine: Literal["cp-sat"] = "cp-sat"
    solver_status: str
    solve_time_ms: float = Field(ge=0)
    model_build_time_ms: float = Field(ge=0)
    total_time_ms: float = Field(ge=0)
    time_budget_ms: int = Field(ge=1)
    within_solve_budget: bool
    optimal: bool
    timed_out: bool


class BankSnapshotOut(FeasibilityModel):
    eligible_question_count: int = Field(ge=0)
    selected_question_count: int = Field(ge=0)
    selected_marks: decimal.Decimal = Field(ge=0)


class FeasibilityResponse(FeasibilityModel):
    outcome: Outcome
    assignments: list[AssignmentOut]
    compromises: list[CompromiseOut]
    coverage: list[CoverageFactOut]
    reasons: list[InfeasibilityReasonOut]
    metrics: SolverMetricsOut
    bank_snapshot: BankSnapshotOut
    recommended_blueprint: Optional[FeasibilityRequest] = None
    recommendation_notes: list[str] = Field(default_factory=list)
    request_flow: Literal["synchronous-pre-enqueue"] = "synchronous-pre-enqueue"
    enqueue_performed: Literal[False] = False


__all__ = [
    "AssignmentOut",
    "BankSnapshotOut",
    "ChapterCoverageIn",
    "CompromiseOut",
    "CoverageFactOut",
    "FeasibilityRequest",
    "FeasibilityResponse",
    "InfeasibilityReasonOut",
    "LearningOutcomeCoverageIn",
    "MixTargetIn",
    "SectionIn",
    "SolverMetricsOut",
]
