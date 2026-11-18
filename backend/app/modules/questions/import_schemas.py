"""Pydantic schemas for the P2-T5 question import pipeline.

Two-step import flow
--------------------
1. ``POST /questions/import/preview`` — upload a file, receive detected column
   headers + first 5 rows + a recommended auto-mapping (source column → target
   field name).  The caller inspects and corrects the mapping.
2. ``POST /questions/import`` — upload the same file + submit the column mapping
   as a JSON body field ``mapping`` in the multipart form. The server persists a
   durable job and returns immediately; ``GET /questions/import-jobs/{id}``
   reports progress and the final row-level outcomes.

Column mapping
--------------
Target field names are the canonical names the template uses:
    qtype, stem, marks, difficulty, bloom, language, source, scope,
    subject_name, chapter_name, board_code, class_name,
    options_json, answer_json, rubric_json

``subject_name`` / ``chapter_name`` / ``board_code`` / ``class_name`` are
resolved to UUIDs by name-lookup in the curriculum tables.  Callers who fill
UUIDs directly may use ``subject_id`` / ``chapter_id`` / ``board_id`` /
``class_id`` as target field names instead.

Bulk operations
---------------
``BulkActionIn`` drives approve / reject / archive on a batch of already-
imported draft questions.  Each question_id is paired with a ``row_version``
for optimistic locking.  Partial success — a per-question error does not abort
the rest of the batch.
"""

from __future__ import annotations

import datetime as dt
import uuid
from typing import Any, Literal, Optional

from pydantic import BaseModel, ConfigDict, Field


# ---------------------------------------------------------------------------
# Column mapping types
# ---------------------------------------------------------------------------

IMPORT_TARGET_FIELDS: tuple[str, ...] = (
    "qtype",
    "stem",
    "marks",
    "difficulty",
    "bloom",
    "language",
    "source",
    "scope",
    "subject_name",
    "chapter_name",
    "board_code",
    "class_name",
    # Direct UUID fields (for callers who already know the IDs)
    "subject_id",
    "chapter_id",
    "board_id",
    "class_id",
    # JSONB content columns
    "options_json",
    "answer_json",
    "rubric_json",
    "meta_json",
)

IMPORT_REQUIRED_FIELDS: frozenset[str] = frozenset({"qtype", "stem", "marks", "difficulty"})


class ColumnMappingEntry(BaseModel):
    """One mapping: a source column header to a canonical target field name."""

    model_config = ConfigDict(extra="forbid")

    source_column: str = Field(..., description="Column name as it appears in the uploaded file")
    target_field: str = Field(
        ...,
        description=f"Canonical target field; one of {IMPORT_TARGET_FIELDS}",
    )


# ---------------------------------------------------------------------------
# Preview endpoint
# ---------------------------------------------------------------------------

class ImportPreviewOut(BaseModel):
    """Response from POST /questions/import/preview.

    Returns the detected column headers, first (up to) 5 rows, and an
    automatically inferred column mapping.  The caller corrects the mapping
    before posting to the main import endpoint.
    """

    model_config = ConfigDict(extra="forbid")

    detected_columns: list[str]
    preview_rows: list[dict[str, Any]] = Field(
        description="First up to 5 data rows, keyed by source column name"
    )
    recommended_mapping: list[ColumnMappingEntry] = Field(
        description="Auto-inferred mapping (source_column → target_field) based on header similarity"
    )
    row_count_estimate: Optional[int] = Field(
        None,
        description="Total number of data rows in the file (None if not determinable cheaply)",
    )


# ---------------------------------------------------------------------------
# Per-row import outcome
# ---------------------------------------------------------------------------

ImportRowStatus = Literal[
    "created",               # Question successfully created as draft
    "created_with_warnings", # Created but near-duplicate warning(s) attached
    "skipped_duplicate",     # Exact content_hash collision — not created
    "error",                 # Validation or reference error — not created
]


class NearDuplicateInfo(BaseModel):
    """Compact near-duplicate info in the import row result."""

    existing_question_id: uuid.UUID
    similarity: float = Field(ge=0.0, le=1.0)
    stem_preview: str = Field(description="First 120 characters of the existing question's stem")


class ImportRowResultOut(BaseModel):
    """Outcome for one data row of an import batch."""

    model_config = ConfigDict(extra="forbid")

    row_number: int = Field(description="1-based row position in the source file (excluding header)")
    status: ImportRowStatus
    question_id: Optional[uuid.UUID] = Field(None, description="Set when status is created or created_with_warnings")
    errors: list[str] = Field(default_factory=list, description="Human-readable reasons for status=error or skipped_duplicate")
    warnings: list[NearDuplicateInfo] = Field(default_factory=list, description="Near-duplicate candidates when status=created_with_warnings")


# ---------------------------------------------------------------------------
# Full import result
# ---------------------------------------------------------------------------

class ImportResultOut(BaseModel):
    """Response from POST /questions/import."""

    model_config = ConfigDict(extra="forbid")

    total_rows: int
    created_count: int
    created_with_warnings_count: int
    error_count: int
    duplicate_count: int
    rows: list[ImportRowResultOut] = Field(
        description=(
            "Row-level outcomes.  The full list is always present in the JSON response. "
            "For very large batches only error/warning rows are included here; "
            "all rows are present in the downloadable error report."
        )
    )
    error_report_token: Optional[str] = Field(
        None,
        description=(
            "Present when there are any errors or near-duplicate warnings. "
            "Use GET /questions/import/report/{token} to download the full CSV report. "
            "For background imports this is the durable import-job ID."
        ),
    )


ImportJobStatus = Literal["queued", "processing", "completed", "failed"]


class ImportJobOut(BaseModel):
    """Durable import state returned by submission and polling endpoints."""

    model_config = ConfigDict(extra="forbid")

    id: uuid.UUID
    status: ImportJobStatus
    total_rows: int
    processed_rows: int
    created_count: int
    created_with_warnings_count: int
    error_count: int
    duplicate_count: int
    rows: list[ImportRowResultOut] = Field(default_factory=list)
    error_report_token: Optional[str] = None
    error: Optional[str] = None
    started_at: Optional[dt.datetime] = None
    completed_at: Optional[dt.datetime] = None
    created_at: dt.datetime
    updated_at: dt.datetime


# ---------------------------------------------------------------------------
# Bulk approve / reject / archive
# ---------------------------------------------------------------------------

BulkAction = Literal["approve", "reject", "archive"]

BULK_ACTION_TARGET_STATUSES: dict[BulkAction, str] = {
    "approve": "approved",
    "reject": "rejected",
    "archive": "archived",
}


class BulkActionIn(BaseModel):
    """Payload for POST /questions/bulk.

    Each question_id must be paired with its current row_version for optimistic
    locking.  A mismatch on any individual question produces a per-row error
    (non-fatal — processing continues for the remaining questions).

    Deliberate scope limit
    ----------------------
    This endpoint uses ``get_db`` (tenant-only session).  Global questions
    (``org_id IS NULL``) cannot be modified through it — the RLS UPDATE
    WITH CHECK clause will deny the write at the database level.  This is
    intentional: bulk operations act on the org's own draft queue.  A
    super_admin who needs to change the status of a global question must use
    the individual ``POST /questions/{id}/approve`` endpoint.
    """

    model_config = ConfigDict(extra="forbid")

    action: BulkAction
    question_ids: list[uuid.UUID] = Field(..., min_length=1, max_length=200)
    row_versions: dict[str, int] = Field(
        ...,
        description="Map of question_id (str) → current row_version for optimistic locking",
    )


BulkRowStatus = Literal["ok", "error"]


class BulkActionRowResultOut(BaseModel):
    """Outcome for one question in a bulk operation."""

    model_config = ConfigDict(extra="forbid")

    question_id: uuid.UUID
    status: BulkRowStatus
    new_status: Optional[str] = Field(None, description="The question's new status if status=ok")
    error_reason: Optional[str] = Field(None, description="Human-readable reason if status=error")


class BulkActionResultOut(BaseModel):
    """Response from POST /questions/bulk."""

    model_config = ConfigDict(extra="forbid")

    action: BulkAction
    ok_count: int
    error_count: int
    results: list[BulkActionRowResultOut]
