"""Structured contracts for the global curriculum import workflow.

The command-line importer remains a useful operator client, while these DTOs make
the same nested manifest available to authenticated browser tooling.  Requests are
strict and camel-cased like the rest of the API; responses are explicit allowlists.
"""

from __future__ import annotations

import uuid
from datetime import date
from typing import Any, Literal, Optional, Union

from pydantic import Field, model_validator

from app.modules.auth.schemas import AuthRequest, AuthResponse


ImportEntity = Literal[
    "boards",
    "classes",
    "curriculum_versions",
    "subjects",
    "chapters",
    "learning_outcomes",
]
ImportOperation = Literal["create", "skip", "transition"]
ImportVersionStatus = Literal["draft", "active", "superseded"]


class CurriculumImportOutcomeIn(AuthRequest):
    code: str = Field(min_length=1, max_length=100, pattern=r".*\S.*")
    description: str = Field(min_length=1, max_length=4000, pattern=r".*\S.*")


class CurriculumImportLineageIn(AuthRequest):
    academic_year: str = Field(min_length=1, max_length=64, pattern=r".*\S.*")
    class_name: str = Field(min_length=1, max_length=200, pattern=r".*\S.*")
    subject_name: str = Field(min_length=1, max_length=200, pattern=r".*\S.*")
    chapter_seq: int = Field(strict=True)


class CurriculumImportChapterIn(AuthRequest):
    seq: int = Field(strict=True)
    title: str = Field(min_length=1, max_length=500, pattern=r".*\S.*")
    derived_from: Optional[CurriculumImportLineageIn] = None
    learning_outcomes: list[CurriculumImportOutcomeIn]


class CurriculumImportSubjectIn(AuthRequest):
    name: str = Field(min_length=1, max_length=200, pattern=r".*\S.*")
    chapters: list[CurriculumImportChapterIn] = Field(min_length=1)


class CurriculumImportClassIn(AuthRequest):
    name: str = Field(min_length=1, max_length=200, pattern=r".*\S.*")
    subjects: list[CurriculumImportSubjectIn] = Field(min_length=1)


class CurriculumImportVersionByIdIn(AuthRequest):
    id: uuid.UUID
    final_status: ImportVersionStatus = "draft"
    supersede_active: bool = False


class CurriculumImportVersionByYearIn(AuthRequest):
    academic_year: str = Field(min_length=1, max_length=64, pattern=r".*\S.*")
    label: Optional[str] = Field(default=None, max_length=200)
    effective_from: Optional[date] = Field(default=None, description="ISO calendar date")
    source_ref: Optional[str] = Field(default=None, max_length=1000)
    final_status: ImportVersionStatus = "draft"
    supersede_active: bool = False


CurriculumImportVersionIn = Union[
    CurriculumImportVersionByIdIn,
    CurriculumImportVersionByYearIn,
]


class CurriculumImportBoardIn(AuthRequest):
    code: str = Field(min_length=1, max_length=64, pattern=r".*\S.*")
    name: str = Field(min_length=1, max_length=200, pattern=r".*\S.*")
    curriculum_version: CurriculumImportVersionIn
    classes: list[CurriculumImportClassIn] = Field(min_length=1)


class CurriculumImportDocumentIn(AuthRequest):
    schema_version: Literal[1]
    board: CurriculumImportBoardIn

    @model_validator(mode="after")
    def validate_manifest_semantics(self) -> "CurriculumImportDocumentIn":
        """Reject duplicate natural keys and impossible current-version lineage."""

        errors: list[str] = []
        version = self.board.curriculum_version
        desired_status = version.final_status
        if version.supersede_active and desired_status == "draft":
            errors.append(
                "$.board.curriculumVersion.supersedeActive: cannot be true when "
                "finalStatus is draft"
            )

        target_year = (
            version.academic_year
            if isinstance(version, CurriculumImportVersionByYearIn)
            else None
        )
        class_names: dict[str, int] = {}
        for class_index, school_class in enumerate(self.board.classes):
            class_path = f"$.board.classes[{class_index}]"
            if school_class.name in class_names:
                errors.append(
                    f"{class_path}.name: duplicate board-scoped class name; first used at "
                    f"$.board.classes[{class_names[school_class.name]}].name"
                )
            else:
                class_names[school_class.name] = class_index

            subject_names: dict[str, int] = {}
            for subject_index, subject in enumerate(school_class.subjects):
                subject_path = f"{class_path}.subjects[{subject_index}]"
                if subject.name in subject_names:
                    errors.append(
                        f"{subject_path}.name: duplicate subject name in this class; "
                        f"first used at {class_path}.subjects[{subject_names[subject.name]}].name"
                    )
                else:
                    subject_names[subject.name] = subject_index

                chapter_sequences: dict[int, int] = {}
                for chapter_index, chapter in enumerate(subject.chapters):
                    chapter_path = f"{subject_path}.chapters[{chapter_index}]"
                    if chapter.seq in chapter_sequences:
                        errors.append(
                            f"{chapter_path}.seq: duplicate subject-scoped chapter sequence; "
                            f"first used at {subject_path}.chapters[{chapter_sequences[chapter.seq]}].seq"
                        )
                    else:
                        chapter_sequences[chapter.seq] = chapter_index

                    if chapter.derived_from is not None and target_year is not None:
                        source = chapter.derived_from
                        source_path = f"{chapter_path}.derivedFrom"
                        if source.academic_year == target_year:
                            errors.append(
                                f"{source_path}.academicYear: lineage must name a different, "
                                "previous curriculum version"
                            )

                    outcome_codes: dict[str, int] = {}
                    for outcome_index, outcome in enumerate(chapter.learning_outcomes):
                        outcome_path = f"{chapter_path}.learningOutcomes[{outcome_index}]"
                        if outcome.code in outcome_codes:
                            errors.append(
                                f"{outcome_path}.code: duplicate chapter-scoped outcome code; "
                                f"first used at {chapter_path}.learningOutcomes["
                                f"{outcome_codes[outcome.code]}].code"
                            )
                        else:
                            outcome_codes[outcome.code] = outcome_index

        if errors:
            raise ValueError("; ".join(errors))
        return self


class CurriculumImportApplyIn(AuthRequest):
    document: CurriculumImportDocumentIn
    expected_plan_digest: str = Field(min_length=8, max_length=128)


class CurriculumImportEntityCountOut(AuthResponse):
    create: int = Field(ge=0)
    skip: int = Field(ge=0)


class CurriculumImportIssueOut(AuthResponse):
    path: str
    message: str
    severity: Literal["error", "warning"] = "error"


class CurriculumImportActionOut(AuthResponse):
    operation: ImportOperation
    entity: ImportEntity
    path: str
    key: str
    endpoint: str
    existing_id: Optional[uuid.UUID] = None
    payload: Optional[dict[str, Any]] = None
    from_status: Optional[ImportVersionStatus] = None
    to_status: Optional[ImportVersionStatus] = None


class CurriculumImportValidationOut(AuthResponse):
    valid: bool = True
    document_digest: str
    board_code: str
    version_key: str
    entity_totals: dict[ImportEntity, int]


class CurriculumImportPlanOut(AuthResponse):
    ready: bool
    atomic: bool = True
    document_digest: str
    plan_digest: str
    board_code: str
    version_key: str
    counts: dict[ImportEntity, CurriculumImportEntityCountOut]
    actions: list[CurriculumImportActionOut]
    issues: list[CurriculumImportIssueOut] = Field(default_factory=list)


class CurriculumImportResultOut(CurriculumImportPlanOut):
    committed: bool = True
    board_id: uuid.UUID
    curriculum_version_id: uuid.UUID


__all__ = [name for name in globals() if name.startswith("CurriculumImport")]
