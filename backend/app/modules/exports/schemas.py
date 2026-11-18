"""HTTP DTOs for asynchronous export jobs and signed downloads."""

from __future__ import annotations

import datetime as dt
import uuid
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field
from pydantic.alias_generators import to_camel


class ExportModel(BaseModel):
    model_config = ConfigDict(alias_generator=to_camel, populate_by_name=True, extra="forbid")


class ExportCreateIn(ExportModel):
    template_id: uuid.UUID
    format: Literal["pdf", "docx"] = "pdf"
    artifact_type: Literal["paper", "answer_key"] = "paper"


class ExportJobOut(ExportModel):
    id: uuid.UUID
    paper_id: uuid.UUID
    paper_version_id: uuid.UUID
    template_id: uuid.UUID
    format: Literal["pdf", "docx"]
    artifact_type: Literal["paper", "answer_key"]
    status: Literal["queued", "processing", "done", "failed"]
    object_key: str | None = None
    media_type: str | None = None
    byte_size: int | None = None
    sha256: str | None = None
    error: str | None = None
    started_at: dt.datetime | None = None
    completed_at: dt.datetime | None = None
    created_at: dt.datetime
    updated_at: dt.datetime


class SignedDownloadOut(ExportModel):
    export_id: uuid.UUID
    url: str
    expires_in_seconds: int = Field(ge=1)


__all__ = ["ExportCreateIn", "ExportJobOut", "SignedDownloadOut"]
