"""Safe read-only loader for git-reviewed prompt artifacts."""

from __future__ import annotations

import hashlib
import json
from pathlib import Path
from typing import Any

from packages.prompts.errors import (
    InvalidPromptArtifactError,
    PromptBindingMismatchError,
    PromptNotFoundError,
)
from packages.prompts.schema import PromptArtifact, PromptReference

DEFAULT_MAX_PROMPT_BYTES = 256 * 1024


class PromptFileStore:
    """Resolve logical template references beneath one fixed package root.

    The store is intentionally read-only.  A reference is parsed before any path
    is constructed, and the resolved file must remain below ``artifacts/`` even if
    a checked-out tree contains a symlink.  This keeps database-controlled
    ``template_ref`` values from becoming arbitrary filesystem reads.
    """

    def __init__(
        self,
        package_root: Path | str | None = None,
        *,
        max_prompt_bytes: int = DEFAULT_MAX_PROMPT_BYTES,
    ) -> None:
        if max_prompt_bytes < 1:
            raise ValueError("max_prompt_bytes must be positive")
        root = Path(package_root) if package_root is not None else Path(__file__).parent
        self._artifacts_root = (root / "artifacts").resolve()
        self._max_prompt_bytes = max_prompt_bytes

    def load(
        self,
        template_ref: str,
        *,
        expected_task: str | None = None,
        expected_version: int | None = None,
    ) -> PromptArtifact:
        """Load and validate one prompt without rendering its placeholders.

        ``expected_task`` and ``expected_version`` let an application repository
        bind a database row to its referenced artifact instead of trusting the path
        alone.
        """

        reference = PromptReference.parse(template_ref)
        if expected_task is not None and expected_task != reference.task:
            raise PromptBindingMismatchError(
                f"database task {expected_task!r} does not match {template_ref!r}"
            )
        if expected_version is not None and expected_version != reference.version:
            raise PromptBindingMismatchError(
                f"database version {expected_version!r} does not match {template_ref!r}"
            )

        candidate = (
            self._artifacts_root
            / reference.task
            / f"v{reference.version}.json"
        ).resolve()
        if not candidate.is_relative_to(self._artifacts_root):
            # Strict reference parsing already blocks traversal.  This second check
            # also blocks a symlink inside the trusted checkout from escaping root.
            raise PromptBindingMismatchError(
                f"prompt artifact for {template_ref!r} resolves outside artifacts root"
            )
        if not candidate.is_file():
            raise PromptNotFoundError(f"no prompt artifact exists for {template_ref!r}")

        raw_bytes = candidate.read_bytes()
        if len(raw_bytes) > self._max_prompt_bytes:
            raise InvalidPromptArtifactError(
                f"prompt artifact {template_ref!r} exceeds "
                f"{self._max_prompt_bytes} bytes"
            )
        try:
            raw_text = raw_bytes.decode("utf-8")
        except UnicodeDecodeError as exc:
            raise InvalidPromptArtifactError(
                f"prompt artifact {template_ref!r} is not valid UTF-8"
            ) from exc

        try:
            parsed = json.loads(raw_text, object_pairs_hook=_object_without_duplicates)
        except json.JSONDecodeError as exc:
            raise InvalidPromptArtifactError(
                f"prompt artifact {template_ref!r} is not valid JSON: {exc.msg}"
            ) from exc

        return PromptArtifact.from_mapping(
            parsed,
            reference=reference,
            raw_text=raw_text,
            content_sha256=hashlib.sha256(raw_bytes).hexdigest(),
        )


def _object_without_duplicates(pairs: list[tuple[str, Any]]) -> dict[str, Any]:
    value: dict[str, Any] = {}
    for key, item in pairs:
        if key in value:
            raise InvalidPromptArtifactError(
                f"prompt artifact contains duplicate JSON key {key!r}"
            )
        value[key] = item
    return value


def load_prompt(
    template_ref: str,
    *,
    expected_task: str | None = None,
    expected_version: int | None = None,
) -> PromptArtifact:
    """Load one artifact from the package's built-in read-only store."""

    return PromptFileStore().load(
        template_ref,
        expected_task=expected_task,
        expected_version=expected_version,
    )


__all__ = ["DEFAULT_MAX_PROMPT_BYTES", "PromptFileStore", "load_prompt"]
