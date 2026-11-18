"""Provider-neutral request and response models for LLM calls.

The configured model is deliberately absent from :class:`LlmRequest`.  The method
(``generate`` or ``classify``) selects a capability tier and the adapter resolves
that tier through configuration.  This makes it impossible for business code to
smuggle a provider-specific model identifier across the interface.
"""

from __future__ import annotations

from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator


class ProviderModel(BaseModel):
    """Strict models at the provider boundary catch drift before an API call."""

    model_config = ConfigDict(extra="forbid")


class LlmMessage(ProviderModel):
    """One text-only chat turn in provider-neutral role vocabulary."""

    role: Literal["system", "user", "assistant"]
    content: str = Field(min_length=1)

    @field_validator("content")
    @classmethod
    def reject_blank_content(cls, value: str) -> str:
        if not value.strip():
            raise ValueError("message content must not be blank")
        return value


class LlmRequest(ProviderModel):
    """A text completion request shared by generation and classification."""

    messages: list[LlmMessage] = Field(min_length=1)
    # ``None`` means use the deployment setting.  A caller may deliberately tune a
    # request, but model routing itself remains adapter/config owned.
    temperature: float | None = Field(default=None, ge=0.0, le=2.0)
    max_tokens: int | None = Field(default=None, ge=1)
    response_schema: dict[str, Any] | None = None
    # Standard requests leave provider reasoning at its normal setting.  Rung 6
    # is the sole caller allowed to request the explicitly larger reasoning
    # budget configured by the Gemini adapter.
    reasoning_effort: Literal["standard", "extended"] = "standard"

    @model_validator(mode="after")
    def require_non_system_message(self) -> "LlmRequest":
        if not any(message.role != "system" for message in self.messages):
            raise ValueError("at least one user or assistant message is required")
        return self


class LlmResponse(ProviderModel):
    """Normalized response and metering facts returned by every text adapter."""

    content: str
    # The requested deployment model and the concrete version reported by the
    # provider are intentionally separate.  Aliases may resolve to a newer version.
    model: str
    model_version: str | None = None
    provider: str
    tokens_in: int = Field(ge=0)
    tokens_out: int = Field(ge=0)
    latency_ms: int = Field(ge=0)
    # Pricing/budget computation belongs to P3-T6.  ``None`` is honest and avoids
    # recording a fabricated zero-cost event until that pricing table exists.
    cost_estimate: float | None = Field(default=None, ge=0.0)
    finish_reason: str
    safety_blocked: bool = False
    safety_categories: list[str] = Field(default_factory=list)


__all__ = ["LlmMessage", "LlmRequest", "LlmResponse"]
