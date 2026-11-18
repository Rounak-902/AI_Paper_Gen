"""Camel-case request and response DTOs for the curriculum catalog."""

from __future__ import annotations

import datetime as _dt
import uuid
from typing import Literal, Optional

from pydantic import ConfigDict, Field

from app.modules.auth.schemas import AuthRequest, AuthResponse


CurriculumVersionStatus = Literal["draft", "active", "superseded"]


class CurriculumRequest(AuthRequest):
    """Request base: camelCase aliases and unknown-field rejection are inherited."""


class CurriculumResponse(AuthResponse):
    """Response base with ORM attribute loading and explicit field allowlists."""

    model_config = ConfigDict(from_attributes=True)


# ---------------------------------------------------------------------------
# Boards
# ---------------------------------------------------------------------------
class BoardCreateIn(CurriculumRequest):
    code: str = Field(min_length=1, max_length=64)
    name: str = Field(min_length=1, max_length=200)


class BoardUpdateIn(CurriculumRequest):
    row_version: int = Field(ge=1)
    code: Optional[str] = Field(default=None, min_length=1, max_length=64)
    name: Optional[str] = Field(default=None, min_length=1, max_length=200)


class BoardDeleteIn(CurriculumRequest):
    row_version: int = Field(ge=1)


class BoardOut(CurriculumResponse):
    id: uuid.UUID
    code: str
    name: str
    row_version: int
    created_at: _dt.datetime
    updated_at: _dt.datetime


# ---------------------------------------------------------------------------
# Classes
# ---------------------------------------------------------------------------
class ClassCreateIn(CurriculumRequest):
    board_id: uuid.UUID
    name: str = Field(min_length=1, max_length=200)


class ClassUpdateIn(CurriculumRequest):
    """A class may be renamed; its board placement is immutable."""

    row_version: int = Field(ge=1)
    name: Optional[str] = Field(default=None, min_length=1, max_length=200)


class ClassDeleteIn(CurriculumRequest):
    row_version: int = Field(ge=1)


class ClassOut(CurriculumResponse):
    id: uuid.UUID
    board_id: uuid.UUID
    name: str
    row_version: int
    created_at: _dt.datetime
    updated_at: _dt.datetime


# ---------------------------------------------------------------------------
# Subjects
# ---------------------------------------------------------------------------
class SubjectCreateIn(CurriculumRequest):
    class_id: uuid.UUID
    curriculum_version_id: uuid.UUID
    name: str = Field(min_length=1, max_length=200)


class SubjectUpdateIn(CurriculumRequest):
    """Only the display name is mutable; class and version placement are fixed."""

    row_version: int = Field(ge=1)
    name: Optional[str] = Field(default=None, min_length=1, max_length=200)


class SubjectDeleteIn(CurriculumRequest):
    row_version: int = Field(ge=1)


class SubjectOut(CurriculumResponse):
    id: uuid.UUID
    class_id: uuid.UUID
    curriculum_version_id: Optional[uuid.UUID] = None
    name: str
    row_version: int
    created_at: _dt.datetime
    updated_at: _dt.datetime


# ---------------------------------------------------------------------------
# Chapters and lineage
# ---------------------------------------------------------------------------
class ChapterCreateIn(CurriculumRequest):
    subject_id: uuid.UUID
    curriculum_version_id: Optional[uuid.UUID] = None
    seq: int
    title: str = Field(min_length=1, max_length=500)
    derived_from_chapter_id: Optional[uuid.UUID] = None


class ChapterUpdateIn(CurriculumRequest):
    """Subject/version placement is fixed; the immediate lineage edge may change."""

    row_version: int = Field(ge=1)
    seq: Optional[int] = None
    title: Optional[str] = Field(default=None, min_length=1, max_length=500)
    derived_from_chapter_id: Optional[uuid.UUID] = None


class ChapterDeleteIn(CurriculumRequest):
    row_version: int = Field(ge=1)


class ChapterOut(CurriculumResponse):
    id: uuid.UUID
    subject_id: uuid.UUID
    curriculum_version_id: Optional[uuid.UUID] = None
    seq: int
    title: str
    derived_from_chapter_id: Optional[uuid.UUID] = None
    row_version: int
    created_at: _dt.datetime
    updated_at: _dt.datetime


class ChapterLineageOut(CurriculumResponse):
    """A root-first lineage ending at ``chapter_id``."""

    chapter_id: uuid.UUID
    lineage: list[ChapterOut]


# ---------------------------------------------------------------------------
# Learning outcomes (the HTTP resource is named ``outcomes``)
# ---------------------------------------------------------------------------
class OutcomeCreateIn(CurriculumRequest):
    chapter_id: uuid.UUID
    code: Optional[str] = Field(default=None, min_length=1, max_length=100)
    description: str = Field(min_length=1, max_length=4000)


class OutcomeUpdateIn(CurriculumRequest):
    """An outcome's chapter placement is immutable."""

    row_version: int = Field(ge=1)
    code: Optional[str] = Field(default=None, min_length=1, max_length=100)
    description: Optional[str] = Field(default=None, min_length=1, max_length=4000)


class OutcomeDeleteIn(CurriculumRequest):
    row_version: int = Field(ge=1)


class OutcomeOut(CurriculumResponse):
    id: uuid.UUID
    chapter_id: uuid.UUID
    code: Optional[str] = None
    description: str
    row_version: int
    created_at: _dt.datetime
    updated_at: _dt.datetime


# Model-oriented aliases make the table name discoverable without forcing routers to
# expose ``/learning-outcomes`` instead of architecture doc 02's ``/outcomes``.
LearningOutcomeCreateIn = OutcomeCreateIn
LearningOutcomeUpdateIn = OutcomeUpdateIn
LearningOutcomeDeleteIn = OutcomeDeleteIn
LearningOutcomeOut = OutcomeOut


# ---------------------------------------------------------------------------
# Curriculum versions
# ---------------------------------------------------------------------------
class CurriculumVersionCreateIn(CurriculumRequest):
    board_id: uuid.UUID
    academic_year: str = Field(min_length=1, max_length=64)
    label: Optional[str] = Field(default=None, max_length=200)
    effective_from: Optional[_dt.date] = None
    source_ref: Optional[str] = Field(default=None, max_length=1000)


class CurriculumVersionUpdateIn(CurriculumRequest):
    """Lifecycle metadata is mutable; board and academic year are immutable."""

    row_version: int = Field(ge=1)
    label: Optional[str] = Field(default=None, max_length=200)
    status: Optional[CurriculumVersionStatus] = None
    effective_from: Optional[_dt.date] = None
    source_ref: Optional[str] = Field(default=None, max_length=1000)


class CurriculumVersionDeleteIn(CurriculumRequest):
    row_version: int = Field(ge=1)


class CurriculumVersionOut(CurriculumResponse):
    id: uuid.UUID
    board_id: uuid.UUID
    academic_year: str
    label: Optional[str] = None
    status: str
    effective_from: Optional[_dt.date] = None
    source_ref: Optional[str] = None
    row_version: int
    created_at: _dt.datetime
    updated_at: _dt.datetime


class DeletedOut(CurriculumResponse):
    """Envelope payload returned after an optimistic, soft delete."""

    id: uuid.UUID
    deleted: bool = True


__all__ = [
    "BoardCreateIn",
    "BoardDeleteIn",
    "BoardOut",
    "BoardUpdateIn",
    "ChapterCreateIn",
    "ChapterDeleteIn",
    "ChapterLineageOut",
    "ChapterOut",
    "ChapterUpdateIn",
    "ClassCreateIn",
    "ClassDeleteIn",
    "ClassOut",
    "ClassUpdateIn",
    "CurriculumRequest",
    "CurriculumResponse",
    "CurriculumVersionCreateIn",
    "CurriculumVersionDeleteIn",
    "CurriculumVersionOut",
    "CurriculumVersionUpdateIn",
    "CurriculumVersionStatus",
    "DeletedOut",
    "LearningOutcomeCreateIn",
    "LearningOutcomeDeleteIn",
    "LearningOutcomeOut",
    "LearningOutcomeUpdateIn",
    "OutcomeCreateIn",
    "OutcomeDeleteIn",
    "OutcomeOut",
    "OutcomeUpdateIn",
    "SubjectCreateIn",
    "SubjectDeleteIn",
    "SubjectOut",
    "SubjectUpdateIn",
]
