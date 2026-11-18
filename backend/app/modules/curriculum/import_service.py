"""Read-only planning and atomic execution for nested curriculum manifests."""

from __future__ import annotations

import hashlib
import json
import uuid
from dataclasses import dataclass, field
from typing import Any, Iterable, Mapping, Optional, Sequence

from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from app.common.audit_log import ACTIONS, record_audit
from app.common.deps import OrgContext
from app.common.envelope import ErrorDetail
from app.common.errors import ConflictError, ValidationError
from app.models.curriculum import (
    Board,
    Chapter,
    Class as SchoolClass,
    CurriculumVersion,
    LearningOutcome,
    Subject,
)
from app.modules.curriculum import service
from app.modules.curriculum.import_schemas import (
    CurriculumImportActionOut,
    CurriculumImportApplyIn,
    CurriculumImportDocumentIn,
    CurriculumImportEntityCountOut,
    CurriculumImportIssueOut,
    CurriculumImportPlanOut,
    CurriculumImportResultOut,
    CurriculumImportValidationOut,
)
from app.modules.curriculum.repository import (
    BoardRepository,
    ChapterRepository,
    ClassRepository,
    CurriculumVersionRepository,
    OutcomeRepository,
    SubjectRepository,
)
from app.modules.curriculum.schemas import (
    BoardCreateIn,
    BoardOut,
    ChapterCreateIn,
    ChapterOut,
    ClassCreateIn,
    ClassOut,
    CurriculumVersionCreateIn,
    CurriculumVersionOut,
    CurriculumVersionUpdateIn,
    OutcomeCreateIn,
    OutcomeOut,
    SubjectCreateIn,
    SubjectOut,
)


ENTITY_ORDER = (
    "boards",
    "classes",
    "curriculum_versions",
    "subjects",
    "chapters",
    "learning_outcomes",
)


class ImportPreflightError(Exception):
    def __init__(self, path: str, message: str) -> None:
        self.path = path
        self.message = message
        super().__init__(f"{path}: {message}")


@dataclass
class EntityAction:
    kind: str
    path: str
    key: str
    endpoint: str
    payload: dict[str, Any]
    existing: Optional[Mapping[str, Any]] = None
    result: Optional[Mapping[str, Any]] = None

    @property
    def is_create(self) -> bool:
        return self.existing is None

    @property
    def row(self) -> Optional[Mapping[str, Any]]:
        return self.result or self.existing

    @property
    def row_id(self) -> str:
        row = self.row
        if not row or not row.get("id"):
            raise RuntimeError(f"{self.kind} action {self.path} has no resolved id")
        return str(row["id"])


@dataclass
class StatusTransition:
    path: str
    key: str
    from_status: str
    to_status: str
    action: Optional[EntityAction] = None
    row: Optional[Mapping[str, Any]] = None

    def current_row(self) -> Mapping[str, Any]:
        if self.action is not None:
            resolved = self.action.row
            if resolved is None:
                raise RuntimeError(f"version action {self.path} is unresolved")
            return resolved
        if self.row is None:
            raise RuntimeError(f"version transition {self.path} is unresolved")
        return self.row

    def replace_row(self, row: Mapping[str, Any]) -> None:
        if self.action is not None:
            self.action.result = row
        else:
            self.row = row


@dataclass
class ImportPlan:
    groups: dict[str, list[EntityAction]] = field(
        default_factory=lambda: {kind: [] for kind in ENTITY_ORDER}
    )
    transitions: list[StatusTransition] = field(default_factory=list)

    def actions(self) -> Iterable[EntityAction]:
        for kind in ENTITY_ORDER:
            yield from self.groups[kind]


def _row_dict(model_type: Any, row: Any) -> Mapping[str, Any]:
    return model_type.model_validate(row).model_dump(by_alias=True, mode="json")


class Planner:
    """State-aware read-only planner using the same natural keys as the CLI."""

    def __init__(
        self,
        session: AsyncSession,
        ctx: OrgContext,
        document: CurriculumImportDocumentIn,
    ) -> None:
        self.session = session
        self.ctx = ctx
        self.document = document.model_dump(
            by_alias=True,
            mode="json",
            exclude_unset=True,
        )
        self.plan_result = ImportPlan()
        self._boards: Optional[list[Mapping[str, Any]]] = None
        self._classes: dict[str, list[Mapping[str, Any]]] = {}
        self._versions: dict[str, list[Mapping[str, Any]]] = {}
        self._subjects: dict[tuple[str, str], list[Mapping[str, Any]]] = {}
        self._chapters: dict[tuple[str, str], list[Mapping[str, Any]]] = {}
        self._outcomes: dict[str, list[Mapping[str, Any]]] = {}

    async def build(self) -> ImportPlan:
        board_input = self.document["board"]
        board_path = "$.board"
        board_existing = self._one(
            await self._list_boards(),
            lambda row: row.get("code") == board_input["code"],
            path=f"{board_path}.code",
            key=f"code={board_input['code']!r}",
        )
        if board_existing is not None:
            self._same(
                board_existing.get("name"),
                board_input["name"],
                path=f"{board_path}.name",
                key=f"board code={board_input['code']!r}",
            )
        board_action = self._add(
            "boards",
            path=board_path,
            key=f"code={board_input['code']!r}",
            endpoint="/api/v1/boards",
            payload={"code": board_input["code"], "name": board_input["name"]},
            existing=board_existing,
        )

        class_entries: list[tuple[Mapping[str, Any], str, EntityAction]] = []
        available_classes = (
            await self._list_classes(board_action.row_id)
            if board_existing is not None
            else []
        )
        for class_index, class_input in enumerate(board_input["classes"]):
            class_path = f"{board_path}.classes[{class_index}]"
            class_existing = self._one(
                available_classes,
                lambda row, name=class_input["name"]: row.get("name") == name,
                path=f"{class_path}.name",
                key=f"board={board_input['code']!r}, name={class_input['name']!r}",
            )
            class_action = self._add(
                "classes",
                path=class_path,
                key=f"board={board_input['code']!r}, name={class_input['name']!r}",
                endpoint="/api/v1/classes",
                payload={"boardId": board_action, "name": class_input["name"]},
                existing=class_existing,
            )
            class_entries.append((class_input, class_path, class_action))

        version_input = board_input["curriculumVersion"]
        version_path = f"{board_path}.curriculumVersion"
        version_action, known_versions = await self._version_action(
            board_action, board_input, version_input, version_path
        )

        for class_input, class_path, class_action in class_entries:
            available_subjects = (
                await self._list_subjects(class_action.row_id, version_action.row_id)
                if class_action.existing is not None
                and version_action.existing is not None
                else []
            )
            for subject_index, subject_input in enumerate(class_input["subjects"]):
                subject_path = f"{class_path}.subjects[{subject_index}]"
                subject_existing = self._one(
                    available_subjects,
                    lambda row, name=subject_input["name"]: row.get("name") == name,
                    path=f"{subject_path}.name",
                    key=(
                        f"class={class_input['name']!r}, "
                        f"version={self._version_key(version_input)}, "
                        f"name={subject_input['name']!r}"
                    ),
                )
                subject_action = self._add(
                    "subjects",
                    path=subject_path,
                    key=(
                        f"class={class_input['name']!r}, "
                        f"version={self._version_key(version_input)}, "
                        f"name={subject_input['name']!r}"
                    ),
                    endpoint="/api/v1/subjects",
                    payload={
                        "classId": class_action,
                        "curriculumVersionId": version_action,
                        "name": subject_input["name"],
                    },
                    existing=subject_existing,
                )
                available_chapters = (
                    await self._list_chapters(subject_action.row_id, version_action.row_id)
                    if subject_action.existing is not None
                    and version_action.existing is not None
                    else []
                )
                await self._prime_outcomes(available_chapters)
                for chapter_index, chapter_input in enumerate(subject_input["chapters"]):
                    chapter_path = f"{subject_path}.chapters[{chapter_index}]"
                    source = None
                    if chapter_input.get("derivedFrom") is not None:
                        source = await self._resolve_lineage_source(
                            chapter_input["derivedFrom"],
                            path=f"{chapter_path}.derivedFrom",
                            board_action=board_action,
                            target_version_action=version_action,
                        )
                    chapter_existing = self._one(
                        available_chapters,
                        lambda row, seq=chapter_input["seq"]: row.get("seq") == seq,
                        path=f"{chapter_path}.seq",
                        key=f"subject={subject_input['name']!r}, seq={chapter_input['seq']}",
                    )
                    desired_source_id = str(source["id"]) if source is not None else None
                    if chapter_existing is not None:
                        self._same(
                            chapter_existing.get("title"),
                            chapter_input["title"],
                            path=f"{chapter_path}.title",
                            key=f"chapter subject={subject_input['name']!r}, seq={chapter_input['seq']}",
                        )
                        self._same(
                            _optional_string(chapter_existing.get("derivedFromChapterId")),
                            desired_source_id,
                            path=f"{chapter_path}.derivedFrom",
                            key=f"chapter subject={subject_input['name']!r}, seq={chapter_input['seq']}",
                        )
                    chapter_payload: dict[str, Any] = {
                        "subjectId": subject_action,
                        "curriculumVersionId": version_action,
                        "seq": chapter_input["seq"],
                        "title": chapter_input["title"],
                    }
                    if desired_source_id is not None:
                        chapter_payload["derivedFromChapterId"] = desired_source_id
                    chapter_action = self._add(
                        "chapters",
                        path=chapter_path,
                        key=f"subject={subject_input['name']!r}, seq={chapter_input['seq']}",
                        endpoint="/api/v1/chapters",
                        payload=chapter_payload,
                        existing=chapter_existing,
                    )
                    available_outcomes = (
                        await self._list_outcomes(chapter_action.row_id)
                        if chapter_action.existing is not None
                        else []
                    )
                    for outcome_index, outcome_input in enumerate(
                        chapter_input["learningOutcomes"]
                    ):
                        outcome_path = f"{chapter_path}.learningOutcomes[{outcome_index}]"
                        outcome_existing = self._one(
                            available_outcomes,
                            lambda row, code=outcome_input["code"]: row.get("code") == code,
                            path=f"{outcome_path}.code",
                            key=(
                                f"chapter seq={chapter_input['seq']}, "
                                f"code={outcome_input['code']!r}"
                            ),
                        )
                        if outcome_existing is not None:
                            self._same(
                                outcome_existing.get("description"),
                                outcome_input["description"],
                                path=f"{outcome_path}.description",
                                key=f"outcome code={outcome_input['code']!r}",
                            )
                        self._add(
                            "learning_outcomes",
                            path=outcome_path,
                            key=(
                                f"chapter seq={chapter_input['seq']}, "
                                f"code={outcome_input['code']!r}"
                            ),
                            endpoint="/api/v1/outcomes",
                            payload={
                                "chapterId": chapter_action,
                                "code": outcome_input["code"],
                                "description": outcome_input["description"],
                            },
                            existing=outcome_existing,
                        )

        self._plan_version_status(
            version_input=version_input,
            version_path=version_path,
            version_action=version_action,
            known_versions=known_versions,
        )
        return self.plan_result

    async def _version_action(
        self,
        board_action: EntityAction,
        board_input: Mapping[str, Any],
        version_input: Mapping[str, Any],
        version_path: str,
    ) -> tuple[EntityAction, list[Mapping[str, Any]]]:
        known_versions: list[Mapping[str, Any]] = []
        existing: Optional[Mapping[str, Any]] = None
        payload: dict[str, Any] = {}
        if "id" in version_input:
            if board_action.existing is None:
                raise ImportPreflightError(
                    f"{version_path}.id",
                    "cannot target an existing version while its board would be created",
                )
            row = await CurriculumVersionRepository(
                self.session, self.ctx.org_id
            ).get_by_id(uuid.UUID(str(version_input["id"])))
            if row is None:
                raise ImportPreflightError(
                    f"{version_path}.id", "target curriculum version was not found"
                )
            existing = _row_dict(CurriculumVersionOut, row)
            if str(existing.get("boardId")) != board_action.row_id:
                raise ImportPreflightError(
                    f"{version_path}.id",
                    "target curriculum version belongs to a different board",
                )
            known_versions = await self._list_versions(board_action.row_id)
            key = f"id={version_input['id']}"
        else:
            if board_action.existing is not None:
                known_versions = await self._list_versions(board_action.row_id)
                existing = self._one(
                    known_versions,
                    lambda row: row.get("academicYear") == version_input["academicYear"],
                    path=f"{version_path}.academicYear",
                    key=(
                        f"board={board_input['code']!r}, "
                        f"academicYear={version_input['academicYear']!r}"
                    ),
                )
            key = (
                f"board={board_input['code']!r}, "
                f"academicYear={version_input['academicYear']!r}"
            )
            payload = {
                "boardId": board_action,
                "academicYear": version_input["academicYear"],
            }
            for field_name in ("label", "effectiveFrom", "sourceRef"):
                if field_name in version_input:
                    payload[field_name] = version_input[field_name]
                    if existing is not None:
                        self._same(
                            existing.get(field_name),
                            version_input[field_name],
                            path=f"{version_path}.{field_name}",
                            key=f"curriculum version {key}",
                        )

        action = self._add(
            "curriculum_versions",
            path=version_path,
            key=key,
            endpoint="/api/v1/curriculum-versions",
            payload=payload,
            existing=existing,
        )
        return action, known_versions

    def _plan_version_status(
        self,
        *,
        version_input: Mapping[str, Any],
        version_path: str,
        version_action: EntityAction,
        known_versions: Sequence[Mapping[str, Any]],
    ) -> None:
        desired = str(version_input.get("finalStatus", "draft"))
        current = (
            str(version_action.existing.get("status"))
            if version_action.existing is not None
            else "draft"
        )
        version_owned_creates = sum(
            action.is_create
            for kind in ("subjects", "chapters", "learning_outcomes")
            for action in self.plan_result.groups[kind]
        )
        if current == "superseded" and version_owned_creates:
            raise ImportPreflightError(
                version_path,
                f"target version is {current!r} and immutable, but "
                f"{version_owned_creates} nested row(s) are missing",
            )

        rank = {"draft": 0, "active": 1, "superseded": 2}
        if rank[current] > rank[desired]:
            raise ImportPreflightError(
                f"{version_path}.finalStatus",
                f"cannot regress existing status {current!r} to {desired!r}",
            )
        target_steps: list[str] = []
        if current == "draft" and desired in {"active", "superseded"}:
            target_steps.append("active")
        if desired == "superseded" and current != "superseded":
            target_steps.append("superseded")

        activation_needed = "active" in target_steps
        other_active = self._one(
            known_versions,
            lambda row: (
                row.get("status") == "active"
                and (
                    version_action.existing is None
                    or str(row.get("id")) != version_action.row_id
                )
            ),
            path=f"{version_path}.finalStatus",
            key="another active version for this board",
        )
        if activation_needed and other_active is not None:
            if not version_input.get("supersedeActive", False):
                raise ImportPreflightError(
                    f"{version_path}.finalStatus",
                    "activation would replace another active version; set "
                    "supersedeActive=true to confirm that transition",
                )
            self.plan_result.transitions.append(
                StatusTransition(
                    path=f"{version_path}.supersedeActive",
                    key=(
                        f"existing active academicYear={other_active.get('academicYear')!r}, "
                        f"id={other_active.get('id')}"
                    ),
                    from_status="active",
                    to_status="superseded",
                    row=other_active,
                )
            )
        step_from = current
        for next_status in target_steps:
            self.plan_result.transitions.append(
                StatusTransition(
                    path=f"{version_path}.finalStatus",
                    key=version_action.key,
                    from_status=step_from,
                    to_status=next_status,
                    action=version_action,
                )
            )
            step_from = next_status

    async def _resolve_lineage_source(
        self,
        selector: Mapping[str, Any],
        *,
        path: str,
        board_action: EntityAction,
        target_version_action: EntityAction,
    ) -> Mapping[str, Any]:
        if board_action.existing is None:
            raise ImportPreflightError(
                path, "cannot resolve prior-version lineage because the board is new"
            )
        source_class = self._one(
            await self._list_classes(board_action.row_id),
            lambda row: row.get("name") == selector["className"],
            path=f"{path}.className",
            key=f"class name={selector['className']!r}",
        )
        if source_class is None:
            raise ImportPreflightError(
                f"{path}.className", "no live class matches the selector"
            )
        source_version = self._one(
            await self._list_versions(board_action.row_id),
            lambda row: row.get("academicYear") == selector["academicYear"],
            path=f"{path}.academicYear",
            key=f"academicYear={selector['academicYear']!r}",
        )
        if source_version is None:
            raise ImportPreflightError(
                f"{path}.academicYear", "no live curriculum version matches the selector"
            )
        if (
            target_version_action.existing is not None
            and str(source_version.get("id")) == target_version_action.row_id
        ):
            raise ImportPreflightError(path, "lineage must resolve to a different version")
        if source_version.get("status") != "active":
            raise ImportPreflightError(
                f"{path}.academicYear", "lineage source version must be active"
            )
        source_subject = self._one(
            await self._list_subjects(
                str(source_class["id"]), str(source_version["id"])
            ),
            lambda row: row.get("name") == selector["subjectName"],
            path=f"{path}.subjectName",
            key=f"subject name={selector['subjectName']!r}",
        )
        if source_subject is None:
            raise ImportPreflightError(
                f"{path}.subjectName", "no subject matches the selected class and version"
            )
        source_chapter = self._one(
            await self._list_chapters(
                str(source_subject["id"]), str(source_version["id"])
            ),
            lambda row: row.get("seq") == selector["chapterSeq"],
            path=f"{path}.chapterSeq",
            key=f"chapter seq={selector['chapterSeq']}",
        )
        if source_chapter is None:
            raise ImportPreflightError(
                f"{path}.chapterSeq", "no chapter matches the selected subject and version"
            )
        return source_chapter

    def _add(
        self,
        kind: str,
        *,
        path: str,
        key: str,
        endpoint: str,
        payload: dict[str, Any],
        existing: Optional[Mapping[str, Any]],
    ) -> EntityAction:
        action = EntityAction(kind, path, key, endpoint, payload, existing)
        self.plan_result.groups[kind].append(action)
        return action

    @staticmethod
    def _one(
        rows: Iterable[Mapping[str, Any]],
        predicate: Any,
        *,
        path: str,
        key: str,
    ) -> Optional[Mapping[str, Any]]:
        matches = [row for row in rows if predicate(row)]
        if len(matches) > 1:
            raise ImportPreflightError(
                path,
                f"natural key {key} matched multiple live rows: "
                f"{[str(row.get('id')) for row in matches]}",
            )
        return matches[0] if matches else None

    @staticmethod
    def _same(actual: Any, desired: Any, *, path: str, key: str) -> None:
        if actual != desired:
            raise ImportPreflightError(
                path,
                f"existing {key} has {actual!r}, input requests {desired!r}; "
                "refusing to treat drift as idempotent",
            )

    @staticmethod
    def _version_key(version_input: Mapping[str, Any]) -> str:
        return (
            f"id={version_input['id']!r}"
            if "id" in version_input
            else f"academicYear={version_input['academicYear']!r}"
        )

    async def _list_boards(self) -> list[Mapping[str, Any]]:
        if self._boards is None:
            rows = await BoardRepository(self.session, self.ctx.org_id).list_boards()
            self._boards = [_row_dict(BoardOut, row) for row in rows]
        return self._boards

    async def _list_classes(self, board_id: str) -> list[Mapping[str, Any]]:
        if board_id not in self._classes:
            rows = await ClassRepository(self.session, self.ctx.org_id).list_for_board(
                uuid.UUID(board_id)
            )
            self._classes[board_id] = [_row_dict(ClassOut, row) for row in rows]
        return self._classes[board_id]

    async def _list_versions(self, board_id: str) -> list[Mapping[str, Any]]:
        if board_id not in self._versions:
            rows = await CurriculumVersionRepository(
                self.session, self.ctx.org_id
            ).list_for_board(uuid.UUID(board_id))
            self._versions[board_id] = [
                _row_dict(CurriculumVersionOut, row) for row in rows
            ]
        return self._versions[board_id]

    async def _list_subjects(
        self, class_id: str, version_id: str
    ) -> list[Mapping[str, Any]]:
        key = (class_id, version_id)
        if key not in self._subjects:
            rows = await SubjectRepository(self.session, self.ctx.org_id).list_for_class(
                uuid.UUID(class_id), curriculum_version_id=uuid.UUID(version_id)
            )
            self._subjects[key] = [_row_dict(SubjectOut, row) for row in rows]
        return self._subjects[key]

    async def _list_chapters(
        self, subject_id: str, version_id: str
    ) -> list[Mapping[str, Any]]:
        key = (subject_id, version_id)
        if key not in self._chapters:
            rows = await ChapterRepository(self.session, self.ctx.org_id).list_for_subject(
                uuid.UUID(subject_id), curriculum_version_id=uuid.UUID(version_id)
            )
            self._chapters[key] = [_row_dict(ChapterOut, row) for row in rows]
        return self._chapters[key]

    async def _list_outcomes(self, chapter_id: str) -> list[Mapping[str, Any]]:
        if chapter_id not in self._outcomes:
            rows = await OutcomeRepository(self.session, self.ctx.org_id).list_for_chapter(
                uuid.UUID(chapter_id)
            )
            self._outcomes[chapter_id] = [_row_dict(OutcomeOut, row) for row in rows]
        return self._outcomes[chapter_id]

    async def _prime_outcomes(
        self, chapters: Sequence[Mapping[str, Any]]
    ) -> None:
        missing_ids = [
            str(chapter["id"])
            for chapter in chapters
            if str(chapter["id"]) not in self._outcomes
        ]
        if not missing_ids:
            return

        rows = await OutcomeRepository(
            self.session, self.ctx.org_id
        ).list_for_chapters([uuid.UUID(chapter_id) for chapter_id in missing_ids])
        for chapter_id in missing_ids:
            self._outcomes[chapter_id] = []
        for row in rows:
            rendered = _row_dict(OutcomeOut, row)
            self._outcomes[str(rendered["chapterId"])].append(rendered)


def document_digest(document: CurriculumImportDocumentIn) -> str:
    payload = document.model_dump(by_alias=True, mode="json", exclude_unset=True)
    return _digest(payload)


def entity_totals(document: CurriculumImportDocumentIn) -> dict[str, int]:
    classes = document.board.classes
    subjects = [subject for school_class in classes for subject in school_class.subjects]
    chapters = [chapter for subject in subjects for chapter in subject.chapters]
    outcomes = [outcome for chapter in chapters for outcome in chapter.learning_outcomes]
    return {
        "boards": 1,
        "classes": len(classes),
        "curriculum_versions": 1,
        "subjects": len(subjects),
        "chapters": len(chapters),
        "learning_outcomes": len(outcomes),
    }


def validate_document(
    document: CurriculumImportDocumentIn,
) -> CurriculumImportValidationOut:
    version = document.board.curriculum_version
    version_key = (
        f"id={version.id}"
        if hasattr(version, "id")
        else f"academicYear={version.academic_year}"
    )
    return CurriculumImportValidationOut(
        document_digest=document_digest(document),
        board_code=document.board.code,
        version_key=version_key,
        entity_totals=entity_totals(document),
    )


async def preview_import(
    *,
    session: AsyncSession,
    ctx: OrgContext,
    document: CurriculumImportDocumentIn,
) -> CurriculumImportPlanOut:
    validation = validate_document(document)
    try:
        plan = await Planner(session, ctx, document).build()
    except ImportPreflightError as exc:
        issue = CurriculumImportIssueOut(path=exc.path, message=exc.message)
        empty_counts = {
            kind: CurriculumImportEntityCountOut(create=0, skip=0)
            for kind in ENTITY_ORDER
        }
        issue_payload = issue.model_dump(by_alias=True, mode="json")
        return CurriculumImportPlanOut(
            ready=False,
            document_digest=validation.document_digest,
            plan_digest=_digest(
                {"document": validation.document_digest, "issues": [issue_payload]}
            ),
            board_code=validation.board_code,
            version_key=validation.version_key,
            counts=empty_counts,
            actions=[],
            issues=[issue],
        )
    return _plan_out(validation, plan)


async def apply_import(
    *,
    session: AsyncSession,
    ctx: OrgContext,
    payload: CurriculumImportApplyIn,
    ip: Optional[str] = None,
) -> CurriculumImportResultOut:
    validation = validate_document(payload.document)
    try:
        prelock_plan = await Planner(session, ctx, payload.document).build()
    except ImportPreflightError as exc:
        raise ValidationError(
            "Curriculum import preflight failed.",
            details=[ErrorDetail(field=exc.path, issue=exc.message)],
        ) from exc

    prelock_preview = _plan_out(validation, prelock_plan)
    if prelock_preview.plan_digest != payload.expected_plan_digest:
        raise ConflictError(
            "The live catalog changed after this plan was reviewed. Preview it again.",
            code="CURRICULUM_IMPORT_PLAN_CHANGED",
            log_detail=(
                f"expected plan {payload.expected_plan_digest}; "
                f"rebuilt {prelock_preview.plan_digest}"
            ),
        )

    board_action = prelock_plan.groups["boards"][0]
    if board_action.existing is not None:
        await BoardRepository(session, ctx.org_id).lock_board(
            uuid.UUID(board_action.row_id)
        )

    try:
        locked_plan = await Planner(session, ctx, payload.document).build()
    except ImportPreflightError as exc:
        raise ConflictError(
            "The live catalog changed while this import was being committed. "
            "Preview it again.",
            code="CURRICULUM_IMPORT_PLAN_CHANGED",
            log_detail=f"locked preflight failed at {exc.path}: {exc.message}",
        ) from exc
    locked_preview = _plan_out(validation, locked_plan)
    if locked_preview.plan_digest != payload.expected_plan_digest:
        raise ConflictError(
            "The live catalog changed after this plan was reviewed. Preview it again.",
            code="CURRICULUM_IMPORT_PLAN_CHANGED",
            log_detail=(
                f"expected plan {payload.expected_plan_digest}; "
                f"rebuilt {locked_preview.plan_digest}"
            ),
        )

    await _execute(session=session, ctx=ctx, plan=locked_plan, ip=ip)
    board = locked_plan.groups["boards"][0]
    version = locked_plan.groups["curriculum_versions"][0]
    return CurriculumImportResultOut(
        **locked_preview.model_dump(),
        board_id=uuid.UUID(board.row_id),
        curriculum_version_id=uuid.UUID(version.row_id),
    )


async def _execute(
    *,
    session: AsyncSession,
    ctx: OrgContext,
    plan: ImportPlan,
    ip: Optional[str],
) -> None:
    schemas_and_models = {
        "boards": (BoardCreateIn, Board),
        "classes": (ClassCreateIn, SchoolClass),
        "curriculum_versions": (CurriculumVersionCreateIn, CurriculumVersion),
        "subjects": (SubjectCreateIn, Subject),
        "chapters": (ChapterCreateIn, Chapter),
        "learning_outcomes": (OutcomeCreateIn, LearningOutcome),
    }

    # IDs are allocated before the flush so every child payload can resolve its
    # parent without a database round-trip. SQLAlchemy then emits one batched
    # INSERT per table, in dependency order, instead of running the regular CRUD
    # service's existence/lock/refresh query sequence for every individual row.
    # The locked planner above has already performed those catalog checks against
    # the same transaction snapshot, and database constraints remain the final
    # concurrency guard.
    for action in plan.actions():
        if not action.is_create:
            action.result = action.existing
            continue

        action.result = {"id": str(uuid.uuid4()), "rowVersion": 1}

    pending_rows: list[Any] = []
    pending_audits: list[tuple[EntityAction, Any]] = []
    for action in plan.actions():
        if not action.is_create:
            continue
        schema_type, orm_type = schemas_and_models[action.kind]
        resolved = _resolve_payload(action.payload)
        values = schema_type.model_validate(resolved).model_dump()
        row = orm_type(
            id=uuid.UUID(action.row_id),
            **values,
        )
        pending_rows.append(row)
        pending_audits.append((action, row))

    session.add_all(pending_rows)
    for action, row in pending_audits:
        _stage_create_audit(
            session=session,
            ctx=ctx,
            action=action,
            row=row,
            ip=ip,
        )

    if pending_rows:
        try:
            await session.flush()
        except IntegrityError as exc:
            raise ConflictError(
                "The curriculum catalog changed while this import was being committed. "
                "Preview it again.",
                code="CURRICULUM_IMPORT_PLAN_CHANGED",
            ) from exc

    for transition in plan.transitions:
        row = transition.current_row()
        result = await service.update_curriculum_version(
            session=session,
            ctx=ctx,
            curriculum_version_id=uuid.UUID(str(row["id"])),
            payload=CurriculumVersionUpdateIn(
                row_version=int(row["rowVersion"]),
                status=transition.to_status,
            ),
            ip=ip,
        )
        transition.replace_row(result.model_dump(by_alias=True, mode="json"))


def _stage_create_audit(
    *,
    session: AsyncSession,
    ctx: OrgContext,
    action: EntityAction,
    row: Any,
    ip: Optional[str],
) -> None:
    if action.kind == "boards":
        audit_action = ACTIONS.BOARD_CREATED
        entity = "boards"
        meta = {"code": row.code, "name": row.name}
    elif action.kind == "classes":
        audit_action = ACTIONS.CLASS_CREATED
        entity = "classes"
        meta = {"boardId": str(row.board_id), "name": row.name}
    elif action.kind == "curriculum_versions":
        audit_action = ACTIONS.CURRICULUM_VERSION_CREATED
        entity = "curriculum_versions"
        meta = {"boardId": str(row.board_id), "academicYear": row.academic_year}
    elif action.kind == "subjects":
        audit_action = ACTIONS.SUBJECT_CREATED
        entity = "subjects"
        meta = {
            "classId": str(row.class_id),
            "curriculumVersionId": str(row.curriculum_version_id),
            "name": row.name,
        }
    elif action.kind == "chapters":
        audit_action = ACTIONS.CHAPTER_CREATED
        entity = "chapters"
        meta = {
            "subjectId": str(row.subject_id),
            "seq": row.seq,
            "derivedFromChapterId": (
                str(row.derived_from_chapter_id)
                if row.derived_from_chapter_id
                else None
            ),
        }
    else:
        audit_action = ACTIONS.OUTCOME_CREATED
        entity = "learning_outcomes"
        meta = {"chapterId": str(row.chapter_id), "code": row.code}
    record_audit(
        session,
        org_id=ctx.org_id,
        actor_id=ctx.user_id,
        action=audit_action,
        entity=entity,
        entity_id=row.id,
        meta=meta,
        ip=ip,
    )


def _plan_out(
    validation: CurriculumImportValidationOut,
    plan: ImportPlan,
) -> CurriculumImportPlanOut:
    actions: list[CurriculumImportActionOut] = []
    for action in plan.actions():
        actions.append(
            CurriculumImportActionOut(
                operation="create" if action.is_create else "skip",
                entity=action.kind,
                path=action.path,
                key=action.key,
                endpoint=action.endpoint,
                existing_id=(
                    uuid.UUID(str(action.existing["id"]))
                    if action.existing is not None
                    else None
                ),
                payload=_resolve_payload(action.payload, preview=True),
            )
        )
    for transition in plan.transitions:
        row = (
            transition.action.row
            if transition.action is not None
            else transition.row
        )
        row_id = str(row["id"]) if row is not None and row.get("id") else None
        actions.append(
            CurriculumImportActionOut(
                operation="transition",
                entity="curriculum_versions",
                path=transition.path,
                key=transition.key,
                endpoint=f"/api/v1/curriculum-versions/{row_id or '<new>'}",
                existing_id=(
                    uuid.UUID(row_id) if row_id else None
                ),
                from_status=transition.from_status,
                to_status=transition.to_status,
            )
        )
    counts = {
        kind: CurriculumImportEntityCountOut(
            create=sum(action.is_create for action in plan.groups[kind]),
            skip=sum(not action.is_create for action in plan.groups[kind]),
        )
        for kind in ENTITY_ORDER
    }
    digest_payload = [
        action.model_dump(by_alias=True, mode="json") for action in actions
    ]
    return CurriculumImportPlanOut(
        ready=True,
        document_digest=validation.document_digest,
        plan_digest=_digest(
            {"document": validation.document_digest, "actions": digest_payload}
        ),
        board_code=validation.board_code,
        version_key=validation.version_key,
        counts=counts,
        actions=actions,
        issues=[],
    )


def _resolve_payload(value: Any, *, preview: bool = False) -> Any:
    if isinstance(value, EntityAction):
        if value.row is not None:
            return value.row_id
        if preview:
            return f"<id from {value.path}>"
        return value.row_id
    if isinstance(value, Mapping):
        return {
            key: _resolve_payload(item, preview=preview) for key, item in value.items()
        }
    if isinstance(value, list):
        return [_resolve_payload(item, preview=preview) for item in value]
    return value


def _optional_string(value: Any) -> Optional[str]:
    return str(value) if value is not None else None


def _digest(value: Any) -> str:
    raw = json.dumps(
        value,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
    ).encode("utf-8")
    return "sha256:" + hashlib.sha256(raw).hexdigest()


__all__ = ["apply_import", "entity_totals", "preview_import", "validate_document"]
