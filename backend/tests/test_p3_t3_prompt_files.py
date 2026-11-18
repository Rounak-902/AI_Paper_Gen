"""Focused, database-free tests for immutable P3-T3 prompt files."""

from __future__ import annotations

import hashlib
import json
from dataclasses import FrozenInstanceError
from pathlib import Path

import pytest

from packages.prompts import (
    InvalidPromptArtifactError,
    InvalidPromptReferenceError,
    PromptBindingMismatchError,
    PromptFileStore,
    PromptNotFoundError,
    PromptReference,
    load_prompt,
)


def test_loads_real_versioned_prompt_text_and_content_identity() -> None:
    artifact = load_prompt(
        "generate_mcq/v1",
        expected_task="generate_mcq",
        expected_version=1,
    )

    assert artifact.template_ref == "generate_mcq/v1"
    assert artifact.task == "generate_mcq"
    assert artifact.version == 1
    assert "curriculum-bound" in artifact.layers.system
    assert "exactly four" in artifact.layers.developer
    assert "{{curriculum_context}}" in artifact.layers.user
    assert '"task": "generate_mcq"' in artifact.raw_text
    assert artifact.content_sha256 == hashlib.sha256(
        artifact.raw_text.encode("utf-8")
    ).hexdigest()


def test_versions_are_distinct_immutable_artifacts() -> None:
    v1 = load_prompt("generate_mcq/v1")
    v2 = load_prompt("generate_mcq/v2")
    v3 = load_prompt("generate_mcq/v3")

    assert v1.version == 1
    assert v2.version == 2
    assert v3.version == 3
    assert len({v1.content_sha256, v2.content_sha256, v3.content_sha256}) == 3
    assert len({v1.layers, v2.layers, v3.layers}) == 3
    with pytest.raises(FrozenInstanceError):
        v1.version = 2  # type: ignore[misc]


def test_independent_task_artifact_has_the_same_three_layer_schema() -> None:
    artifact = load_prompt("classify_difficulty/v1")

    assert artifact.task == "classify_difficulty"
    assert artifact.version == 1
    assert "easy, medium, or hard" in artifact.layers.developer
    assert tuple(artifact.layers.__dataclass_fields__) == (
        "system",
        "developer",
        "user",
    )


@pytest.mark.parametrize(
    "template_ref",
    [
        "../generate_mcq/v1",
        "/generate_mcq/v1",
        "generate_mcq\\v1",
        "generate-mcq/v1",
        "Generate_mcq/v1",
        "generate_mcq/v0",
        "generate_mcq/v01",
        "generate_mcq/v1.json",
        "generate_mcq/v1/extra",
        " generate_mcq/v1",
    ],
)
def test_rejects_noncanonical_or_unsafe_references(template_ref: str) -> None:
    with pytest.raises(InvalidPromptReferenceError):
        PromptReference.parse(template_ref)


def test_unknown_but_well_formed_reference_is_a_typed_not_found() -> None:
    with pytest.raises(PromptNotFoundError, match="generate_mcq/v999"):
        load_prompt("generate_mcq/v999")


def test_database_binding_must_match_reference() -> None:
    store = PromptFileStore()

    with pytest.raises(PromptBindingMismatchError, match="database task"):
        store.load("generate_mcq/v1", expected_task="classify_difficulty")
    with pytest.raises(PromptBindingMismatchError, match="database version"):
        store.load("generate_mcq/v1", expected_version=2)


def _write_artifact(root: Path, template_ref: str, payload: str) -> None:
    reference = PromptReference.parse(template_ref)
    destination = root / "artifacts" / reference.task / f"v{reference.version}.json"
    destination.parent.mkdir(parents=True)
    destination.write_text(payload, encoding="utf-8")


def _valid_payload(*, task: str = "sample_task", version: int = 1) -> dict[str, object]:
    return {
        "schema_version": 1,
        "task": task,
        "version": version,
        "layers": {
            "system": "System rules.",
            "developer": "Task rules.",
            "user": "Runtime input: {{value}}",
        },
    }


@pytest.mark.parametrize(
    "mutate, match",
    [
        (lambda value: value.update(schema_version=2), "schema_version"),
        (lambda value: value.update(task="different_task"), "does not match"),
        (lambda value: value.update(version=2), "does not match"),
        (lambda value: value.update(extra="typo"), "unexpected"),
        (lambda value: value["layers"].update(system="  "), "must not be blank"),
        (lambda value: value["layers"].pop("developer"), "missing"),
    ],
)
def test_rejects_schema_drift(tmp_path: Path, mutate, match: str) -> None:
    payload = _valid_payload()
    mutate(payload)
    _write_artifact(tmp_path, "sample_task/v1", json.dumps(payload))

    with pytest.raises(InvalidPromptArtifactError, match=match):
        PromptFileStore(tmp_path).load("sample_task/v1")


def test_rejects_duplicate_json_keys(tmp_path: Path) -> None:
    payload = (
        '{"schema_version":1,"task":"sample_task","task":"other_task",'
        '"version":1,"layers":{"system":"s","developer":"d","user":"u"}}'
    )
    _write_artifact(tmp_path, "sample_task/v1", payload)

    with pytest.raises(InvalidPromptArtifactError, match="duplicate JSON key 'task'"):
        PromptFileStore(tmp_path).load("sample_task/v1")


def test_prompt_size_limit_is_enforced_before_json_parsing(tmp_path: Path) -> None:
    _write_artifact(tmp_path, "sample_task/v1", json.dumps(_valid_payload()))

    with pytest.raises(InvalidPromptArtifactError, match="exceeds 8 bytes"):
        PromptFileStore(tmp_path, max_prompt_bytes=8).load("sample_task/v1")
