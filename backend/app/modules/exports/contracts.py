"""Stable renderer port shared by PDF now and DOCX in P6-T3."""

from __future__ import annotations

import uuid
from abc import ABC, abstractmethod
from dataclasses import dataclass
from datetime import datetime
from decimal import Decimal
from typing import Any, Mapping


@dataclass(frozen=True, slots=True)
class ExportRenderRequest:
    """Fully-resolved render input with structural and presentation data separate."""

    format: str
    paper_id: uuid.UUID
    paper_version_id: uuid.UUID
    template_id: uuid.UUID
    title: str
    version_label: str
    version_created_at: datetime | None
    duration_minutes: int | None
    total_marks: Decimal | None
    content: Mapping[str, Any]
    template_layout: Mapping[str, Any]
    artifact_type: str = "paper"
    answer_key: Mapping[str, Any] | None = None


@dataclass(frozen=True, slots=True)
class RenderedArtifact:
    format: str
    media_type: str
    extension: str
    content: bytes
    rendered_html: str | None = None
    artifact_type: str = "paper"


class ExportRenderer(ABC):
    """Format backend contract; orchestration and storage do not know PDF internals."""

    @property
    @abstractmethod
    def format(self) -> str:
        raise NotImplementedError

    @abstractmethod
    def render(self, request: ExportRenderRequest) -> RenderedArtifact:
        raise NotImplementedError


class RendererRegistry:
    def __init__(self, renderers: tuple[ExportRenderer, ...]) -> None:
        self._renderers = {renderer.format: renderer for renderer in renderers}
        if len(self._renderers) != len(renderers):
            raise ValueError("duplicate export renderer format")

    def for_format(self, format: str) -> ExportRenderer:
        try:
            return self._renderers[format]
        except KeyError as exc:
            raise ValueError(f"unsupported export format: {format}") from exc


__all__ = [
    "ExportRenderRequest",
    "ExportRenderer",
    "RenderedArtifact",
    "RendererRegistry",
]
