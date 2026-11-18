"""Stable, provider-neutral LLM interface."""

from __future__ import annotations

from abc import ABC, abstractmethod
from typing import ClassVar

from app.modules.ai.providers.schemas import LlmRequest, LlmResponse


class LlmProvider(ABC):
    """Contract a deliberate future provider replacement must implement.

    The three methods represent capabilities, not vendor model names.  Concrete
    model selection belongs to an adapter and its deployment configuration.
    """

    id: ClassVar[str]

    @abstractmethod
    async def generate(self, request: LlmRequest) -> LlmResponse:
        """Run the stronger generation/reasoning tier."""

    @abstractmethod
    async def classify(self, request: LlmRequest) -> LlmResponse:
        """Run the cheaper classification/extraction tier."""

    @abstractmethod
    async def embed(self, texts: list[str]) -> list[list[float]]:
        """Return one normalized, configured-width vector per input text."""


__all__ = ["LlmProvider"]
