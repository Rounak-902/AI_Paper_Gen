"""Pure value objects and validation for versioned prompt files."""

from __future__ import annotations

import re
from dataclasses import dataclass
from typing import Any, Mapping

from packages.prompts.errors import (
    InvalidPromptArtifactError,
    InvalidPromptReferenceError,
)

PROMPT_FILE_SCHEMA_VERSION = 1

# Task identifiers are an open vocabulary in the architecture.  Its examples all
# use lower snake case (``generate_mcq``, ``classify_difficulty``), so references
# enforce that portable spelling without trying to invent an exhaustive enum.
_TASK_PATTERN = r"[a-z][a-z0-9]*(?:_[a-z0-9]+)*"
_REFERENCE_RE = re.compile(
    rf"(?P<task>{_TASK_PATTERN})/v(?P<version>[1-9][0-9]*)\Z"
)


@dataclass(frozen=True, slots=True)
class PromptReference:
    """A stable logical reference such as ``generate_mcq/v2``."""

    task: str
    version: int

    @classmethod
    def parse(cls, value: str) -> "PromptReference":
        if not isinstance(value, str):
            raise InvalidPromptReferenceError("template_ref must be a string")
        match = _REFERENCE_RE.fullmatch(value)
        if match is None:
            raise InvalidPromptReferenceError(
                "template_ref must match '<lower_snake_case_task>/v<positive_integer>'"
            )
        return cls(task=match.group("task"), version=int(match.group("version")))

    @classmethod
    def from_parts(cls, task: str, version: int) -> "PromptReference":
        if type(version) is not int or version < 1:
            raise InvalidPromptReferenceError("prompt version must be a positive integer")
        return cls.parse(f"{task}/v{version}")

    def __str__(self) -> str:
        return f"{self.task}/v{self.version}"


@dataclass(frozen=True, slots=True)
class PromptLayers:
    """The architecture's system, developer, and user prompt layers.

    These are unrendered strings.  In particular, this type does not interpret
    placeholder syntax or merge the developer layer into a provider-specific role.
    """

    system: str
    developer: str
    user: str


@dataclass(frozen=True, slots=True)
class PromptArtifact:
    """One immutable prompt file plus its content identity."""

    schema_version: int
    template_ref: str
    task: str
    version: int
    layers: PromptLayers
    raw_text: str
    content_sha256: str

    @classmethod
    def from_mapping(
        cls,
        value: Any,
        *,
        reference: PromptReference,
        raw_text: str,
        content_sha256: str,
    ) -> "PromptArtifact":
        artifact = _require_mapping(value, location="prompt artifact")
        _require_exact_keys(
            artifact,
            expected={"schema_version", "task", "version", "layers"},
            location="prompt artifact",
        )

        schema_version = artifact["schema_version"]
        if type(schema_version) is not int or schema_version != PROMPT_FILE_SCHEMA_VERSION:
            raise InvalidPromptArtifactError(
                f"schema_version must be {PROMPT_FILE_SCHEMA_VERSION}"
            )

        task = artifact["task"]
        if not isinstance(task, str):
            raise InvalidPromptArtifactError("prompt artifact task must be a string")
        try:
            PromptReference.from_parts(task, 1)
        except InvalidPromptReferenceError as exc:
            raise InvalidPromptArtifactError(
                "prompt artifact task must use lower snake case"
            ) from exc

        version = artifact["version"]
        if type(version) is not int or version < 1:
            raise InvalidPromptArtifactError(
                "prompt artifact version must be a positive integer"
            )

        if task != reference.task or version != reference.version:
            raise InvalidPromptArtifactError(
                "prompt artifact task/version does not match its template_ref: "
                f"expected {reference}, found {task}/v{version}"
            )

        layer_mapping = _require_mapping(artifact["layers"], location="layers")
        _require_exact_keys(
            layer_mapping,
            expected={"system", "developer", "user"},
            location="layers",
        )
        layers = PromptLayers(
            system=_require_prompt_text(layer_mapping["system"], layer="system"),
            developer=_require_prompt_text(
                layer_mapping["developer"], layer="developer"
            ),
            user=_require_prompt_text(layer_mapping["user"], layer="user"),
        )

        return cls(
            schema_version=schema_version,
            template_ref=str(reference),
            task=task,
            version=version,
            layers=layers,
            raw_text=raw_text,
            content_sha256=content_sha256,
        )


def _require_mapping(value: Any, *, location: str) -> Mapping[str, Any]:
    if not isinstance(value, Mapping):
        raise InvalidPromptArtifactError(f"{location} must be a JSON object")
    if not all(isinstance(key, str) for key in value):
        raise InvalidPromptArtifactError(f"{location} keys must be strings")
    return value


def _require_exact_keys(
    value: Mapping[str, Any], *, expected: set[str], location: str
) -> None:
    actual = set(value)
    missing = sorted(expected - actual)
    unexpected = sorted(actual - expected)
    if missing or unexpected:
        details: list[str] = []
        if missing:
            details.append(f"missing {missing}")
        if unexpected:
            details.append(f"unexpected {unexpected}")
        raise InvalidPromptArtifactError(f"{location} has {'; '.join(details)}")


def _require_prompt_text(value: Any, *, layer: str) -> str:
    if not isinstance(value, str):
        raise InvalidPromptArtifactError(f"{layer} layer must be a string")
    if not value.strip():
        raise InvalidPromptArtifactError(f"{layer} layer must not be blank")
    if "\x00" in value:
        raise InvalidPromptArtifactError(f"{layer} layer must not contain NUL bytes")
    return value


__all__ = [
    "PROMPT_FILE_SCHEMA_VERSION",
    "PromptArtifact",
    "PromptLayers",
    "PromptReference",
]
