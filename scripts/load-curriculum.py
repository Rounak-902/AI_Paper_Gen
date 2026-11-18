#!/usr/bin/env python3
"""Validate and load a nested curriculum document through PaperGen's real API.

This script intentionally contains no database client, SQL, or ORM import. Every read
and mutation goes through the authenticated HTTP routes that P2-T1 verified.
"""

from __future__ import annotations

import argparse
import json
import os
import sys
import urllib.error
import urllib.parse
import urllib.request
import uuid
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Iterable, Mapping, Optional, Sequence

try:
    from jsonschema import Draft202012Validator, FormatChecker
    from jsonschema.exceptions import SchemaError
except ImportError as exc:  # pragma: no cover - depends on the invoking environment
    raise SystemExit(
        "Missing dependency 'jsonschema'. Install the backend project dependencies "
        "before running this loader."
    ) from exc


SCRIPT_DIR = Path(__file__).resolve().parent
DEFAULT_SCHEMA = SCRIPT_DIR / "curriculum-import-schema.json"
DEFAULT_API_BASE_URL = "http://127.0.0.1:8000"
PAGE_SIZE = 100

ENTITY_ORDER = (
    "boards",
    "classes",
    "curriculum_versions",
    "subjects",
    "chapters",
    "learning_outcomes",
)
DISPLAY_NAMES = {
    "boards": "board",
    "classes": "class",
    "curriculum_versions": "curriculum version",
    "subjects": "subject",
    "chapters": "chapter",
    "learning_outcomes": "learning outcome",
}


class InputValidationError(Exception):
    """The local document failed schema or semantic validation."""

    def __init__(self, errors: Sequence[str]) -> None:
        self.errors = list(errors)
        super().__init__("; ".join(self.errors))


class ConfigurationError(Exception):
    """Required environment configuration is missing or malformed."""


class PreflightError(Exception):
    """Read-only API preflight found a conflict or drift before any mutation."""


class ApiError(Exception):
    """An HTTP or PaperGen envelope error."""

    def __init__(
        self,
        message: str,
        *,
        method: str,
        path: str,
        status: Optional[int] = None,
        code: Optional[str] = None,
        details: Any = None,
    ) -> None:
        self.message = message
        self.method = method
        self.path = path
        self.status = status
        self.code = code
        self.details = details
        parts = [f"{method} {path}"]
        if status is not None:
            parts.append(f"HTTP {status}")
        if code:
            parts.append(code)
        parts.append(message)
        super().__init__(" | ".join(parts))


class ActionExecutionError(Exception):
    """A planned row mutation failed."""

    def __init__(self, action: "EntityAction", payload: Mapping[str, Any], cause: Exception):
        self.action = action
        self.payload = dict(payload)
        self.cause = cause
        super().__init__(str(cause))


def _json_path(parts: Iterable[Any]) -> str:
    result = "$"
    for part in parts:
        if isinstance(part, int):
            result += f"[{part}]"
        elif isinstance(part, str) and part.isidentifier():
            result += f".{part}"
        else:
            result += "[" + json.dumps(str(part)) + "]"
    return result


def _read_json(path: Path, *, label: str) -> Any:
    try:
        with path.open("r", encoding="utf-8") as handle:
            return json.load(handle)
    except FileNotFoundError as exc:
        raise InputValidationError([f"{label}: file not found: {path}"]) from exc
    except json.JSONDecodeError as exc:
        raise InputValidationError(
            [f"{label}: invalid JSON at line {exc.lineno}, column {exc.colno}: {exc.msg}"]
        ) from exc
    except OSError as exc:
        raise InputValidationError([f"{label}: cannot read {path}: {exc}"]) from exc


def _schema_errors(document: Any, schema: Any) -> list[str]:
    try:
        Draft202012Validator.check_schema(schema)
    except SchemaError as exc:
        return [f"schema: the loader schema itself is invalid: {exc.message}"]

    validator = Draft202012Validator(schema, format_checker=FormatChecker())
    errors = sorted(
        validator.iter_errors(document),
        key=lambda error: (
            tuple(f"{type(part).__name__}:{part}" for part in error.absolute_path),
            error.message,
        ),
    )
    return [f"{_json_path(error.absolute_path)}: {error.message}" for error in errors]


def _semantic_errors(document: Mapping[str, Any]) -> list[str]:
    """Validate natural-key uniqueness and lineage facts JSON Schema cannot express."""

    errors: list[str] = []
    board = document["board"]
    version = board["curriculumVersion"]
    target_year = version.get("academicYear")

    final_status = version.get("finalStatus", "draft")
    if version.get("supersedeActive", False) and final_status == "draft":
        errors.append(
            "$.board.curriculumVersion.supersedeActive: cannot be true when "
            "finalStatus is draft"
        )

    class_names: dict[str, int] = {}
    for class_index, school_class in enumerate(board["classes"]):
        class_path = f"$.board.classes[{class_index}]"
        class_name = school_class["name"]
        if class_name in class_names:
            errors.append(
                f"{class_path}.name: duplicate board-scoped class name; first used at "
                f"$.board.classes[{class_names[class_name]}].name"
            )
        else:
            class_names[class_name] = class_index

        subject_names: dict[str, int] = {}
        for subject_index, subject in enumerate(school_class["subjects"]):
            subject_path = f"{class_path}.subjects[{subject_index}]"
            subject_name = subject["name"]
            if subject_name in subject_names:
                errors.append(
                    f"{subject_path}.name: duplicate subject name in the same class and "
                    f"version; first used at {class_path}.subjects[{subject_names[subject_name]}].name"
                )
            else:
                subject_names[subject_name] = subject_index

            chapter_sequences: dict[int, int] = {}
            for chapter_index, chapter in enumerate(subject["chapters"]):
                chapter_path = f"{subject_path}.chapters[{chapter_index}]"
                sequence = chapter["seq"]
                if sequence in chapter_sequences:
                    errors.append(
                        f"{chapter_path}.seq: duplicate subject-scoped chapter sequence; "
                        f"first used at {subject_path}.chapters[{chapter_sequences[sequence]}].seq"
                    )
                else:
                    chapter_sequences[sequence] = chapter_index

                derived_from = chapter.get("derivedFrom")
                if derived_from is not None:
                    source_path = f"{chapter_path}.derivedFrom"
                    exact_self = (
                        target_year is not None
                        and derived_from["academicYear"] == target_year
                        and derived_from["className"] == class_name
                        and derived_from["subjectName"] == subject_name
                        and derived_from["chapterSeq"] == sequence
                    )
                    if exact_self:
                        errors.append(
                            f"{source_path}: direct self-lineage is invalid; the selector "
                            "resolves to this target chapter"
                        )
                    elif target_year is not None and derived_from["academicYear"] == target_year:
                        errors.append(
                            f"{source_path}.academicYear: lineage must name a different, "
                            "active prior curriculum version"
                        )
                    if derived_from["className"] != class_name:
                        errors.append(
                            f"{source_path}.className: must equal enclosing class name "
                            f"{class_name!r} because the API forbids cross-class lineage"
                        )
                    if derived_from["subjectName"] != subject_name:
                        errors.append(
                            f"{source_path}.subjectName: must equal enclosing subject name "
                            f"{subject_name!r} because the API forbids cross-subject lineage"
                        )

                outcome_codes: dict[str, int] = {}
                for outcome_index, outcome in enumerate(chapter["learningOutcomes"]):
                    outcome_path = f"{chapter_path}.learningOutcomes[{outcome_index}]"
                    code = outcome["code"]
                    if code in outcome_codes:
                        errors.append(
                            f"{outcome_path}.code: duplicate chapter-scoped outcome code; "
                            f"first used at {chapter_path}.learningOutcomes[{outcome_codes[code]}].code"
                        )
                    else:
                        outcome_codes[code] = outcome_index
    return errors


def validate_input(document_path: Path, schema_path: Path) -> Mapping[str, Any]:
    schema = _read_json(schema_path, label="schema")
    document = _read_json(document_path, label="input")
    errors = _schema_errors(document, schema)
    if not errors and isinstance(document, Mapping):
        errors.extend(_semantic_errors(document))
    if errors:
        raise InputValidationError(errors)
    return document


def _normal_uuid(value: str, *, name: str) -> str:
    try:
        return str(uuid.UUID(value))
    except (ValueError, AttributeError) as exc:
        raise ConfigurationError(f"{name} must be a valid UUID") from exc


@dataclass(frozen=True)
class Credentials:
    email: str
    password: str
    org_id: str

    @classmethod
    def from_environment(cls) -> "Credentials":
        names = (
            "PAPERGEN_SUPER_ADMIN_EMAIL",
            "PAPERGEN_SUPER_ADMIN_PASSWORD",
            "PAPERGEN_SUPER_ADMIN_ORG_ID",
        )
        values = {name: os.environ.get(name, "") for name in names}
        missing = [name for name, value in values.items() if not value]
        if missing:
            raise ConfigurationError(
                "missing required credential environment variable(s): " + ", ".join(missing)
            )
        return cls(
            email=values["PAPERGEN_SUPER_ADMIN_EMAIL"],
            password=values["PAPERGEN_SUPER_ADMIN_PASSWORD"],
            org_id=_normal_uuid(
                values["PAPERGEN_SUPER_ADMIN_ORG_ID"],
                name="PAPERGEN_SUPER_ADMIN_ORG_ID",
            ),
        )


class ApiClient:
    """Small synchronous JSON client for PaperGen's success/error envelopes."""

    def __init__(self, base_url: str, *, timeout: float) -> None:
        parsed = urllib.parse.urlsplit(base_url)
        if parsed.scheme not in {"http", "https"} or not parsed.netloc:
            raise ConfigurationError(
                "PAPERGEN_API_BASE_URL/--api-base-url must be an absolute http(s) URL"
            )
        self.base_url = base_url.rstrip("/")
        self.timeout = timeout
        self.token: Optional[str] = None
        self.active_org: Optional[Mapping[str, Any]] = None

    def login(self, credentials: Credentials) -> Mapping[str, Any]:
        data, _ = self.request(
            "POST",
            "/api/v1/auth/login",
            body={
                "email": credentials.email,
                "password": credentials.password,
                "orgId": credentials.org_id,
            },
            authenticated=False,
        )
        if not isinstance(data, Mapping):
            raise ApiError(
                "login data is not an object",
                method="POST",
                path="/api/v1/auth/login",
            )
        token = data.get("accessToken")
        active_org = data.get("activeOrg")
        if not isinstance(token, str) or not token:
            raise ApiError(
                "login response did not contain data.accessToken",
                method="POST",
                path="/api/v1/auth/login",
            )
        if not isinstance(active_org, Mapping):
            raise ApiError(
                "login response did not contain an active organization",
                method="POST",
                path="/api/v1/auth/login",
            )
        actual_org = _normal_uuid(str(active_org.get("orgId", "")), name="activeOrg.orgId")
        if actual_org != credentials.org_id:
            raise ApiError(
                f"login selected organization {actual_org}, expected {credentials.org_id}",
                method="POST",
                path="/api/v1/auth/login",
            )
        if active_org.get("role") != "super_admin":
            raise ApiError(
                f"authenticated membership role is {active_org.get('role')!r}; super_admin is required",
                method="POST",
                path="/api/v1/auth/login",
                status=403,
                code="FORBIDDEN",
            )
        self.token = token
        self.active_org = active_org
        return data

    def request(
        self,
        method: str,
        path: str,
        *,
        body: Optional[Mapping[str, Any]] = None,
        params: Optional[Mapping[str, Any]] = None,
        authenticated: bool = True,
    ) -> tuple[Any, Mapping[str, Any]]:
        query = ""
        if params:
            clean_params = {key: value for key, value in params.items() if value is not None}
            query = "?" + urllib.parse.urlencode(clean_params)
        url = self.base_url + path + query
        headers = {"Accept": "application/json"}
        if authenticated:
            if not self.token:
                raise ApiError("not authenticated", method=method, path=path)
            headers["Authorization"] = "Bearer " + self.token
        encoded: Optional[bytes] = None
        if body is not None:
            encoded = json.dumps(body, ensure_ascii=False).encode("utf-8")
            headers["Content-Type"] = "application/json"
        request = urllib.request.Request(url, data=encoded, headers=headers, method=method)
        status: Optional[int] = None
        raw = b""
        try:
            with urllib.request.urlopen(request, timeout=self.timeout) as response:
                status = response.status
                raw = response.read()
        except urllib.error.HTTPError as exc:
            status = exc.code
            raw = exc.read()
            envelope = _decode_json(raw)
            error = envelope.get("error", {}) if isinstance(envelope, Mapping) else {}
            raise ApiError(
                str(error.get("message") or exc.reason or "request failed"),
                method=method,
                path=path,
                status=status,
                code=str(error.get("code")) if error.get("code") else None,
                details=error.get("details"),
            ) from exc
        except urllib.error.URLError as exc:
            raise ApiError(
                f"connection failed: {exc.reason}", method=method, path=path
            ) from exc
        except TimeoutError as exc:
            raise ApiError("request timed out", method=method, path=path) from exc

        envelope = _decode_json(raw)
        if not isinstance(envelope, Mapping):
            raise ApiError(
                "response was not a JSON object", method=method, path=path, status=status
            )
        if envelope.get("success") is not True:
            error = envelope.get("error", {})
            if not isinstance(error, Mapping):
                error = {}
            raise ApiError(
                str(error.get("message") or "response envelope reported failure"),
                method=method,
                path=path,
                status=status,
                code=str(error.get("code")) if error.get("code") else None,
                details=error.get("details"),
            )
        meta = envelope.get("meta")
        return envelope.get("data"), meta if isinstance(meta, Mapping) else {}

    def get(self, path: str) -> Mapping[str, Any]:
        data, _ = self.request("GET", path)
        if not isinstance(data, Mapping):
            raise ApiError("expected an object", method="GET", path=path)
        return data

    def list_all(self, path: str, *, params: Optional[Mapping[str, Any]] = None) -> list[Mapping[str, Any]]:
        result: list[Mapping[str, Any]] = []
        page = 1
        while True:
            page_params = dict(params or {})
            page_params.update({"page": page, "pageSize": PAGE_SIZE})
            data, meta = self.request("GET", path, params=page_params)
            if not isinstance(data, list) or not all(isinstance(item, Mapping) for item in data):
                raise ApiError("expected a list", method="GET", path=path)
            result.extend(data)
            pagination = meta.get("pagination")
            has_next = bool(pagination.get("hasNext")) if isinstance(pagination, Mapping) else False
            if not has_next:
                return result
            page += 1


def _decode_json(raw: bytes) -> Any:
    if not raw:
        return None
    try:
        return json.loads(raw.decode("utf-8"))
    except (UnicodeDecodeError, json.JSONDecodeError):
        return None


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


class Planner:
    def __init__(self, client: ApiClient, document: Mapping[str, Any]) -> None:
        self.client = client
        self.document = document
        self.plan_result = ImportPlan()
        self._boards: Optional[list[Mapping[str, Any]]] = None
        self._classes: dict[str, list[Mapping[str, Any]]] = {}
        self._versions: dict[str, list[Mapping[str, Any]]] = {}
        self._subjects: dict[tuple[str, str], list[Mapping[str, Any]]] = {}
        self._chapters: dict[tuple[str, str], list[Mapping[str, Any]]] = {}
        self._outcomes: dict[str, list[Mapping[str, Any]]] = {}

    def build(self) -> ImportPlan:
        board_input = self.document["board"]
        board_path = "$.board"
        board_existing = self._one(
            self._list_boards(),
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
            self._list_classes(board_action.row_id) if board_existing is not None else []
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
        version_action, known_versions = self._version_action(
            board_action, board_input, version_input, version_path
        )

        for class_input, class_path, class_action in class_entries:
            if class_action.existing is not None and version_action.existing is not None:
                available_subjects = self._list_subjects(
                    class_action.row_id, version_action.row_id
                )
            else:
                available_subjects = []
            for subject_index, subject_input in enumerate(class_input["subjects"]):
                subject_path = f"{class_path}.subjects[{subject_index}]"
                subject_existing = self._one(
                    available_subjects,
                    lambda row, name=subject_input["name"]: row.get("name") == name,
                    path=f"{subject_path}.name",
                    key=(
                        f"class={class_input['name']!r}, version={self._version_key(version_input)}, "
                        f"name={subject_input['name']!r}"
                    ),
                )
                subject_action = self._add(
                    "subjects",
                    path=subject_path,
                    key=(
                        f"class={class_input['name']!r}, version={self._version_key(version_input)}, "
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
                if subject_action.existing is not None and version_action.existing is not None:
                    available_chapters = self._list_chapters(
                        subject_action.row_id, version_action.row_id
                    )
                else:
                    available_chapters = []
                for chapter_index, chapter_input in enumerate(subject_input["chapters"]):
                    chapter_path = f"{subject_path}.chapters[{chapter_index}]"
                    source = None
                    if chapter_input.get("derivedFrom") is not None:
                        source = self._resolve_lineage_source(
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
                        self._list_outcomes(chapter_action.row_id)
                        if chapter_action.existing is not None
                        else []
                    )
                    for outcome_index, outcome_input in enumerate(
                        chapter_input["learningOutcomes"]
                    ):
                        outcome_path = (
                            f"{chapter_path}.learningOutcomes[{outcome_index}]"
                        )
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

    def _version_action(
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
                raise PreflightError(
                    f"{version_path}.id: cannot target an existing curriculum version "
                    "while its enclosing board would be created"
                )
            try:
                existing = self.client.get(
                    f"/api/v1/curriculum-versions/{version_input['id']}"
                )
            except ApiError as exc:
                raise PreflightError(
                    f"{version_path}.id: target curriculum version could not be read: {exc}"
                ) from exc
            if str(existing.get("boardId")) != board_action.row_id:
                raise PreflightError(
                    f"{version_path}.id: version belongs to boardId={existing.get('boardId')}, "
                    f"not enclosing boardId={board_action.row_id}"
                )
            known_versions = self._list_versions(board_action.row_id)
            key = f"id={version_input['id']}"
        else:
            if board_action.existing is not None:
                known_versions = self._list_versions(board_action.row_id)
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
        if current != "draft" and version_owned_creates:
            raise PreflightError(
                f"{version_path}: target version is {current!r} and immutable, but "
                f"{version_owned_creates} nested row(s) are missing; target a draft version"
            )

        rank = {"draft": 0, "active": 1, "superseded": 2}
        if rank[current] > rank[desired]:
            raise PreflightError(
                f"{version_path}.finalStatus: cannot regress existing status "
                f"{current!r} to {desired!r}"
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
                raise PreflightError(
                    f"{version_path}.finalStatus: activating this version would conflict "
                    f"with active academicYear={other_active.get('academicYear')!r}; set "
                    "supersedeActive=true to opt into the required active -> superseded transition"
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
        for status in target_steps:
            self.plan_result.transitions.append(
                StatusTransition(
                    path=f"{version_path}.finalStatus",
                    key=version_action.key,
                    from_status=step_from,
                    to_status=status,
                    action=version_action,
                )
            )
            step_from = status

    def _resolve_lineage_source(
        self,
        selector: Mapping[str, Any],
        *,
        path: str,
        board_action: EntityAction,
        target_version_action: EntityAction,
    ) -> Mapping[str, Any]:
        if board_action.existing is None:
            raise PreflightError(
                f"{path}: cannot resolve prior-version lineage because the enclosing "
                "board does not yet exist"
            )
        source_class = self._one(
            self._list_classes(board_action.row_id),
            lambda row: row.get("name") == selector["className"],
            path=f"{path}.className",
            key=f"class name={selector['className']!r}",
        )
        if source_class is None:
            raise PreflightError(
                f"{path}.className: no live API row matches {selector['className']!r}"
            )
        source_version = self._one(
            self._list_versions(board_action.row_id),
            lambda row: row.get("academicYear") == selector["academicYear"],
            path=f"{path}.academicYear",
            key=f"academicYear={selector['academicYear']!r}",
        )
        if source_version is None:
            raise PreflightError(
                f"{path}.academicYear: no live API row matches {selector['academicYear']!r}"
            )
        if (
            target_version_action.existing is not None
            and str(source_version.get("id")) == target_version_action.row_id
        ):
            raise PreflightError(
                f"{path}: lineage resolves inside the target version; a different active "
                "prior version is required"
            )
        if source_version.get("status") != "active":
            raise PreflightError(
                f"{path}.academicYear: source version status is "
                f"{source_version.get('status')!r}; the API requires 'active'"
            )
        source_subject = self._one(
            self._list_subjects(str(source_class["id"]), str(source_version["id"])),
            lambda row: row.get("name") == selector["subjectName"],
            path=f"{path}.subjectName",
            key=f"subject name={selector['subjectName']!r}",
        )
        if source_subject is None:
            raise PreflightError(
                f"{path}.subjectName: no subject matches {selector['subjectName']!r} "
                "in the selected class and version"
            )
        source_chapter = self._one(
            self._list_chapters(str(source_subject["id"]), str(source_version["id"])),
            lambda row: row.get("seq") == selector["chapterSeq"],
            path=f"{path}.chapterSeq",
            key=f"chapter seq={selector['chapterSeq']}",
        )
        if source_chapter is None:
            raise PreflightError(
                f"{path}.chapterSeq: no chapter matches seq={selector['chapterSeq']} "
                "in the selected subject and version"
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
        action = EntityAction(
            kind=kind,
            path=path,
            key=key,
            endpoint=endpoint,
            payload=payload,
            existing=existing,
        )
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
            ids = [str(row.get("id")) for row in matches]
            raise PreflightError(
                f"{path}: natural key {key} matched multiple live API rows: {ids}"
            )
        return matches[0] if matches else None

    @staticmethod
    def _same(actual: Any, desired: Any, *, path: str, key: str) -> None:
        if actual != desired:
            raise PreflightError(
                f"{path}: existing {key} has {actual!r}, input requests {desired!r}; "
                "refusing to silently treat drift as idempotent"
            )

    @staticmethod
    def _version_key(version_input: Mapping[str, Any]) -> str:
        return (
            f"id={version_input['id']!r}"
            if "id" in version_input
            else f"academicYear={version_input['academicYear']!r}"
        )

    def _list_boards(self) -> list[Mapping[str, Any]]:
        if self._boards is None:
            self._boards = self.client.list_all("/api/v1/boards")
        return self._boards

    def _list_classes(self, board_id: str) -> list[Mapping[str, Any]]:
        if board_id not in self._classes:
            self._classes[board_id] = self.client.list_all(
                "/api/v1/classes", params={"boardId": board_id}
            )
        return self._classes[board_id]

    def _list_versions(self, board_id: str) -> list[Mapping[str, Any]]:
        if board_id not in self._versions:
            self._versions[board_id] = self.client.list_all(
                "/api/v1/curriculum-versions", params={"boardId": board_id}
            )
        return self._versions[board_id]

    def _list_subjects(self, class_id: str, version_id: str) -> list[Mapping[str, Any]]:
        key = (class_id, version_id)
        if key not in self._subjects:
            self._subjects[key] = self.client.list_all(
                "/api/v1/subjects",
                params={"classId": class_id, "curriculumVersionId": version_id},
            )
        return self._subjects[key]

    def _list_chapters(self, subject_id: str, version_id: str) -> list[Mapping[str, Any]]:
        key = (subject_id, version_id)
        if key not in self._chapters:
            self._chapters[key] = self.client.list_all(
                "/api/v1/chapters",
                params={"subjectId": subject_id, "curriculumVersionId": version_id},
            )
        return self._chapters[key]

    def _list_outcomes(self, chapter_id: str) -> list[Mapping[str, Any]]:
        if chapter_id not in self._outcomes:
            self._outcomes[chapter_id] = self.client.list_all(
                "/api/v1/outcomes", params={"chapterId": chapter_id}
            )
        return self._outcomes[chapter_id]


def _optional_string(value: Any) -> Optional[str]:
    return str(value) if value is not None else None


def _resolve_payload(value: Any, *, preview: bool = False) -> Any:
    if isinstance(value, EntityAction):
        if value.row is not None:
            return value.row_id
        if preview:
            return f"<id from {value.path}>"
        return value.row_id
    if isinstance(value, Mapping):
        return {key: _resolve_payload(item, preview=preview) for key, item in value.items()}
    if isinstance(value, list):
        return [_resolve_payload(item, preview=preview) for item in value]
    return value


def _print_plan(plan: ImportPlan) -> None:
    print("Plan:")
    for action in plan.actions():
        if action.is_create:
            payload = _resolve_payload(action.payload, preview=True)
            print(
                f"  CREATE {action.kind} {action.path} key=({action.key}) "
                f"payload={json.dumps(payload, ensure_ascii=False, sort_keys=True)}"
            )
        else:
            print(
                f"  SKIP   {action.kind} {action.path} key=({action.key}) "
                f"id={action.row_id}"
            )
    for transition in plan.transitions:
        row = transition.action.row if transition.action is not None else transition.row
        row_id = row.get("id") if row is not None else f"<id from {transition.action.path}>"
        print(
            f"  PATCH  curriculum_versions {transition.path} key=({transition.key}) "
            f"id={row_id} {transition.from_status} -> {transition.to_status}"
        )


def _summary_counts(plan: ImportPlan) -> dict[str, dict[str, int]]:
    return {
        kind: {
            "create": sum(action.is_create for action in plan.groups[kind]),
            "skip": sum(not action.is_create for action in plan.groups[kind]),
        }
        for kind in ENTITY_ORDER
    }


def _print_dry_run_summary(plan: ImportPlan) -> None:
    counts = _summary_counts(plan)
    print("Summary (dry-run; zero writes sent):")
    for kind in ENTITY_ORDER:
        print(
            f"  {kind}: would_create={counts[kind]['create']} "
            f"already_existing={counts[kind]['skip']}"
        )
    print(f"  curriculum_version_status_transitions: would_update={len(plan.transitions)}")
    print("  failures: 0")


def _execute(
    client: ApiClient,
    plan: ImportPlan,
    completed: dict[str, dict[str, int]],
    transition_progress: dict[str, int],
) -> dict[str, dict[str, int]]:
    for action in plan.actions():
        if not action.is_create:
            action.result = action.existing
            completed[action.kind]["skipped"] += 1
            print(f"SKIP   {action.kind} {action.path} id={action.row_id}")
            continue
        payload = _resolve_payload(action.payload)
        try:
            data, _ = client.request("POST", action.endpoint, body=payload)
            if not isinstance(data, Mapping) or not data.get("id"):
                raise ApiError(
                    "create response did not contain an object with id",
                    method="POST",
                    path=action.endpoint,
                )
            action.result = data
            completed[action.kind]["created"] += 1
            print(f"CREATE {action.kind} {action.path} id={action.row_id}")
        except Exception as exc:
            completed[action.kind]["failed"] += 1
            raise ActionExecutionError(action, payload, exc) from exc

    for transition in plan.transitions:
        row = transition.current_row()
        payload = {"rowVersion": row["rowVersion"], "status": transition.to_status}
        endpoint = f"/api/v1/curriculum-versions/{row['id']}"
        try:
            data, _ = client.request("PATCH", endpoint, body=payload)
            if not isinstance(data, Mapping):
                raise ApiError(
                    "status response did not contain an object",
                    method="PATCH",
                    path=endpoint,
                )
            transition.replace_row(data)
            transition_progress["updated"] += 1
            print(
                f"PATCH  curriculum_versions {transition.path} id={row['id']} "
                f"{row['status']} -> {data.get('status')}"
            )
        except Exception as exc:
            status_action = EntityAction(
                kind="curriculum_versions",
                path=transition.path,
                key=transition.key,
                endpoint=endpoint,
                payload=payload,
                existing=row,
            )
            completed["curriculum_versions"]["failed"] += 1
            raise ActionExecutionError(status_action, payload, exc) from exc
    return completed


def _print_apply_summary(
    counts: Mapping[str, Mapping[str, int]], *, transitions: int, failures: int
) -> None:
    print("Summary (apply):")
    for kind in ENTITY_ORDER:
        row = counts[kind]
        print(
            f"  {kind}: created={row['created']} skipped={row['skipped']} "
            f"failed={row['failed']}"
        )
    print(f"  curriculum_version_status_transitions: updated={transitions}")
    print(f"  failures: {failures}")


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description=(
            "Validate and idempotently import nested curriculum data through the "
            "Argon2id-authenticated PaperGen API."
        )
    )
    parser.add_argument("input", type=Path, help="curriculum JSON document")
    parser.add_argument(
        "--schema",
        type=Path,
        default=DEFAULT_SCHEMA,
        help=f"JSON Schema path (default: {DEFAULT_SCHEMA})",
    )
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="validate, authenticate, query live state, and print the exact plan without POST/PATCH",
    )
    parser.add_argument(
        "--validate-only",
        action="store_true",
        help="run local schema and semantic validation only; do not authenticate or query the API",
    )
    parser.add_argument(
        "--api-base-url",
        default=os.environ.get("PAPERGEN_API_BASE_URL", DEFAULT_API_BASE_URL),
        help=(
            "API origin (default: PAPERGEN_API_BASE_URL or "
            f"{DEFAULT_API_BASE_URL})"
        ),
    )
    parser.add_argument(
        "--timeout",
        type=float,
        default=30.0,
        help="per-request timeout in seconds (default: 30)",
    )
    return parser


def main(argv: Optional[Sequence[str]] = None) -> int:
    args = _parser().parse_args(argv)
    if args.timeout <= 0:
        print("CONFIGURATION ERROR: --timeout must be greater than zero", file=sys.stderr)
        return 2

    try:
        document = validate_input(args.input.resolve(), args.schema.resolve())
    except InputValidationError as exc:
        print(f"INPUT VALIDATION FAILED ({len(exc.errors)} error(s)); no API calls were made:", file=sys.stderr)
        for error in exc.errors:
            print(f"  - {error}", file=sys.stderr)
        return 2

    print(f"Input validation: PASS (schema + semantic preflight) file={args.input.resolve()}")
    if args.validate_only:
        print("Validation-only mode: PASS; no API calls were made")
        return 0

    try:
        credentials = Credentials.from_environment()
        client = ApiClient(args.api_base_url, timeout=args.timeout)
        client.login(credentials)
        print(
            "Authentication: PASS "
            f"orgId={credentials.org_id} role=super_admin api={client.base_url}"
        )
        plan = Planner(client, document).build()
        print("Read-only API preflight: PASS; no mutations have been sent")
    except (ConfigurationError, ApiError, PreflightError) as exc:
        print(f"PREFLIGHT FAILED; no curriculum mutations were sent: {exc}", file=sys.stderr)
        return 1

    _print_plan(plan)
    if args.dry_run:
        _print_dry_run_summary(plan)
        return 0

    counts = {
        kind: {"created": 0, "skipped": 0, "failed": 0} for kind in ENTITY_ORDER
    }
    transition_progress = {"updated": 0}
    try:
        counts = _execute(client, plan, counts, transition_progress)
    except ActionExecutionError as exc:
        action = exc.action
        print("IMPORT ROW FAILED:", file=sys.stderr)
        print(f"  entity: {action.kind}", file=sys.stderr)
        print(f"  path: {action.path}", file=sys.stderr)
        print(f"  key: {action.key}", file=sys.stderr)
        print(
            "  payload: " + json.dumps(exc.payload, ensure_ascii=False, sort_keys=True),
            file=sys.stderr,
        )
        print(f"  reason: {exc.cause}", file=sys.stderr)
        counts[action.kind]["failed"] += int(counts[action.kind]["failed"] == 0)
        _print_apply_summary(
            counts, transitions=transition_progress["updated"], failures=1
        )
        print(
            "The API has no bulk transaction; earlier successful rows remain. Fix the "
            "reported row and rerun—the natural-key checks will skip them.",
            file=sys.stderr,
        )
        return 1


    _print_apply_summary(
        counts, transitions=transition_progress["updated"], failures=0
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
