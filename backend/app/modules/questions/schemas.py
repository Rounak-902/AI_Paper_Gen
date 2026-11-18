"""Pydantic schemas for the Question Bank module (P2-T3 through P2-T6).

All 13 question types (QT-1…QT-13, doc 01 §Question Types) use a single
``question_bank`` row with three JSONB columns — ``options``, ``answer``,
``rubric`` — whose shapes vary by ``qtype``. The split is:

* ``stem``   — the canonical, indexed question text (NOT NULL, full-text indexed)
* ``options`` — type-specific payload: MCQ option list, Match columns, sub-questions, etc.
* ``answer``  — the correct answer in a type-specific envelope
* ``rubric``  — marking scheme / key points (optional for simple types)

Per-type shapes (judgment calls, all flagged in the implementation plan):

QT-1  MCQ             stem=question, options=[{text,is_correct}]×4,   answer={index:0..3}
QT-2  True/False      stem=statement,                                   answer={value:bool}
QT-3  One Word        stem=question,                                    answer={value:str,synonyms:[str]}
QT-4  Fill Blank      stem=sentence with ___ markers,                  answer={blanks:[str]}
QT-5  Short Answer    stem=question,                                    answer={text:str}, rubric={key_points:[str]}
QT-6  Long Answer     stem=question,                                    answer={text:str}, rubric={marking_scheme:[{point,marks}]}
QT-7  Assertion-Reason stem=assertion, options={reason:str,rel_options:[str]}, answer={relationship:str}
QT-8  Case Study      stem=case passage, options={sub_questions:[{stem,marks,answer,rubric}]}
QT-9  Numerical       stem=problem,                                     answer={value:float,units:str,tolerance:float}, rubric={steps:[str]}
QT-10 Diagram Based   stem=question, options={asset_id:uuid|None},      answer={text:str}, rubric={labels:[str]}
QT-11 Match           stem=instruction, options={column_a:[str],column_b:[str],distractors:[str]}, answer={pairs:[[int,int]]}
QT-12 Very Short Ans  stem=question,                                    answer={text:str}
QT-13 Source Based    stem=source extract, options={sub_questions:[{stem,marks,answer}]}
"""

from __future__ import annotations

import decimal
import uuid
from typing import Any, Literal, Optional, Union

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator


# ---------------------------------------------------------------------------
# Vocabulary (mirrors app.models.question constants exactly)
# ---------------------------------------------------------------------------

QUESTION_TYPES = (
    "mcq",
    "true_false",
    "one_word",
    "fill_blank",
    "short_answer",
    "long_answer",
    "assertion_reason",
    "case_study",
    "numerical",
    "diagram_based",
    "match",
    "very_short_answer",
    "source_based",
)

QUESTION_SOURCES = (
    "institute_bank",
    "textbook",
    "intext",
    "previous_year",
    "ai_generated",
    "template_generated",
)

QUESTION_DIFFICULTIES = ("easy", "medium", "hard")

QUESTION_BLOOM_LEVELS = (
    "remember",
    "understand",
    "apply",
    "analyze",
    "evaluate",
    "create",
)

QUESTION_STATUSES = ("draft", "approved", "rejected", "archived")

QUESTION_SCOPES = ("global", "org")

# QT-7 Assertion-Reason standard four-option relationship vocabulary (CBSE standard)
ASSERTION_REASON_RELATIONSHIPS = (
    "both_true_reason_correct",
    "both_true_reason_incorrect",
    "assertion_true_reason_false",
    "both_false",
)


# ---------------------------------------------------------------------------
# Per-type options / answer / rubric shapes
# ---------------------------------------------------------------------------

class MCQOption(BaseModel):
    """One option in a QT-1 MCQ question."""
    model_config = ConfigDict(extra="forbid")
    text: str = Field(..., description="Option text")
    is_correct: bool = Field(..., description="Exactly one option must be correct")


class SubQuestion(BaseModel):
    """One sub-question inside QT-8 (Case Study) or QT-13 (Source Based)."""
    model_config = ConfigDict(extra="forbid")
    stem: str = Field(..., min_length=1, description="Sub-question text")
    marks: decimal.Decimal = Field(..., gt=0, description="Marks for this sub-question")
    answer: dict[str, Any] = Field(..., description="Answer envelope for this sub-question")
    rubric: Optional[dict[str, Any]] = Field(None, description="Rubric for this sub-question")

    @field_validator("answer")
    @classmethod
    def validate_answer(cls, value: dict[str, Any]) -> dict[str, Any]:
        if not value:
            raise ValueError("sub-question answer must not be empty")
        return value


class MarkingPoint(BaseModel):
    """One point in a QT-6 Long Answer marking scheme."""
    model_config = ConfigDict(extra="forbid")
    point: str
    marks: decimal.Decimal = Field(..., gt=0)


# ---------------------------------------------------------------------------
# Core question create / update payload
# ---------------------------------------------------------------------------

class QuestionCreateIn(BaseModel):
    """Payload to create any of the 13 question types.

    The caller always supplies ``options``, ``answer``, ``rubric`` as plain
    dicts — the schema validates the minimum structural requirements per
    ``qtype`` in the ``@model_validator``. Full per-type field enumeration in
    the module docstring.
    """
    model_config = ConfigDict(extra="forbid")

    # Classification
    qtype: str = Field(..., description="One of the 13 QT codes")
    marks: decimal.Decimal = Field(..., gt=0, description="Marks for this question")
    difficulty: str
    bloom: Optional[str] = None
    language: str = Field(default="en")
    source: str = Field(default="institute_bank")
    scope: Optional[str] = Field(
        None,
        description=(
            "Omit or set 'org' for tenant-owned questions. "
            "Only super_admin may create 'global' questions."
        ),
    )

    # Curriculum placement (all optional — questions need not be chapter-tagged)
    board_id: Optional[uuid.UUID] = None
    class_id: Optional[uuid.UUID] = None
    subject_id: Optional[uuid.UUID] = None
    chapter_id: Optional[uuid.UUID] = None

    # Content (type-specific shape — see module docstring)
    stem: str = Field(..., min_length=1, description="Canonical question text")
    options: Optional[Any] = Field(None, description="Type-specific options payload")
    answer: Optional[Any] = Field(None, description="Type-specific answer payload")
    rubric: Optional[Any] = Field(None, description="Type-specific rubric payload")

    # Taxonomy
    learning_outcome_ids: list[uuid.UUID] = Field(default_factory=list)
    competency_ids: list[uuid.UUID] = Field(default_factory=list)
    meta: dict[str, Any] = Field(default_factory=dict)

    # AI provenance
    prompt_version_id: Optional[uuid.UUID] = None

    @field_validator("qtype")
    @classmethod
    def validate_qtype(cls, v: str) -> str:
        if v not in QUESTION_TYPES:
            raise ValueError(f"qtype must be one of {QUESTION_TYPES}")
        return v

    @field_validator("difficulty")
    @classmethod
    def validate_difficulty(cls, v: str) -> str:
        if v not in QUESTION_DIFFICULTIES:
            raise ValueError(f"difficulty must be one of {QUESTION_DIFFICULTIES}")
        return v

    @field_validator("bloom")
    @classmethod
    def validate_bloom(cls, v: Optional[str]) -> Optional[str]:
        if v is not None and v not in QUESTION_BLOOM_LEVELS:
            raise ValueError(f"bloom must be one of {QUESTION_BLOOM_LEVELS}")
        return v

    @field_validator("source")
    @classmethod
    def validate_source(cls, v: str) -> str:
        if v not in QUESTION_SOURCES:
            raise ValueError(f"source must be one of {QUESTION_SOURCES}")
        return v

    @field_validator("scope")
    @classmethod
    def validate_scope(cls, v: Optional[str]) -> Optional[str]:
        if v is not None and v not in QUESTION_SCOPES:
            raise ValueError(f"scope must be one of {QUESTION_SCOPES}")
        return v

    @model_validator(mode="after")
    def validate_type_specific_fields(self) -> "QuestionCreateIn":
        """Light structural validation per qtype.

        We validate the minimum shape rather than deep field-level checking —
        the full business rules (e.g. MCQ exactly-one-correct-option, blank count
        equals answer count) are enforced in the service layer where we have
        richer context.
        """
        qt = self.qtype
        opts = self.options
        ans = self.answer
        # Textbook extraction imports may intentionally create an incomplete
        # draft when the source does not contain a trustworthy answer.  The
        # explicit review flag keeps ordinary create requests strict while
        # allowing those drafts to reach the review queue without inventing an
        # answer merely to satisfy the transport schema.
        answer_review_pending = (
            ans is None and self.meta.get("answer_review_required") is True
        )

        # QT-1 MCQ: options must be a non-empty list; answer must have 'index'
        if qt == "mcq":
            if not isinstance(opts, list) or len(opts) < 2:
                raise ValueError("MCQ requires options as a list of at least 2 items")
            if not isinstance(ans, dict) or "index" not in ans:
                raise ValueError("MCQ answer must be {'index': <int>}")
            correct_count = sum(1 for o in opts if isinstance(o, dict) and o.get("is_correct"))
            if correct_count != 1:
                raise ValueError("MCQ must have exactly one option with is_correct=true")

        # QT-2 True/False: answer must have boolean 'value'
        elif qt == "true_false":
            if not isinstance(ans, dict) or "value" not in ans:
                raise ValueError("true_false answer must be {'value': true/false}")
            if not isinstance(ans["value"], bool):
                raise ValueError("true_false answer.value must be a boolean")

        # QT-3 One Word: answer must have 'value'
        elif qt == "one_word":
            if not isinstance(ans, dict) or not ans.get("value"):
                raise ValueError("one_word answer must be {'value': '<word>', 'synonyms': [...]}")

        # QT-4 Fill Blank: answer must have non-empty 'blanks' list
        elif qt == "fill_blank":
            if not isinstance(ans, dict) or not isinstance(ans.get("blanks"), list) or not ans["blanks"]:
                raise ValueError("fill_blank answer must be {'blanks': ['<answer1>', ...]}")
            # Count blanks in stem
            blank_count = self.stem.count("___")
            if blank_count > 0 and blank_count != len(ans["blanks"]):
                raise ValueError(
                    f"fill_blank: stem has {blank_count} blank(s) but answer has {len(ans['blanks'])} value(s)"
                )

        # QT-5/QT-6/QT-12: answer must have 'text'
        elif qt in ("short_answer", "long_answer", "very_short_answer"):
            if not answer_review_pending and (
                not isinstance(ans, dict) or not ans.get("text")
            ):
                raise ValueError(f"{qt} answer must be {{'text': '<answer text>'}}")

        # QT-7 Assertion-Reason: options.reason required; answer.relationship must be valid
        elif qt == "assertion_reason":
            if not isinstance(opts, dict) or not opts.get("reason"):
                raise ValueError("assertion_reason options must be {'reason': '<reason text>', ...}")
            if not isinstance(ans, dict) or ans.get("relationship") not in ASSERTION_REASON_RELATIONSHIPS:
                raise ValueError(
                    f"assertion_reason answer.relationship must be one of {ASSERTION_REASON_RELATIONSHIPS}"
                )

        # QT-8 Case Study / QT-13 Source Based: options.sub_questions required
        elif qt in ("case_study", "source_based"):
            if not isinstance(opts, dict) or not isinstance(opts.get("sub_questions"), list):
                raise ValueError(f"{qt} options must be {{'sub_questions': [...]}}")
            if len(opts["sub_questions"]) < 2:
                raise ValueError(f"{qt} requires at least 2 sub-questions")
            validated = [
                SubQuestion.model_validate(item) for item in opts["sub_questions"]
            ]
            # Store the validated canonical shape so malformed/extra answer fields
            # cannot reach assembly and silently disappear from an answer key.
            self.options = {
                **opts,
                "sub_questions": [item.model_dump(mode="json") for item in validated],
            }
            sub_marks = sum((item.marks for item in validated), decimal.Decimal(0))
            if sub_marks != self.marks:
                raise ValueError(
                    f"{qt} sub-question marks ({sub_marks}) must sum to parent marks ({self.marks})"
                )

        # QT-9 Numerical: answer must have 'value' and 'units'
        elif qt == "numerical":
            if not answer_review_pending and (
                not isinstance(ans, dict)
                or "value" not in ans
                or not ans.get("units")
            ):
                raise ValueError("numerical answer must be {'value': <num>, 'units': '<unit>', ...}")

        # QT-10 Diagram Based: answer must have 'text'
        elif qt == "diagram_based":
            if not answer_review_pending and (
                not isinstance(ans, dict) or not ans.get("text")
            ):
                raise ValueError("diagram_based answer must be {'text': '<answer>'}")

        # QT-11 Match: options must have column_a and column_b; answer must have pairs
        elif qt == "match":
            if not isinstance(opts, dict) or not isinstance(opts.get("column_a"), list) or \
               not isinstance(opts.get("column_b"), list):
                raise ValueError("match options must be {'column_a': [...], 'column_b': [...]}")
            if not isinstance(ans, dict) or not isinstance(ans.get("pairs"), list):
                raise ValueError("match answer must be {'pairs': [[a_idx, b_idx], ...]}")

        return self


class QuestionUpdateIn(BaseModel):
    """PATCH payload for editing a question.

    Editing a *draft* question updates it in place.
    Editing an *approved* question forks a new version (BR-8).

    ``row_version`` is mandatory for optimistic locking on in-place edits;
    it is also passed for fork edits so the service can verify the caller
    is looking at the correct version before forking.
    """
    model_config = ConfigDict(extra="forbid")

    row_version: int = Field(..., description="Current row_version for optimistic locking")

    # All content fields are optional — only supplied fields are changed
    stem: Optional[str] = Field(None, min_length=1)
    options: Optional[Any] = None
    answer: Optional[Any] = None
    rubric: Optional[Any] = None
    marks: Optional[decimal.Decimal] = Field(None, gt=0)
    difficulty: Optional[str] = None
    bloom: Optional[str] = None
    language: Optional[str] = None
    board_id: Optional[uuid.UUID] = None
    class_id: Optional[uuid.UUID] = None
    subject_id: Optional[uuid.UUID] = None
    chapter_id: Optional[uuid.UUID] = None
    learning_outcome_ids: Optional[list[uuid.UUID]] = None
    competency_ids: Optional[list[uuid.UUID]] = None
    meta: Optional[dict[str, Any]] = None

    @field_validator("difficulty")
    @classmethod
    def validate_difficulty(cls, v: Optional[str]) -> Optional[str]:
        if v is not None and v not in QUESTION_DIFFICULTIES:
            raise ValueError(f"difficulty must be one of {QUESTION_DIFFICULTIES}")
        return v

    @field_validator("bloom")
    @classmethod
    def validate_bloom(cls, v: Optional[str]) -> Optional[str]:
        if v is not None and v not in QUESTION_BLOOM_LEVELS:
            raise ValueError(f"bloom must be one of {QUESTION_BLOOM_LEVELS}")
        return v


class QuestionApproveIn(BaseModel):
    """Payload to approve a draft question."""
    model_config = ConfigDict(extra="forbid")
    row_version: int = Field(..., description="Current row_version for optimistic locking")


class QuestionStatusIn(BaseModel):
    """Generic status transition (reject / archive)."""
    model_config = ConfigDict(extra="forbid")
    status: str = Field(..., description="Target status: rejected or archived")
    row_version: int

    @field_validator("status")
    @classmethod
    def validate_status(cls, v: str) -> str:
        if v not in ("rejected", "archived"):
            raise ValueError("status must be 'rejected' or 'archived'")
        return v


# ---------------------------------------------------------------------------
# Search / filter params
# ---------------------------------------------------------------------------

class QuestionSearchParams(BaseModel):
    """Query parameters for listing / searching questions."""
    model_config = ConfigDict(extra="forbid", populate_by_name=True)

    # Full-text search
    q: Optional[str] = Field(
        None,
        max_length=500,
        description="Full-text search query against stem, answer, and rubric",
    )

    # Facet filters
    board_id: Optional[uuid.UUID] = None
    class_id: Optional[uuid.UUID] = None
    subject_id: Optional[uuid.UUID] = None
    chapter_id: Optional[uuid.UUID] = None
    qtype: Optional[str] = None
    difficulty: Optional[str] = None
    bloom: Optional[str] = None
    source: Optional[str] = None
    status: Optional[str] = Field(
        "approved",
        description="Filter by status; normal bank search defaults to approved",
    )
    scope: Optional[str] = None

    # Pagination
    page: int = Field(default=1, ge=1)
    page_size: int = Field(default=20, ge=1, le=100, alias="pageSize")

    # Version filter
    current_only: bool = Field(
        default=True,
        description="Only return is_current=true rows; set false to include lineage history",
    )

    @property
    def offset(self) -> int:
        return (self.page - 1) * self.page_size

    @field_validator("q")
    @classmethod
    def normalize_query(cls, v: Optional[str]) -> Optional[str]:
        if v is None:
            return None
        return v.strip() or None

    @field_validator("qtype")
    @classmethod
    def validate_qtype(cls, v: Optional[str]) -> Optional[str]:
        if v is not None and v not in QUESTION_TYPES:
            raise ValueError(f"qtype must be one of {QUESTION_TYPES}")
        return v

    @field_validator("difficulty")
    @classmethod
    def validate_difficulty(cls, v: Optional[str]) -> Optional[str]:
        if v is not None and v not in QUESTION_DIFFICULTIES:
            raise ValueError(f"difficulty must be one of {QUESTION_DIFFICULTIES}")
        return v

    @field_validator("bloom")
    @classmethod
    def validate_bloom(cls, v: Optional[str]) -> Optional[str]:
        if v is not None and v not in QUESTION_BLOOM_LEVELS:
            raise ValueError(f"bloom must be one of {QUESTION_BLOOM_LEVELS}")
        return v

    @field_validator("source")
    @classmethod
    def validate_source(cls, v: Optional[str]) -> Optional[str]:
        if v is not None and v not in QUESTION_SOURCES:
            raise ValueError(f"source must be one of {QUESTION_SOURCES}")
        return v

    @field_validator("status")
    @classmethod
    def validate_status(cls, v: Optional[str]) -> Optional[str]:
        if v is not None and v not in QUESTION_STATUSES:
            raise ValueError(f"status must be one of {QUESTION_STATUSES}")
        return v

    @field_validator("scope")
    @classmethod
    def validate_scope(cls, v: Optional[str]) -> Optional[str]:
        if v is not None and v not in QUESTION_SCOPES:
            raise ValueError(f"scope must be one of {QUESTION_SCOPES}")
        return v


# ---------------------------------------------------------------------------
# Response schemas
# ---------------------------------------------------------------------------

class QuestionOut(BaseModel):
    """Full question row as returned by the API."""
    model_config = ConfigDict(from_attributes=True)

    id: uuid.UUID
    root_question_id: Optional[uuid.UUID]
    previous_version_id: Optional[uuid.UUID]
    version_number: int
    is_current: bool

    org_id: Optional[uuid.UUID]
    scope: str
    source: str

    board_id: Optional[uuid.UUID]
    class_id: Optional[uuid.UUID]
    subject_id: Optional[uuid.UUID]
    chapter_id: Optional[uuid.UUID]

    qtype: str
    marks: decimal.Decimal
    difficulty: str
    bloom: Optional[str]
    language: str

    stem: str
    options: Optional[Any]
    answer: Optional[Any]
    rubric: Optional[Any]

    learning_outcome_ids: list[uuid.UUID]
    competency_ids: list[uuid.UUID]
    meta: dict[str, Any]

    content_hash: str
    status: str
    created_by: Optional[uuid.UUID]
    prompt_version_id: Optional[uuid.UUID]

    created_at: Any  # datetime — Any to avoid tz import dance
    updated_at: Any
    deleted_at: Optional[Any]
    row_version: int


class NearDuplicateCandidate(BaseModel):
    """A visible existing question similar to a newly created question."""

    existing_question_id: uuid.UUID
    root_question_id: Optional[uuid.UUID]
    qtype: str
    scope: str
    stem: str
    similarity: float = Field(ge=0.0, le=1.0)


class QuestionCreateWarning(BaseModel):
    """Structured, non-blocking warning returned by question creation."""

    code: Literal["NEAR_DUPLICATE"] = "NEAR_DUPLICATE"
    message: str
    threshold: float = Field(ge=0.0, le=1.0)
    candidates: list[NearDuplicateCandidate]


class QuestionCreateOut(QuestionOut):
    """Create response: the question plus any advisory dedup warnings."""

    warnings: list[QuestionCreateWarning] = Field(default_factory=list)


class ForkResult(BaseModel):
    """Result of a fork-on-edit operation (BR-8)."""
    model_config = ConfigDict(from_attributes=True)
    new_version: QuestionOut
    previous_version_id: uuid.UUID
    root_question_id: uuid.UUID
    version_number: int
