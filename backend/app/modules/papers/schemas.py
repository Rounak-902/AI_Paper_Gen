"""HTTP contracts for initial paper generation and lock-preserving regeneration."""

from __future__ import annotations

import uuid
import datetime as dt
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator
from pydantic.alias_generators import to_camel

from app.modules.feasibility.schemas import CompromiseOut, CoverageFactOut, FeasibilityRequest


class PaperMutationModel(BaseModel):
    model_config = ConfigDict(
        alias_generator=to_camel,
        populate_by_name=True,
        extra="forbid",
    )


class GeneratePaperRequest(PaperMutationModel):
    """Paper metadata plus the canonical, shared feasibility blueprint contract."""

    title: str = Field(min_length=1, max_length=500)
    duration_minutes: int = Field(ge=1, le=1440)
    blueprint: FeasibilityRequest

    @field_validator("title")
    @classmethod
    def normalize_title(cls, value: str) -> str:
        normalized = value.strip()
        if not normalized:
            raise ValueError("title must not be blank")
        return normalized


class GenerationAccepted(PaperMutationModel):
    paper_id: uuid.UUID
    job_id: uuid.UUID
    outbox_id: uuid.UUID
    status: str


class PaperCoverageReport(PaperMutationModel):
    """Final solver facts read verbatim from an immutable paper version."""

    paper_id: uuid.UUID
    version_id: uuid.UUID
    solver_outcome: str
    coverage: list[CoverageFactOut]
    compromises: list[CompromiseOut]
    source: Literal["persisted-final-exact-selection"] = "persisted-final-exact-selection"


class PaperRealtimeSlotState(PaperMutationModel):
    """One durable assembly checkpoint projected for reconnect reconciliation."""

    slot_id: str
    section_id: str
    position: int
    status: Literal["pending", "running", "completed", "failed"]
    attempt_count: int
    question: dict[str, Any] | None = None
    error: str | None = None


class PaperRealtimeState(PaperMutationModel):
    """Durable paper state used after an SSE gap and by polling fallback."""

    paper_id: uuid.UUID
    run_id: uuid.UUID | None = None
    status: Literal["queued", "planned", "running", "finalizing", "completed", "failed"]
    version_id: uuid.UUID | None = None
    sequence: int = Field(ge=0)
    filled_slots: int = Field(ge=0)
    total_slots: int = Field(ge=0)
    slots: list[PaperRealtimeSlotState]


class SavePaperDraftRequest(PaperMutationModel):
    """Whole-buffer autosave guarded by the last acknowledged draft revision."""

    expected_revision: int | None = Field(default=None, ge=1)
    document: dict[str, Any]


class PaperDraftResource(PaperMutationModel):
    draft_id: uuid.UUID
    paper_id: uuid.UUID | None
    revision: int
    document: dict[str, Any]
    updated_at: dt.datetime


class RegenerationRequest(PaperMutationModel):
    request_id: uuid.UUID
    source_version_id: uuid.UUID
    target_slot_ids: list[str] = Field(min_length=1, max_length=200)
    locked_slot_ids: list[str] = Field(default_factory=list, max_length=200)

    @model_validator(mode="after")
    def validate_slot_sets(self) -> "RegenerationRequest":
        if len(self.target_slot_ids) != len(set(self.target_slot_ids)):
            raise ValueError("target_slot_ids must not contain duplicates")
        if len(self.locked_slot_ids) != len(set(self.locked_slot_ids)):
            raise ValueError("locked_slot_ids must not contain duplicates")
        overlap = set(self.target_slot_ids) & set(self.locked_slot_ids)
        if overlap:
            raise ValueError("locked slots cannot be regeneration targets")
        return self


class RegenerationAccepted(PaperMutationModel):
    job_id: uuid.UUID
    run_id: uuid.UUID
    paper_id: uuid.UUID
    source_version_id: uuid.UUID
    target_slot_ids: list[str]
    locked_slot_ids: list[str]
    status: str
