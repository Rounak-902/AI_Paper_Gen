"""Resolve database-selected prompt metadata to reviewed prompt text."""

from __future__ import annotations

import uuid
from dataclasses import dataclass
from typing import Protocol

from app.models.prompt import PromptVersion
from packages.prompts import PromptFileStore, PromptLayers


class ActivePromptNotFoundError(LookupError):
    """No live active prompt exists for an exact task/model binding."""

    def __init__(self, *, task: str, model: str) -> None:
        self.task = task
        self.model = model
        super().__init__(
            f"no active prompt version for task={task!r}, model={model!r}"
        )


class ActivePromptSource(Protocol):
    """The one database capability required by :class:`PromptResolver`."""

    async def get_active(self, *, task: str, model: str) -> PromptVersion | None: ...


@dataclass(frozen=True, slots=True)
class ResolvedPrompt:
    """Active release metadata bound to the exact shipped file content.

    ``layers`` are intentionally unrendered.  P3-T4 will inject runtime context and
    map the logical developer layer to the provider-neutral message contract.
    ``source_text`` preserves the byte-decoded reviewed artifact for audit evidence.
    """

    prompt_version_id: uuid.UUID
    task: str
    version: int
    model: str
    template_ref: str
    layers: PromptLayers
    source_text: str
    content_sha256: str

    @property
    def text(self) -> str:
        """Alias for the exact UTF-8 artifact text requested by the registry API."""

        return self.source_text


class PromptResolver:
    """Resolve on every call so database promotion is visible without a redeploy."""

    def __init__(
        self,
        source: ActivePromptSource,
        *,
        file_store: PromptFileStore | None = None,
    ) -> None:
        self._source = source
        self._file_store = file_store or PromptFileStore()

    async def resolve(self, *, task: str, model: str) -> ResolvedPrompt:
        """Fail closed or return the sole active row and its validated real file."""

        if not isinstance(task, str) or not task.strip():
            raise ValueError("task must be a non-blank string")
        if not isinstance(model, str) or not model.strip():
            raise ValueError("model must be a non-blank string")

        row = await self._source.get_active(task=task, model=model)
        if row is None:
            raise ActivePromptNotFoundError(task=task, model=model)

        artifact = self._file_store.load(
            row.template_ref,
            expected_task=row.task,
            expected_version=row.version,
        )
        return ResolvedPrompt(
            prompt_version_id=row.id,
            task=row.task,
            version=row.version,
            model=row.model,
            template_ref=row.template_ref,
            layers=artifact.layers,
            source_text=artifact.raw_text,
            content_sha256=artifact.content_sha256,
        )


__all__ = [
    "ActivePromptNotFoundError",
    "ActivePromptSource",
    "PromptResolver",
    "ResolvedPrompt",
]
