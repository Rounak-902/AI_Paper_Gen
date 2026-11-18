"""Pydantic-settings configuration package (packages/config)."""

from app.core.config import (
    MIN_SECRET_LENGTH,
    PLACEHOLDER_SUBSTRINGS,
    Settings,
    get_settings,
)

__all__ = ["Settings", "get_settings", "MIN_SECRET_LENGTH", "PLACEHOLDER_SUBSTRINGS"]
