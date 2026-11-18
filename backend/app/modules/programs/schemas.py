"""Camel-case request and response DTOs for the programs module."""

import datetime as _dt
import uuid
from typing import Literal, Optional

from pydantic import ConfigDict, Field

from app.modules.auth.schemas import AuthRequest, AuthResponse


ProgramStatus = Literal["active", "archived"]


class ProgramRequest(AuthRequest):
    """Request base: camelCase aliases and unknown-field rejection are inherited."""


class ProgramResponse(AuthResponse):
    """Response base with ORM attribute loading and explicit field allowlists."""

    model_config = ConfigDict(from_attributes=True)


class ProgramCreateIn(ProgramRequest):
    name: str = Field(min_length=1, max_length=200)
    branch_id: Optional[uuid.UUID] = None
    exam_target: Optional[str] = Field(default=None, min_length=1, max_length=200)
    academic_year: Optional[str] = Field(default=None, min_length=1, max_length=64)


class ProgramUpdateIn(ProgramRequest):
    row_version: int = Field(ge=1)
    name: Optional[str] = Field(default=None, min_length=1, max_length=200)
    exam_target: Optional[str] = Field(default=None, min_length=1, max_length=200)
    academic_year: Optional[str] = Field(default=None, min_length=1, max_length=64)
    status: Optional[ProgramStatus] = None


class ProgramOut(ProgramResponse):
    id: uuid.UUID
    org_id: uuid.UUID
    branch_id: Optional[uuid.UUID] = None
    name: str
    exam_target: Optional[str] = None
    academic_year: Optional[str] = None
    status: str
    row_version: int
    created_at: _dt.datetime
    updated_at: _dt.datetime


class ProgramAssignmentsOut(ProgramResponse):
    """Current full-replacement assignment sets for one program."""

    program_id: uuid.UUID
    teacher_ids: list[uuid.UUID]
    subject_ids: list[uuid.UUID]


class AssignTeachersRequest(ProgramRequest):
    teacher_ids: list[uuid.UUID]


class AssignSubjectsRequest(ProgramRequest):
    subject_ids: list[uuid.UUID]


class DeletedOut(ProgramResponse):
    """Envelope payload returned after an optimistic, soft delete."""

    id: uuid.UUID
    deleted: bool = True
