"""Typed transport-neutral pipeline inputs and audit results."""
from __future__ import annotations

import uuid
from dataclasses import dataclass, field
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field

from app.modules.ai.providers.schemas import LlmResponse
from app.modules.prompts.service import ResolvedPrompt
from app.modules.questions.schemas import QuestionCreateIn
from app.modules.retrieval.schemas import BlueprintSlot, SlotRetrievalResult
from packages.prompts.templates import GeneratedQuestion
from .retry import RetryEvent


class StrictModel(BaseModel):
    model_config = ConfigDict(extra="forbid", allow_inf_nan=False)


class CurriculumContext(StrictModel):
    board_id: uuid.UUID
    class_id: uuid.UUID
    subject_id: uuid.UUID
    curriculum_version_id: uuid.UUID
    chapter_ids: list[uuid.UUID] = Field(min_length=1)
    learning_outcome_ids: list[uuid.UUID] = Field(default_factory=list)
    description: str = Field(min_length=1)
    textbook_snippets: list[str] = Field(default_factory=list)
    # Explicit caller-vetted mapping: never guess eligibility from a type alone.
    template_refs: list[str] = Field(default_factory=list)


@dataclass(frozen=True)
class GenerationContext:
    slot: BlueprintSlot
    retrieval: SlotRetrievalResult
    curriculum: CurriculumContext
    language: str
    template: GeneratedQuestion | None


class GeneratedContent(QuestionCreateIn):
    """Question structure plus model claims which must be independently checked."""
    model_config = ConfigDict(extra="forbid", allow_inf_nan=False)
    language: str
    bloom: str
    board_id: uuid.UUID
    class_id: uuid.UUID
    subject_id: uuid.UUID
    chapter_id: uuid.UUID
    learning_outcome_ids: list[uuid.UUID]
    cited_question_ids: list[uuid.UUID] = Field(default_factory=list)
    cited_snippet_indices: list[int] = Field(default_factory=list)
    solution_steps: str | None = None
    solution_expression: str | None = None
    solution_units: str | None = None


class Classification(StrictModel):
    difficulty: Literal["easy", "medium", "hard"]
    bloom: Literal["remember", "understand", "apply", "analyze", "evaluate", "create"]
    confidence: float = Field(ge=0, le=1)
    curriculum_aligned: bool
    language_matches: bool
    grammar_flags: list[str]
    format_flags: list[str]
    qualitative_correctness_confidence: float = Field(ge=0, le=1)
    evidence: str = Field(min_length=1)


@dataclass(frozen=True)
class PipelinePrompts:
    generation: ResolvedPrompt
    classification: ResolvedPrompt


@dataclass(frozen=True)
class SimilarityResult:
    # Exact collisions raise P2-T4's existing conflict; trigram matches warn.
    warnings: tuple[Any, ...] = ()


@dataclass
class PipelineResult:
    branch: Literal["template", "llm"]
    question: QuestionCreateIn
    flags: list[str] = field(default_factory=list)
    similarity: SimilarityResult = field(default_factory=SimilarityResult)
    classification: Classification | None = None
    responses: list[LlmResponse] = field(default_factory=list)
    retries: list[RetryEvent] = field(default_factory=list)
    stages: list[str] = field(default_factory=list)
