"""HTTP contracts for version-bound paper approval rounds."""

from __future__ import annotations

import datetime as dt
import uuid
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, field_validator
from pydantic.alias_generators import to_camel


class ApprovalModel(BaseModel):
    model_config = ConfigDict(
        alias_generator=to_camel,
        populate_by_name=True,
        extra="forbid",
        from_attributes=True,
    )


class ApprovalSubmitIn(ApprovalModel):
    paper_export_artifact_id: uuid.UUID
    answer_key_export_artifact_id: uuid.UUID
    accept_coverage_report: Literal[True]


class ApprovalTransitionIn(ApprovalModel):
    row_version: int = Field(ge=1)
    comment: str | None = Field(default=None, max_length=10_000)

    @field_validator("comment")
    @classmethod
    def normalize_comment(cls, value: str | None) -> str | None:
        if value is None:
            return None
        normalized = value.strip()
        return normalized or None


class ApprovalCommentIn(ApprovalModel):
    slot_id: str | None = Field(default=None, max_length=200)
    body: str = Field(min_length=1, max_length=10_000)

    @field_validator("body")
    @classmethod
    def normalize_body(cls, value: str) -> str:
        normalized = value.strip()
        if not normalized:
            raise ValueError("body must not be blank")
        return normalized


class ApprovalRequestOut(ApprovalModel):
    id: uuid.UUID
    paper_id: uuid.UUID
    paper_version_id: uuid.UUID
    paper_export_artifact_id: uuid.UUID
    answer_key_export_artifact_id: uuid.UUID
    submitted_by: uuid.UUID
    review_started_by: uuid.UUID | None = None
    approver_id: uuid.UUID | None = None
    status: Literal["submitted", "review", "approved", "rejected"]
    separation_of_duties_required: bool
    coverage_report_sha256: str
    submitted_at: dt.datetime
    review_started_at: dt.datetime | None = None
    decided_at: dt.datetime | None = None
    decision_comment: str | None = None
    row_version: int


class ApprovalEventOut(ApprovalModel):
    id: uuid.UUID
    action: str
    actor_id: uuid.UUID
    from_status: str | None
    to_status: str
    comment: str | None
    occurred_at: dt.datetime


class ApprovalCommentOut(ApprovalModel):
    id: uuid.UUID
    created_by: uuid.UUID
    slot_id: str | None
    body: str
    created_at: dt.datetime


class ApprovalHistoryOut(ApprovalModel):
    request: ApprovalRequestOut
    events: list[ApprovalEventOut]
    comments: list[ApprovalCommentOut]


class SlotChangeOut(ApprovalModel):
    slot_id: str
    section_id: str | None = None
    changed_fields: list[str] = Field(default_factory=list)


class SectionChangeOut(ApprovalModel):
    section_id: str
    changed_fields: list[str] = Field(default_factory=list)


class PaperVersionDiffOut(ApprovalModel):
    paper_id: uuid.UUID
    base_version_id: uuid.UUID | None
    target_version_id: uuid.UUID
    basis: Literal["explicit", "last-approved", "immediately-preceding", "none"]
    added_slot_ids: list[str]
    removed_slot_ids: list[str]
    changed_slots: list[SlotChangeOut]
    added_section_ids: list[str]
    removed_section_ids: list[str]
    changed_sections: list[SectionChangeOut]


__all__ = [
    "ApprovalCommentIn",
    "ApprovalCommentOut",
    "ApprovalEventOut",
    "ApprovalHistoryOut",
    "ApprovalRequestOut",
    "ApprovalSubmitIn",
    "ApprovalTransitionIn",
    "PaperVersionDiffOut",
]
