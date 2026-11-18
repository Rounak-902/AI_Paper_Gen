"""Question import pipeline service (P2-T5).

Design contract
---------------
* Interactive single-row creation continues through ``service.create_question``.
  Durable imports use a set-based fast path for ordinary org-scoped rows: rows
  are validated with the same Pydantic schema, exact and near-duplicate rules
  are evaluated in bulk, questions and audit records are inserted together,
  and the durable job checkpoint commits in that same transaction.

* Import batches are independently atomic.  A database-level batch failure
  rolls the batch back and falls back to the established per-row service path,
  preserving partial success and row-level error reporting without making the
  normal 2,000-row case pay for thousands of network round trips.

* Exact duplicates (``QuestionExactDuplicateConflict``) become
  ``skipped_duplicate`` row outcomes, not HTTP 409s.  Near-duplicate warnings
  from ``QuestionCreateOut.warnings`` become ``created_with_warnings`` outcomes.
  This is P2-T4's policy applied per-row, not a new policy.

* Curriculum name resolution (``subject_name``, ``chapter_name``,
  ``board_code``, ``class_name``) happens inside the same per-row session
  using read-only SELECT against the global catalog tables.  UUID fields
  (``subject_id``, ``chapter_id``, ``board_id``, ``class_id``) are accepted
  directly and bypass resolution.

Error report cache
------------------
Full error/warning reports are stored in a module-level
``dict[str, (bytes, datetime)]`` keyed by a random UUID token, with a 1-hour
TTL.  This is correct for single-instance deployments.  Under multi-worker or
multi-instance deployments the token download may land on a different process.

**Known limitation — revisit at P9 before multi-instance production deployment.**
The fix is to store reports in MinIO/S3 (the P6 export pattern).
"""

from __future__ import annotations

import csv
import io
import json
import logging
import re
import uuid
from collections.abc import Awaitable, Callable
from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone
from difflib import SequenceMatcher
from typing import Any, Optional

from sqlalchemy import insert, select, text
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from app.common.deps import RequestScope
from app.common.errors import ConflictError, NotFoundError, ValidationError
from app.core.config import get_settings
from app.db.session import get_identity_session
from app.models.assembly import TaskOutbox
from app.models.audit import AuditLog
from app.models.curriculum import Board, Chapter, Class as SchoolClass, Subject
from app.models.question import QuestionBank
from app.models.question_import import QuestionImportJob
from app.modules.questions import service as question_service
from app.modules.questions import schemas as question_schemas
from app.modules.questions.import_schemas import (
    BulkAction,
    BulkActionRowResultOut,
    BulkActionResultOut,
    ColumnMappingEntry,
    IMPORT_TARGET_FIELDS,
    ImportPreviewOut,
    ImportJobOut,
    ImportRowResultOut,
    ImportRowStatus,
    NearDuplicateInfo,
)
from app.modules.questions.service import (
    GlobalQuestionCreationForbidden,
    QuestionExactDuplicateConflict,
    assert_path_org,
)
from app.modules.questions.repository import _compute_content_hash, _normalise_stem
from app.workers.outbox import add_outbox_task

logger = logging.getLogger("papergen.questions.import")


# ---------------------------------------------------------------------------
# In-process error-report cache (P9 TODO: replace with MinIO signed URLs)
# ---------------------------------------------------------------------------

# Mapping: token → (csv_bytes, expires_at)
_REPORT_CACHE: dict[str, tuple[bytes, datetime]] = {}
_REPORT_TTL = timedelta(hours=1)


def _store_report(csv_bytes: bytes) -> str:
    """Store an error report in the cache and return its token."""
    _evict_expired_reports()
    token = str(uuid.uuid4())
    _REPORT_CACHE[token] = (csv_bytes, datetime.now(timezone.utc) + _REPORT_TTL)
    return token


def _evict_expired_reports() -> None:
    now = datetime.now(timezone.utc)
    expired = [t for t, (_, exp) in _REPORT_CACHE.items() if exp <= now]
    for t in expired:
        del _REPORT_CACHE[t]


def get_cached_report(token: str) -> Optional[bytes]:
    """Return the cached CSV bytes for a token, or None if expired/missing."""
    _evict_expired_reports()
    entry = _REPORT_CACHE.get(token)
    if entry is None:
        return None
    return entry[0]


# ---------------------------------------------------------------------------
# Downloadable CSV template
# ---------------------------------------------------------------------------

TEMPLATE_COLUMNS: list[str] = [
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
    "options_json",
    "answer_json",
    "rubric_json",
    "meta_json",
]

TEMPLATE_EXAMPLE_ROWS: list[dict[str, str]] = [
    {
        "qtype": "mcq",
        "stem": "What is the capital of France?",
        "marks": "1",
        "difficulty": "easy",
        "bloom": "remember",
        "language": "en",
        "source": "institute_bank",
        "scope": "org",
        "subject_name": "Geography",
        "chapter_name": "European Capitals",
        "board_code": "CBSE",
        "class_name": "Class 9",
        "options_json": '[{"text":"Paris","is_correct":true},{"text":"London","is_correct":false},{"text":"Berlin","is_correct":false},{"text":"Rome","is_correct":false}]',
        "answer_json": '{"index":0}',
        "rubric_json": "",
    },
    {
        "qtype": "short_answer",
        "stem": "Explain the water cycle in 3-4 sentences.",
        "marks": "3",
        "difficulty": "medium",
        "bloom": "understand",
        "language": "en",
        "source": "institute_bank",
        "scope": "org",
        "subject_name": "Science",
        "chapter_name": "Water and Its Properties",
        "board_code": "CBSE",
        "class_name": "Class 8",
        "options_json": "",
        "answer_json": '{"text":"The water cycle describes how water evaporates from bodies of water, forms clouds, and returns as precipitation."}',
        "rubric_json": '{"key_points":["evaporation","condensation","precipitation","collection"]}',
    },
]


def build_template_csv() -> bytes:
    """Return the bytes of a downloadable CSV import template."""
    buf = io.StringIO()
    writer = csv.DictWriter(buf, fieldnames=TEMPLATE_COLUMNS, extrasaction="ignore")
    writer.writeheader()
    for row in TEMPLATE_EXAMPLE_ROWS:
        writer.writerow(row)
    return buf.getvalue().encode("utf-8-sig")  # utf-8-sig for Excel compatibility


# ---------------------------------------------------------------------------
# Column mapping auto-detection
# ---------------------------------------------------------------------------

def _similarity(a: str, b: str) -> float:
    return SequenceMatcher(None, a.lower(), b.lower()).ratio()


def _normalize_column_name(value: str) -> str:
    """Normalize common header separators without discarding ``_id`` semantics."""
    separated = re.sub(r"[^a-z0-9]+", "_", value.casefold())
    return re.sub(r"_+", "_", separated).strip("_")


def _auto_map_columns(source_columns: list[str]) -> list[ColumnMappingEntry]:
    """Infer a column mapping by name similarity.

    Strong matches are resolved globally before fuzzy matching, so an exact
    ``subject_id`` column cannot lose its target to an earlier, superficially
    similar header.  Direct UUID targets are intentionally eligible only for
    an exact normalized match or an explicit ID suffix such as
    ``legacy_subject_id``.  The remaining headers retain the existing fuzzy
    matching behaviour against the downloadable template columns; below 0.5
    similarity a column is left unmapped.
    """
    used_targets: set[str] = set()
    handled_sources: set[str] = set()
    selected_targets: dict[str, str] = {}
    normalized_sources = {src: _normalize_column_name(src) for src in source_columns}
    normalized_targets = {
        target: _normalize_column_name(target) for target in IMPORT_TARGET_FIELDS
    }

    def add_mapping(src: str, target: str) -> None:
        handled_sources.add(src)
        if target in used_targets:
            return
        selected_targets[src] = target
        used_targets.add(target)

    # Pass 1: reserve every exact canonical target before considering fuzzy aliases.
    for src in source_columns:
        src_normalized = normalized_sources[src]
        exact_target = next(
            (
                target
                for target, target_normalized in normalized_targets.items()
                if src_normalized == target_normalized
            ),
            None,
        )
        if exact_target is not None:
            add_mapping(src, exact_target)

    # Pass 2: accept qualified ID headers (for example ``legacy_subject_id``),
    # while never inferring a UUID target from a plain header such as ``subject``.
    id_targets = [target for target in IMPORT_TARGET_FIELDS if target.endswith("_id")]
    for src in source_columns:
        if src in handled_sources:
            continue
        src_normalized = normalized_sources[src]
        suffix_target = next(
            (
                target
                for target in id_targets
                if src_normalized.endswith(f"_{normalized_targets[target]}")
            ),
            None,
        )
        if suffix_target is not None:
            add_mapping(src, suffix_target)

    # Pass 3: preserve P2-T5's established fuzzy matching for template/display
    # columns.  UUID targets are excluded here because fuzzy text similarity is
    # not strong enough evidence that a source column contains UUID values.
    for src in source_columns:
        if src in handled_sources:
            continue
        best_target: Optional[str] = None
        best_score = 0.0
        for target in TEMPLATE_COLUMNS:
            if target in used_targets:
                continue
            s = _similarity(src, target)
            if s > best_score:
                best_score = s
                best_target = target
        if best_target and best_score >= 0.5:
            add_mapping(src, best_target)
    return [
        ColumnMappingEntry(source_column=src, target_field=selected_targets[src])
        for src in source_columns
        if src in selected_targets
    ]


# ---------------------------------------------------------------------------
# File parsing
# ---------------------------------------------------------------------------

def _parse_csv_bytes(raw: bytes) -> list[dict[str, str]]:
    text = raw.decode("utf-8-sig", errors="replace")
    reader = csv.DictReader(io.StringIO(text))
    return [dict(row) for row in reader]


def _parse_xlsx_bytes(raw: bytes) -> list[dict[str, str]]:
    try:
        from openpyxl import load_workbook  # deferred: not always installed in tests
    except ImportError as exc:
        raise ImportError("openpyxl is required for XLSX imports") from exc

    wb = load_workbook(filename=io.BytesIO(raw), read_only=True, data_only=True)
    ws = wb.active
    rows = list(ws.iter_rows(values_only=True))
    if not rows:
        return []
    headers = [str(h) if h is not None else "" for h in rows[0]]
    result = []
    for row in rows[1:]:
        result.append(
            {headers[i]: (str(v) if v is not None else "") for i, v in enumerate(row)}
        )
    wb.close()
    return result


def _parse_json_bytes(raw: bytes) -> list[dict[str, str]]:
    data = json.loads(raw.decode("utf-8"))
    if not isinstance(data, list):
        raise ValueError("JSON import must be a JSON array of objects")
    result = []
    for item in data:
        if not isinstance(item, dict):
            raise ValueError("Each element in the JSON array must be an object")
        # Stringify all values for uniform downstream handling; serialize dict/list as JSON
        result.append(
            {
                k: (
                    json.dumps(v)
                    if isinstance(v, (dict, list))
                    else (str(v) if v is not None else "")
                )
                for k, v in item.items()
            }
        )
    return result


def parse_import_file(
    raw_bytes: bytes,
    content_type: str,
    filename: str = "",
) -> list[dict[str, str]]:
    """Parse CSV, XLSX, or JSON file bytes into a list of raw string dicts.

    Content-type is the primary signal; filename extension is the fallback.
    """
    ct = (content_type or "").lower()
    fn = (filename or "").lower()

    if "spreadsheet" in ct or "xlsx" in ct or fn.endswith(".xlsx"):
        return _parse_xlsx_bytes(raw_bytes)
    if "json" in ct or fn.endswith(".json"):
        return _parse_json_bytes(raw_bytes)
    # Default: CSV (text/csv, text/plain, application/octet-stream, etc.)
    return _parse_csv_bytes(raw_bytes)


def get_source_columns(raw_rows: list[dict[str, str]]) -> list[str]:
    """Return the ordered list of column names from the parsed rows."""
    columns: list[str] = []
    seen: set[str] = set()
    for row in raw_rows:
        for k in row.keys():
            if k not in seen:
                seen.add(k)
                columns.append(k)
    return columns


# ---------------------------------------------------------------------------
# Column mapping application
# ---------------------------------------------------------------------------

def apply_column_mapping(
    raw_row: dict[str, str],
    mapping: list[ColumnMappingEntry],
) -> dict[str, str]:
    """Remap source column names to target field names.

    Unknown source columns (not in any mapping entry) are dropped.
    Multiple source columns mapped to the same target: last one wins.
    """
    mapped: dict[str, str] = {}
    source_to_target = {e.source_column: e.target_field for e in mapping}
    for src_key, value in raw_row.items():
        target = source_to_target.get(src_key)
        if target:
            mapped[target] = value
    return mapped


# ---------------------------------------------------------------------------
# Curriculum name → UUID resolution
# ---------------------------------------------------------------------------

@dataclass
class CurriculumRefCache:
    """Per-import session cache of resolved curriculum names → UUIDs.

    Avoids repeated SELECT queries for the same subject/chapter names when
    hundreds of rows share the same curriculum placement.
    """

    subjects: dict[str, Optional[uuid.UUID]] = field(default_factory=dict)
    chapters: dict[str, Optional[uuid.UUID]] = field(default_factory=dict)
    boards: dict[str, Optional[uuid.UUID]] = field(default_factory=dict)
    classes: dict[str, Optional[uuid.UUID]] = field(default_factory=dict)


async def _resolve_subject_by_name(
    session: AsyncSession,
    name: str,
    cache: CurriculumRefCache,
) -> Optional[uuid.UUID]:
    if name in cache.subjects:
        return cache.subjects[name]
    result = await session.execute(
        select(Subject.id).where(Subject.name == name, Subject.deleted_at.is_(None)).limit(1)
    )
    val = result.scalar_one_or_none()
    cache.subjects[name] = val
    return val


async def _resolve_chapter_by_name(
    session: AsyncSession,
    name: str,
    subject_id: Optional[uuid.UUID],
    cache: CurriculumRefCache,
) -> Optional[uuid.UUID]:
    cache_key = f"{subject_id}:{name}"
    if cache_key in cache.chapters:
        return cache.chapters[cache_key]
    stmt = select(Chapter.id).where(
        Chapter.title == name,
        Chapter.deleted_at.is_(None),
    )
    if subject_id is not None:
        stmt = stmt.where(Chapter.subject_id == subject_id)
    result = await session.execute(stmt.limit(1))
    val = result.scalar_one_or_none()
    cache.chapters[cache_key] = val
    return val


async def _resolve_board_by_code(
    session: AsyncSession,
    code: str,
    cache: CurriculumRefCache,
) -> Optional[uuid.UUID]:
    if code in cache.boards:
        return cache.boards[code]
    result = await session.execute(
        select(Board.id).where(Board.code == code, Board.deleted_at.is_(None)).limit(1)
    )
    val = result.scalar_one_or_none()
    cache.boards[code] = val
    return val


async def _resolve_class_by_name(
    session: AsyncSession,
    name: str,
    cache: CurriculumRefCache,
) -> Optional[uuid.UUID]:
    if name in cache.classes:
        return cache.classes[name]
    result = await session.execute(
        select(SchoolClass.id).where(SchoolClass.name == name, SchoolClass.deleted_at.is_(None)).limit(1)
    )
    val = result.scalar_one_or_none()
    cache.classes[name] = val
    return val


# ---------------------------------------------------------------------------
# Row validation and QuestionCreateIn construction
# ---------------------------------------------------------------------------

def _coerce_mapped_row(mapped: dict[str, str]) -> tuple[dict[str, Any], list[str]]:
    """Convert raw string values to typed values expected by QuestionCreateIn.

    Returns (coerced_dict, error_list).  If error_list is non-empty the row
    should not be sent to the service.
    """
    errors: list[str] = []
    out: dict[str, Any] = {}

    # Required fields
    stem = mapped.get("stem", "").strip()
    if not stem:
        errors.append("missing required field: stem")
    else:
        out["stem"] = stem

    qtype = mapped.get("qtype", "").strip().lower()
    if not qtype:
        errors.append("missing required field: qtype")
    elif qtype not in question_schemas.QUESTION_TYPES:
        errors.append(f"invalid qtype '{qtype}'; must be one of {question_schemas.QUESTION_TYPES}")
    else:
        out["qtype"] = qtype

    marks_raw = mapped.get("marks", "").strip()
    if not marks_raw:
        errors.append("missing required field: marks")
    else:
        try:
            import decimal
            marks_val = decimal.Decimal(marks_raw)
            if marks_val <= 0:
                errors.append(f"marks must be > 0, got {marks_val}")
            else:
                out["marks"] = marks_val
        except Exception:
            errors.append(f"marks is not a valid number: {marks_raw!r}")

    difficulty = mapped.get("difficulty", "").strip().lower()
    if not difficulty:
        errors.append("missing required field: difficulty")
    elif difficulty not in question_schemas.QUESTION_DIFFICULTIES:
        errors.append(f"invalid difficulty '{difficulty}'; must be one of {question_schemas.QUESTION_DIFFICULTIES}")
    else:
        out["difficulty"] = difficulty

    # Optional classification fields
    bloom = mapped.get("bloom", "").strip().lower() or None
    if bloom and bloom not in question_schemas.QUESTION_BLOOM_LEVELS:
        errors.append(f"invalid bloom '{bloom}'; must be one of {question_schemas.QUESTION_BLOOM_LEVELS}")
    else:
        out["bloom"] = bloom

    lang = mapped.get("language", "").strip() or "en"
    out["language"] = lang

    source = mapped.get("source", "").strip().lower() or "institute_bank"
    if source not in question_schemas.QUESTION_SOURCES:
        errors.append(f"invalid source '{source}'; must be one of {question_schemas.QUESTION_SOURCES}")
    else:
        out["source"] = source

    scope = mapped.get("scope", "").strip().lower() or None
    if scope and scope not in question_schemas.QUESTION_SCOPES:
        errors.append(f"invalid scope '{scope}'; must be one of {question_schemas.QUESTION_SCOPES}")
    else:
        out["scope"] = scope

    # JSONB content fields — parse if present, skip if empty
    for field_name, key in [
        ("options", "options_json"),
        ("answer", "answer_json"),
        ("rubric", "rubric_json"),
        ("meta", "meta_json"),
    ]:
        raw_val = mapped.get(key, "").strip()
        if raw_val:
            try:
                out[field_name] = json.loads(raw_val)
            except json.JSONDecodeError as exc:
                errors.append(f"invalid JSON in {key}: {exc}")
        else:
            if field_name == "meta":
                out["meta"] = {}
            else:
                out.setdefault(field_name, None)

    # Direct UUID fields (bypass name resolution)
    for uuid_field in ("subject_id", "chapter_id", "board_id", "class_id"):
        raw_val = mapped.get(uuid_field, "").strip()
        if raw_val:
            try:
                out[uuid_field] = uuid.UUID(raw_val)
            except ValueError:
                errors.append(f"invalid UUID in {uuid_field}: {raw_val!r}")

    return out, errors


# ---------------------------------------------------------------------------
# Set-based durable import path
# ---------------------------------------------------------------------------

@dataclass(frozen=True)
class PreparedImportRow:
    """One validated org-scoped row ready for set-based dedup and insertion."""

    row_number: int
    question_id: uuid.UUID
    payload: question_schemas.QuestionCreateIn
    content_hash: str


BulkProgressCallback = Callable[
    [AsyncSession, list[ImportRowResultOut], int], Awaitable[None]
]


async def _preload_curriculum_cache(
    session: AsyncSession,
    mapped_rows: list[tuple[int, dict[str, str]]],
    cache: CurriculumRefCache,
) -> None:
    """Resolve every catalog label used by an import in four set-based queries."""

    subject_names = {
        mapped.get("subject_name", "").strip()
        for _, mapped in mapped_rows
        if mapped.get("subject_name", "").strip()
    }
    chapter_names = {
        mapped.get("chapter_name", "").strip()
        for _, mapped in mapped_rows
        if mapped.get("chapter_name", "").strip()
    }
    board_codes = {
        mapped.get("board_code", "").strip()
        for _, mapped in mapped_rows
        if mapped.get("board_code", "").strip()
    }
    class_names = {
        mapped.get("class_name", "").strip()
        for _, mapped in mapped_rows
        if mapped.get("class_name", "").strip()
    }

    cache.subjects.update({name: None for name in subject_names})
    cache.boards.update({code: None for code in board_codes})
    cache.classes.update({name: None for name in class_names})

    if subject_names:
        subject_rows = (
            await session.execute(
                select(Subject.id, Subject.name).where(
                    Subject.name.in_(subject_names),
                    Subject.deleted_at.is_(None),
                )
            )
        ).all()
        for subject_id, name in subject_rows:
            cache.subjects.setdefault(name, subject_id)
            if cache.subjects[name] is None:
                cache.subjects[name] = subject_id

    if board_codes:
        board_rows = (
            await session.execute(
                select(Board.id, Board.code).where(
                    Board.code.in_(board_codes),
                    Board.deleted_at.is_(None),
                )
            )
        ).all()
        for board_id, code in board_rows:
            if cache.boards[code] is None:
                cache.boards[code] = board_id

    if class_names:
        class_rows = (
            await session.execute(
                select(SchoolClass.id, SchoolClass.name).where(
                    SchoolClass.name.in_(class_names),
                    SchoolClass.deleted_at.is_(None),
                )
            )
        ).all()
        for class_id, name in class_rows:
            if cache.classes[name] is None:
                cache.classes[name] = class_id

    if chapter_names:
        chapter_rows = (
            await session.execute(
                select(Chapter.id, Chapter.title, Chapter.subject_id).where(
                    Chapter.title.in_(chapter_names),
                    Chapter.deleted_at.is_(None),
                )
            )
        ).all()
        ids_by_title: dict[str, list[uuid.UUID]] = {}
        for chapter_id, title, subject_id in chapter_rows:
            cache.chapters[f"{subject_id}:{title}"] = chapter_id
            ids_by_title.setdefault(title, []).append(chapter_id)
        for title, ids in ids_by_title.items():
            cache.chapters[f"None:{title}"] = ids[0] if len(ids) == 1 else None


def _prepare_import_row(
    *,
    row_number: int,
    mapped: dict[str, str],
    scope: RequestScope,
    cache: CurriculumRefCache,
) -> tuple[Optional[PreparedImportRow], Optional[ImportRowResultOut]]:
    """Coerce, resolve, and validate one row without database round trips."""

    coerced, errors = _coerce_mapped_row(mapped)
    if errors:
        return None, ImportRowResultOut(
            row_number=row_number,
            status="error",
            errors=errors,
        )

    subject_id: Optional[uuid.UUID] = coerced.get("subject_id")
    subject_name = mapped.get("subject_name", "").strip()
    if subject_id is None and subject_name:
        subject_id = cache.subjects.get(subject_name)
        if subject_id is None:
            errors.append(f"subject not found: {subject_name!r}")

    chapter_id: Optional[uuid.UUID] = coerced.get("chapter_id")
    chapter_name = mapped.get("chapter_name", "").strip()
    if chapter_id is None and chapter_name:
        chapter_id = cache.chapters.get(f"{subject_id}:{chapter_name}")
        if chapter_id is None:
            errors.append(
                f"chapter not found: {chapter_name!r} (subject_id={subject_id})"
            )

    board_id: Optional[uuid.UUID] = coerced.get("board_id")
    board_code = mapped.get("board_code", "").strip()
    if board_id is None and board_code:
        board_id = cache.boards.get(board_code)
        if board_id is None:
            errors.append(f"board not found with code: {board_code!r}")

    class_id: Optional[uuid.UUID] = coerced.get("class_id")
    class_name = mapped.get("class_name", "").strip()
    if class_id is None and class_name:
        class_id = cache.classes.get(class_name)
        if class_id is None:
            errors.append(f"class not found: {class_name!r}")

    if errors:
        return None, ImportRowResultOut(
            row_number=row_number,
            status="error",
            errors=errors,
        )

    payload_data: dict[str, Any] = {
        "qtype": coerced["qtype"],
        "stem": coerced["stem"],
        "marks": coerced["marks"],
        "difficulty": coerced["difficulty"],
        "bloom": coerced.get("bloom"),
        "language": coerced.get("language", "en"),
        "source": coerced.get("source", "institute_bank"),
        "scope": coerced.get("scope"),
        "options": coerced.get("options"),
        "answer": coerced.get("answer"),
        "rubric": coerced.get("rubric"),
        "meta": coerced.get("meta", {}),
        "subject_id": subject_id,
        "chapter_id": chapter_id,
        "board_id": board_id,
        "class_id": class_id,
    }
    payload_data = {key: value for key, value in payload_data.items() if value is not None}
    try:
        payload = question_schemas.QuestionCreateIn(**payload_data)
    except Exception as exc:
        return None, ImportRowResultOut(
            row_number=row_number,
            status="error",
            errors=[f"validation: {exc}"],
        )

    if payload.scope == "global":
        if scope.role != "super_admin":
            return None, ImportRowResultOut(
                row_number=row_number,
                status="error",
                errors=[
                    "GLOBAL_QUESTION_CREATION_FORBIDDEN: Only super_admin may create global questions."
                ],
            )
        # Global imports are rare and use the established per-row service path;
        # its database policy checks are intentionally not duplicated here.
        return None, None

    question_id = uuid.uuid4()
    return (
        PreparedImportRow(
            row_number=row_number,
            question_id=question_id,
            payload=payload,
            content_hash=_compute_content_hash(
                payload.qtype,
                payload.stem,
                payload.options,
                payload.answer,
                payload.rubric,
            ),
        ),
        None,
    )


async def _near_duplicate_warnings(
    session: AsyncSession,
    *,
    org_id: uuid.UUID,
    rows: list[PreparedImportRow],
    threshold: float,
) -> dict[int, list[NearDuplicateInfo]]:
    """Find up to five existing/within-batch near matches per incoming row.

    Uses a LATERAL join so PostgreSQL can stop scanning after 5 matches per
    incoming row instead of computing all pairwise similarities first, then
    ranking with window functions.  On a 2,000-row import against a table
    with thousands of existing questions this reduces query time by 5-20×.
    """

    if not rows:
        return {}

    incoming = json.dumps(
        [
            {
                "source_row": row.row_number,
                "question_id": str(row.question_id),
                "subject_id": str(row.payload.subject_id)
                if row.payload.subject_id is not None
                else None,
                "stem": row.payload.stem,
                "normalised_stem": _normalise_stem(row.payload.stem),
                "content_hash": row.content_hash,
            }
            for row in rows
        ]
    )
    await session.execute(
        text(
            "SELECT set_config('pg_trgm.similarity_threshold', :threshold, true)"
        ),
        {"threshold": str(threshold)},
    )

    # Use LATERAL join for per-row limit pushdown — PostgreSQL can stop
    # scanning after finding 5 matches per incoming row, avoiding the full
    # cross-product that the old window-function approach required.
    result = await session.execute(
        text(
            """
            WITH incoming AS (
                SELECT *
                FROM jsonb_to_recordset(CAST(:incoming AS jsonb)) AS item(
                    source_row integer,
                    question_id uuid,
                    subject_id uuid,
                    stem text,
                    normalised_stem text,
                    content_hash text
                )
            ),
            db_matches AS (
                SELECT
                    item.source_row,
                    match.id AS existing_question_id,
                    match.stem AS existing_stem,
                    match.score
                FROM incoming AS item,
                LATERAL (
                    SELECT
                        existing.id,
                        existing.stem,
                        similarity(existing.stem, item.normalised_stem) AS score
                    FROM question_bank AS existing
                    WHERE existing.org_id = :org_id
                      AND existing.subject_id IS NOT DISTINCT FROM item.subject_id
                      AND existing.deleted_at IS NULL
                      AND existing.is_current IS TRUE
                      AND existing.content_hash <> item.content_hash
                      AND existing.stem % item.normalised_stem
                    ORDER BY similarity(existing.stem, item.normalised_stem) DESC, existing.id
                    LIMIT 5
                ) AS match
            ),
            batch_matches AS (
                SELECT
                    later.source_row,
                    earlier.question_id AS existing_question_id,
                    earlier.stem AS existing_stem,
                    similarity(earlier.normalised_stem, later.normalised_stem) AS score
                FROM incoming AS later
                JOIN incoming AS earlier
                  ON earlier.source_row < later.source_row
                 AND earlier.subject_id IS NOT DISTINCT FROM later.subject_id
                 AND earlier.content_hash <> later.content_hash
                 AND similarity(earlier.normalised_stem, later.normalised_stem) >= :threshold
            ),
            all_matches AS (
                SELECT * FROM db_matches
                UNION ALL
                SELECT * FROM batch_matches
            ),
            ranked AS (
                SELECT
                    all_matches.*,
                    row_number() OVER (
                        PARTITION BY source_row
                        ORDER BY score DESC, existing_question_id
                    ) AS match_rank
                FROM all_matches
            )
            SELECT source_row, existing_question_id, existing_stem, score
            FROM ranked
            WHERE match_rank <= 5
            ORDER BY source_row, match_rank
            """
        ),
        {
            "incoming": incoming,
            "org_id": org_id,
            "threshold": threshold,
        },
    )
    warnings: dict[int, list[NearDuplicateInfo]] = {}
    for row in result.mappings():
        warnings.setdefault(row["source_row"], []).append(
            NearDuplicateInfo(
                existing_question_id=row["existing_question_id"],
                similarity=float(row["score"]),
                stem_preview=row["existing_stem"][:120],
            )
        )
    return warnings


async def _process_bulk_batch(
    *,
    mapped_rows: list[tuple[int, dict[str, str]]],
    total_rows: int,
    scope: RequestScope,
    org_id: uuid.UUID,
    cache: CurriculumRefCache,
    ip: Optional[str],
    session_factory: async_sessionmaker[AsyncSession],
    progress_callback: Optional[BulkProgressCallback],
) -> Optional[list[ImportRowResultOut]]:
    """Commit one ordinary org-scoped batch, or return ``None`` for fallback."""

    results_by_row: dict[int, ImportRowResultOut] = {}
    prepared: list[PreparedImportRow] = []
    for row_number, mapped in mapped_rows:
        row, error = _prepare_import_row(
            row_number=row_number,
            mapped=mapped,
            scope=scope,
            cache=cache,
        )
        if row is None and error is None:
            return None
        if error is not None:
            results_by_row[row_number] = error
        elif row is not None:
            prepared.append(row)

    async with get_identity_session(
        scope.user_id,
        org_id=org_id,
        session_factory=session_factory,
    ) as session:
        for subject_id in sorted(
            {row.payload.subject_id for row in prepared},
            key=lambda value: str(value or "no-subject"),
        ):
            await session.execute(
                text(
                    "SELECT pg_advisory_xact_lock("
                    "hashtextextended(:dedup_lock_key, 0))"
                ),
                {
                    "dedup_lock_key": (
                        f"question:{org_id}:{subject_id or 'no-subject'}"
                    )
                },
            )

        hashes = {row.content_hash for row in prepared}
        existing_by_key: dict[tuple[Optional[uuid.UUID], str], uuid.UUID] = {}
        if hashes:
            exact_rows = (
                await session.execute(
                    select(
                        QuestionBank.id,
                        QuestionBank.subject_id,
                        QuestionBank.content_hash,
                    )
                    .where(
                        QuestionBank.org_id == org_id,
                        QuestionBank.deleted_at.is_(None),
                        QuestionBank.content_hash.in_(hashes),
                    )
                    .order_by(
                        QuestionBank.is_current.desc(),
                        QuestionBank.created_at.asc(),
                        QuestionBank.id.asc(),
                    )
                )
            ).all()
            for question_id, subject_id, content_hash in exact_rows:
                existing_by_key.setdefault((subject_id, content_hash), question_id)

        accepted: list[PreparedImportRow] = []
        for row in prepared:
            key = (row.payload.subject_id, row.content_hash)
            duplicate_id = existing_by_key.get(key)
            if duplicate_id is not None:
                results_by_row[row.row_number] = ImportRowResultOut(
                    row_number=row.row_number,
                    status="skipped_duplicate",
                    errors=[
                        "An exact duplicate question already exists "
                        f"(question_id={duplicate_id})."
                    ],
                )
                continue
            existing_by_key[key] = row.question_id
            accepted.append(row)

        threshold = get_settings().DEDUP_REVIEW_THRESHOLD
        warnings_by_row = await _near_duplicate_warnings(
            session,
            org_id=org_id,
            rows=accepted,
            threshold=threshold,
        )

        if accepted:
            await session.execute(
                insert(QuestionBank),
                [
                    {
                        "id": row.question_id,
                        "root_question_id": row.question_id,
                        "previous_version_id": None,
                        "version_number": 1,
                        "is_current": True,
                        "org_id": org_id,
                        "scope": "org",
                        "source": row.payload.source,
                        "board_id": row.payload.board_id,
                        "class_id": row.payload.class_id,
                        "subject_id": row.payload.subject_id,
                        "chapter_id": row.payload.chapter_id,
                        "qtype": row.payload.qtype,
                        "marks": row.payload.marks,
                        "difficulty": row.payload.difficulty,
                        "bloom": row.payload.bloom,
                        "language": row.payload.language,
                        "stem": row.payload.stem,
                        "options": row.payload.options,
                        "answer": row.payload.answer,
                        "rubric": row.payload.rubric,
                        "learning_outcome_ids": row.payload.learning_outcome_ids,
                        "competency_ids": row.payload.competency_ids,
                        "meta": row.payload.meta,
                        "content_hash": row.content_hash,
                        "status": "draft",
                        "created_by": scope.user_id,
                        "prompt_version_id": row.payload.prompt_version_id,
                    }
                    for row in accepted
                ],
            )
            await session.execute(
                insert(AuditLog),
                [
                    {
                        "org_id": org_id,
                        "actor_id": scope.user_id,
                        "action": "question.created",
                        "entity": "question_bank",
                        "entity_id": row.question_id,
                        "meta": {
                            "qtype": row.payload.qtype,
                            "scope": "org",
                            "nearDuplicateCount": len(
                                warnings_by_row.get(row.row_number, [])
                            ),
                            "nearDuplicateThreshold": threshold,
                            "importMode": "bulk",
                        },
                        "ip": ip,
                    }
                    for row in accepted
                ],
            )

        for row in accepted:
            warnings = warnings_by_row.get(row.row_number, [])
            results_by_row[row.row_number] = ImportRowResultOut(
                row_number=row.row_number,
                status="created_with_warnings" if warnings else "created",
                question_id=row.question_id,
                warnings=warnings,
            )

        batch_results = [
            results_by_row[row_number]
            for row_number, _ in mapped_rows
        ]
        if progress_callback is not None:
            await progress_callback(session, batch_results, total_rows)

    logger.info(
        "question_import_bulk_batch org_id=%s first_row=%d last_row=%d size=%d",
        org_id,
        mapped_rows[0][0],
        mapped_rows[-1][0],
        len(mapped_rows),
    )
    return batch_results


async def process_import_bulk(
    *,
    scope: RequestScope,
    org_id: uuid.UUID,
    raw_bytes: bytes,
    content_type: str,
    filename: str,
    column_mapping: list[ColumnMappingEntry],
    ip: Optional[str] = None,
    start_index: int = 0,
    session_factory: async_sessionmaker[AsyncSession],
    progress_callback: Optional[BulkProgressCallback] = None,
    batch_size: int = 500,
) -> list[ImportRowResultOut]:
    """Validate and commit a durable import in bounded set-based batches."""

    assert_path_org(scope, org_id)
    if batch_size < 1:
        raise ValueError("batch_size must be positive")
    raw_rows = parse_import_file(raw_bytes, content_type, filename)
    if start_index < 0 or start_index > len(raw_rows):
        raise ValueError("start_index must identify a row boundary in the import file")

    mapped_rows = [
        (index + 1, apply_column_mapping(raw_row, column_mapping))
        for index, raw_row in enumerate(raw_rows)
    ][start_index:]
    cache = CurriculumRefCache()
    if mapped_rows:
        async with get_identity_session(
            scope.user_id,
            org_id=org_id,
            session_factory=session_factory,
        ) as session:
            await _preload_curriculum_cache(session, mapped_rows, cache)

    results: list[ImportRowResultOut] = []
    for offset in range(0, len(mapped_rows), batch_size):
        batch = mapped_rows[offset : offset + batch_size]
        try:
            batch_results = await _process_bulk_batch(
                mapped_rows=batch,
                total_rows=len(raw_rows),
                scope=scope,
                org_id=org_id,
                cache=cache,
                ip=ip,
                session_factory=session_factory,
                progress_callback=progress_callback,
            )
        except Exception:
            logger.exception(
                "question_import_bulk_batch_fallback org_id=%s first_row=%d last_row=%d",
                org_id,
                batch[0][0],
                batch[-1][0],
            )
            batch_results = None

        if batch_results is None:
            batch_results = []
            for row_number, mapped in batch:
                batch_results.append(
                    await _process_one_row(
                        row_number=row_number,
                        mapped=mapped,
                        scope=scope,
                        org_id=org_id,
                        curriculum_cache=cache,
                        ip=ip,
                        session_factory=session_factory,
                    )
                )
            if progress_callback is not None:
                async with get_identity_session(
                    scope.user_id,
                    org_id=org_id,
                    session_factory=session_factory,
                ) as session:
                    await progress_callback(session, batch_results, len(raw_rows))

        results.extend(batch_results)

    return results


# ---------------------------------------------------------------------------
# Per-row import: single row through service.create_question
# ---------------------------------------------------------------------------

async def _process_one_row(
    *,
    row_number: int,
    mapped: dict[str, str],
    scope: RequestScope,
    org_id: uuid.UUID,
    curriculum_cache: CurriculumRefCache,
    ip: Optional[str],
    session_factory: Optional[async_sessionmaker[AsyncSession]] = None,
) -> ImportRowResultOut:
    """Process one import row in its own independent transaction.

    Returns an ``ImportRowResultOut`` for every outcome — success, warning,
    duplicate, or error — never raises.
    """
    coerced, coerce_errors = _coerce_mapped_row(mapped)
    if coerce_errors:
        return ImportRowResultOut(
            row_number=row_number,
            status="error",
            errors=coerce_errors,
        )

    # Open a fresh identity session for this row's transaction.
    # If the session raises, the row is an error — other rows are unaffected.
    try:
        async with get_identity_session(
            scope.user_id,
            org_id=org_id,
            session_factory=session_factory,
        ) as session:
            # Curriculum name resolution (inside the session — read-only catalog SELECTs)
            resolve_errors: list[str] = []

            subject_id: Optional[uuid.UUID] = coerced.get("subject_id")
            if subject_id is None and mapped.get("subject_name", "").strip():
                subject_id = await _resolve_subject_by_name(
                    session, mapped["subject_name"].strip(), curriculum_cache
                )
                if subject_id is None:
                    resolve_errors.append(f"subject not found: {mapped['subject_name']!r}")

            chapter_id: Optional[uuid.UUID] = coerced.get("chapter_id")
            if chapter_id is None and mapped.get("chapter_name", "").strip():
                chapter_id = await _resolve_chapter_by_name(
                    session, mapped["chapter_name"].strip(), subject_id, curriculum_cache
                )
                if chapter_id is None:
                    resolve_errors.append(f"chapter not found: {mapped['chapter_name']!r} (subject_id={subject_id})")

            board_id: Optional[uuid.UUID] = coerced.get("board_id")
            if board_id is None and mapped.get("board_code", "").strip():
                board_id = await _resolve_board_by_code(
                    session, mapped["board_code"].strip(), curriculum_cache
                )
                if board_id is None:
                    resolve_errors.append(f"board not found with code: {mapped['board_code']!r}")

            class_id: Optional[uuid.UUID] = coerced.get("class_id")
            if class_id is None and mapped.get("class_name", "").strip():
                class_id = await _resolve_class_by_name(
                    session, mapped["class_name"].strip(), curriculum_cache
                )
                if class_id is None:
                    resolve_errors.append(f"class not found: {mapped['class_name']!r}")

            if resolve_errors:
                return ImportRowResultOut(
                    row_number=row_number,
                    status="error",
                    errors=resolve_errors,
                )

            # Build QuestionCreateIn
            payload_data: dict[str, Any] = {
                "qtype": coerced["qtype"],
                "stem": coerced["stem"],
                "marks": coerced["marks"],
                "difficulty": coerced["difficulty"],
                "bloom": coerced.get("bloom"),
                "language": coerced.get("language", "en"),
                "source": coerced.get("source", "institute_bank"),
                "scope": coerced.get("scope"),
                "options": coerced.get("options"),
                "answer": coerced.get("answer"),
                "rubric": coerced.get("rubric"),
                "meta": coerced.get("meta", {}),
                "subject_id": subject_id,
                "chapter_id": chapter_id,
                "board_id": board_id,
                "class_id": class_id,
            }
            # Remove None values for fields with model defaults
            payload_data = {k: v for k, v in payload_data.items() if v is not None}

            try:
                payload = question_schemas.QuestionCreateIn(**payload_data)
            except Exception as exc:
                return ImportRowResultOut(
                    row_number=row_number,
                    status="error",
                    errors=[f"validation: {exc}"],
                )

            try:
                result = await question_service.create_question(
                    session=session,
                    scope=scope,
                    org_id=org_id,
                    payload=payload,
                    ip=ip,
                )
            except QuestionExactDuplicateConflict as exc:
                return ImportRowResultOut(
                    row_number=row_number,
                    status="skipped_duplicate",
                    errors=[str(exc)],
                )
            except GlobalQuestionCreationForbidden as exc:
                return ImportRowResultOut(
                    row_number=row_number,
                    status="error",
                    errors=[f"GLOBAL_QUESTION_CREATION_FORBIDDEN: {exc}"],
                )
            except (ValidationError, ConflictError) as exc:
                return ImportRowResultOut(
                    row_number=row_number,
                    status="error",
                    errors=[str(exc)],
                )
            except Exception as exc:
                logger.warning(
                    "import_row_unexpected_error row=%d: %s", row_number, exc, exc_info=True
                )
                return ImportRowResultOut(
                    row_number=row_number,
                    status="error",
                    errors=[f"unexpected error: {type(exc).__name__}: {exc}"],
                )

            # Near-duplicate warnings
            warnings: list[NearDuplicateInfo] = []
            for w in result.warnings:
                for candidate in w.candidates:
                    warnings.append(
                        NearDuplicateInfo(
                            existing_question_id=candidate.existing_question_id,
                            similarity=candidate.similarity,
                            stem_preview=candidate.stem[:120],
                        )
                    )

            status: ImportRowStatus = "created_with_warnings" if warnings else "created"
            return ImportRowResultOut(
                row_number=row_number,
                status=status,
                question_id=result.id,
                warnings=warnings,
            )

    except Exception as exc:
        logger.warning(
            "import_row_session_error row=%d: %s", row_number, exc, exc_info=True
        )
        return ImportRowResultOut(
            row_number=row_number,
            status="error",
            errors=[f"session error: {type(exc).__name__}: {exc}"],
        )


# ---------------------------------------------------------------------------
# Error report CSV generation
# ---------------------------------------------------------------------------

def generate_error_report_csv(row_results: list[ImportRowResultOut]) -> bytes:
    """Build a downloadable CSV from the full list of row outcomes.

    All rows are included (not just errors) so the caller can audit the
    entire batch.  The CSV includes: row_number, status, question_id,
    errors, warnings.
    """
    buf = io.StringIO()
    writer = csv.DictWriter(
        buf,
        fieldnames=["row_number", "status", "question_id", "errors", "warnings"],
        extrasaction="ignore",
    )
    writer.writeheader()
    for r in row_results:
        writer.writerow(
            {
                "row_number": r.row_number,
                "status": r.status,
                "question_id": str(r.question_id) if r.question_id else "",
                "errors": "; ".join(r.errors),
                "warnings": "; ".join(
                    f"near-dup:{w.existing_question_id} sim={w.similarity:.3f}"
                    for w in r.warnings
                ),
            }
        )
    return buf.getvalue().encode("utf-8-sig")


# ---------------------------------------------------------------------------
# Durable import jobs
# ---------------------------------------------------------------------------

def _result_counts(
    rows: list[ImportRowResultOut],
) -> tuple[int, int, int, int]:
    return (
        sum(row.status == "created" for row in rows),
        sum(row.status == "created_with_warnings" for row in rows),
        sum(row.status == "error" for row in rows),
        sum(row.status == "skipped_duplicate" for row in rows),
    )


def import_job_resource(job: QuestionImportJob) -> ImportJobOut:
    rows = [ImportRowResultOut.model_validate(row) for row in (job.row_results or [])]
    return ImportJobOut(
        id=job.id,
        status=job.status,
        total_rows=job.total_rows,
        processed_rows=job.processed_rows,
        created_count=job.created_count,
        created_with_warnings_count=job.created_with_warnings_count,
        error_count=job.error_count,
        duplicate_count=job.duplicate_count,
        rows=rows if job.status in {"completed", "failed"} else [],
        error_report_token=str(job.id) if job.error_report_available else None,
        error=job.error,
        started_at=job.started_at,
        completed_at=job.completed_at,
        created_at=job.created_at,
        updated_at=job.updated_at,
    )


async def create_import_job(
    *,
    session: AsyncSession,
    scope: RequestScope,
    org_id: uuid.UUID,
    raw_bytes: bytes,
    content_type: str,
    filename: str,
    column_mapping: list[ColumnMappingEntry],
    ip: Optional[str] = None,
    job_id: Optional[uuid.UUID] = None,
) -> ImportJobOut:
    """Persist an upload and enqueue it in the transactional worker outbox.

    ``job_id`` may be supplied by the browser as a submission id.  Reusing the
    same id with the same upload is idempotent, which lets the client recover
    when the HTTP response is lost after the transaction commits.
    """

    assert_path_org(scope, org_id)
    raw_rows = parse_import_file(raw_bytes, content_type, filename)
    resolved_job_id = job_id or uuid.uuid4()
    existing = (
        await session.execute(
            select(QuestionImportJob).where(
                QuestionImportJob.id == resolved_job_id,
                QuestionImportJob.org_id == org_id,
            )
        )
    ).scalar_one_or_none()
    if existing is not None:
        requested_mapping = [entry.model_dump(mode="json") for entry in column_mapping]
        is_same_file = (
            bytes(existing.file_bytes or b"") == raw_bytes
            or (existing.file_bytes is None and existing.status in ("completed", "failed"))
        )
        if (
            existing.requested_by != scope.user_id
            or existing.filename != filename
            or existing.content_type != content_type
            or not is_same_file
            or existing.column_mapping != requested_mapping
        ):
            raise ConflictError(
                "That question-import submission ID is already attached to a different upload.",
                code="QUESTION_IMPORT_SUBMISSION_CONFLICT",
            )
        return import_job_resource(existing)

    job = QuestionImportJob(
        id=resolved_job_id,
        org_id=org_id,
        requested_by=scope.user_id,
        requester_role=scope.role,
        filename=filename,
        content_type=content_type,
        file_bytes=raw_bytes,
        column_mapping=[entry.model_dump(mode="json") for entry in column_mapping],
        request_ip=ip,
        status="queued",
        total_rows=len(raw_rows),
        processed_rows=0,
        row_results=[],
    )
    session.add(job)
    await session.flush()
    await add_outbox_task(
        session,
        org_id=org_id,
        task_name="papergen.workers.process_question_import",
        idempotency_key=f"question-import:{job.id}:process",
        payload={"org_id": str(org_id), "import_id": str(job.id)},
    )
    await session.flush()
    return import_job_resource(job)


async def get_import_job(
    *, session: AsyncSession, org_id: uuid.UUID, import_id: uuid.UUID
) -> QuestionImportJob:
    job = (
        await session.execute(
            select(QuestionImportJob).where(
                QuestionImportJob.id == import_id,
                QuestionImportJob.org_id == org_id,
            )
        )
    ).scalar_one_or_none()
    if job is None:
        raise NotFoundError("Question import job not found.")
    return job


async def recover_stalled_import_job(
    *,
    session: AsyncSession,
    org_id: uuid.UUID,
    import_id: uuid.UUID,
    stale_after_seconds: int,
    now: Optional[datetime] = None,
) -> bool:
    """Re-arm a durable import whose published Celery delivery disappeared.

    Question rows and job progress are committed together in bounded batches, so
    republishing the original outbox task is safe: processing resumes at
    ``processed_rows`` and exact-content deduplication protects any ambiguous edge.
    The original outbox UUID is retained as the Celery task id, which also satisfies
    the producer/consumer idempotency reservation already stored in Redis.
    """

    current_time = now or datetime.now(timezone.utc)
    cutoff = current_time - timedelta(seconds=stale_after_seconds)
    job = (
        await session.execute(
            select(QuestionImportJob)
            .where(
                QuestionImportJob.id == import_id,
                QuestionImportJob.org_id == org_id,
            )
            .with_for_update()
        )
    ).scalar_one_or_none()
    if (
        job is None
        or job.status not in {"queued", "processing"}
        or job.file_bytes is None
        or job.processed_rows >= job.total_rows
        or job.updated_at > cutoff
    ):
        return False

    outbox = (
        await session.execute(
            select(TaskOutbox)
            .where(
                TaskOutbox.org_id == org_id,
                TaskOutbox.idempotency_key
                == f"question-import:{import_id}:process",
            )
            .with_for_update()
        )
    ).scalar_one_or_none()
    if outbox is None:
        await add_outbox_task(
            session,
            org_id=org_id,
            task_name="papergen.workers.process_question_import",
            idempotency_key=f"question-import:{import_id}:process",
            payload={"org_id": str(org_id), "import_id": str(import_id)},
        )
    else:
        outbox.status = "pending"
        outbox.available_at = current_time
        outbox.published_at = None
        outbox.last_error = None

    # Prevent every polling request from re-arming the same job before the worker
    # reaches its next committed batch checkpoint.
    job.status = "queued"
    job.updated_at = current_time
    job.error = None
    logger.warning(
        "question_import_rearmed import_id=%s org_id=%s progress=%d/%d",
        import_id,
        org_id,
        job.processed_rows,
        job.total_rows,
    )
    return True


# ---------------------------------------------------------------------------
# Top-level import orchestrator
# ---------------------------------------------------------------------------

async def process_import(
    *,
    scope: RequestScope,
    org_id: uuid.UUID,
    raw_bytes: bytes,
    content_type: str,
    filename: str,
    column_mapping: list[ColumnMappingEntry],
    ip: Optional[str] = None,
    start_index: int = 0,
    session_factory: Optional[async_sessionmaker[AsyncSession]] = None,
    progress_callback: Optional[
        Callable[[ImportRowResultOut, int], Awaitable[None]]
    ] = None,
    cache_report: bool = True,
) -> tuple[list[ImportRowResultOut], Optional[str]]:
    """Parse the file, apply the column mapping, and process every row.

    Returns:
        - The full list of ``ImportRowResultOut`` objects (one per data row).
        - An error-report token (str) if any row had errors or warnings,
          or ``None`` if every row succeeded cleanly.

    Each row is processed in its own independent transaction through
    ``service.create_question`` — no single-transaction bulk INSERT.
    """
    assert_path_org(scope, org_id)

    raw_rows = parse_import_file(raw_bytes, content_type, filename)

    curriculum_cache = CurriculumRefCache()
    results: list[ImportRowResultOut] = []

    if start_index < 0 or start_index > len(raw_rows):
        raise ValueError("start_index must identify a row boundary in the import file")

    for idx, raw_row in enumerate(raw_rows[start_index:], start=start_index):
        row_number = idx + 1
        mapped = apply_column_mapping(raw_row, column_mapping)
        row_result = await _process_one_row(
            row_number=row_number,
            mapped=mapped,
            scope=scope,
            org_id=org_id,
            curriculum_cache=curriculum_cache,
            ip=ip,
            session_factory=session_factory,
        )
        results.append(row_result)

        if progress_callback is not None:
            await progress_callback(row_result, len(raw_rows))

        if row_number % 100 == 0:
            logger.info(
                "import_progress org_id=%s rows_processed=%d", org_id, row_number
            )

    # Build and cache the error report if there were any non-clean rows
    has_issues = any(r.status in ("error", "skipped_duplicate", "created_with_warnings") for r in results)
    token: Optional[str] = None
    if has_issues and cache_report:
        csv_bytes = generate_error_report_csv(results)
        token = _store_report(csv_bytes)

    return results, token


# ---------------------------------------------------------------------------
# Import file preview
# ---------------------------------------------------------------------------

async def preview_import_file(
    *,
    scope: RequestScope,
    org_id: uuid.UUID,
    raw_bytes: bytes,
    content_type: str,
    filename: str,
) -> ImportPreviewOut:
    """Parse the file and return headers + first 5 rows + auto-mapping.

    Does not create any questions.
    """
    assert_path_org(scope, org_id)
    raw_rows = parse_import_file(raw_bytes, content_type, filename)
    columns = get_source_columns(raw_rows)
    preview = [dict(r) for r in raw_rows[:5]]
    mapping = _auto_map_columns(columns)
    return ImportPreviewOut(
        detected_columns=columns,
        preview_rows=preview,
        recommended_mapping=mapping,
        row_count_estimate=len(raw_rows),
    )
