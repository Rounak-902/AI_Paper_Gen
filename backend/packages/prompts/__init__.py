"""Immutable, file-backed LLM prompt artifacts.

The database stores release metadata and a ``template_ref``.  This package owns
the reviewed prompt text that reference resolves to.  It deliberately does not
render runtime context or depend on the provider layer; P3-T4 will compose these
three logical layers into provider requests.
"""

from packages.prompts.errors import (
    InvalidPromptArtifactError,
    InvalidPromptReferenceError,
    PromptBindingMismatchError,
    PromptFileError,
    PromptNotFoundError,
)
from packages.prompts.loader import PromptFileStore, load_prompt
from packages.prompts.schema import PromptArtifact, PromptLayers, PromptReference

__all__ = [
    "InvalidPromptArtifactError",
    "InvalidPromptReferenceError",
    "PromptArtifact",
    "PromptBindingMismatchError",
    "PromptFileError",
    "PromptFileStore",
    "PromptLayers",
    "PromptNotFoundError",
    "PromptReference",
    "load_prompt",
]
