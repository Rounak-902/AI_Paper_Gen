"""Typed failures for prompt reference and artifact loading."""

from __future__ import annotations


class PromptFileError(ValueError):
    """Base class for deterministic prompt-package failures."""


class InvalidPromptReferenceError(PromptFileError):
    """A template reference is malformed or attempts to leave the prompt root."""


class PromptNotFoundError(PromptFileError):
    """A syntactically valid template reference has no shipped artifact."""


class InvalidPromptArtifactError(PromptFileError):
    """A shipped prompt artifact does not satisfy the versioned file schema."""


class PromptBindingMismatchError(InvalidPromptArtifactError):
    """Database release metadata and the referenced file disagree."""


__all__ = [
    "InvalidPromptArtifactError",
    "InvalidPromptReferenceError",
    "PromptBindingMismatchError",
    "PromptFileError",
    "PromptNotFoundError",
]
