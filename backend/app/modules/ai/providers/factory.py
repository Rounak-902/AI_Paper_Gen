"""Configuration-owned construction of the active LLM provider."""

from __future__ import annotations

from app.core.config import Settings, get_settings
from app.modules.ai.providers.base import LlmProvider


def create_llm_provider(settings: Settings | None = None) -> LlmProvider:
    """Create the sole enabled adapter without leaking it into business modules."""

    resolved = settings or get_settings()
    provider_id = resolved.AI_PROVIDER.strip().casefold()
    if provider_id != "gemini":
        raise ValueError(
            f"AI_PROVIDER={resolved.AI_PROVIDER!r} is not implemented; "
            "ADR-008 currently permits only 'gemini'."
        )

    # Local import keeps the base/factory dependency one-way and makes a future
    # adapter addition a change isolated to this composition root.
    from app.modules.ai.providers.gemini import GeminiAdapter

    return GeminiAdapter(settings=resolved)


__all__ = ["create_llm_provider"]
