"""Typed failures for deterministic question-template generation."""


class TemplateError(Exception):
    """Base class for deterministic template failures."""


class TemplateNotFoundError(TemplateError):
    """The requested immutable template reference is absent."""


class InvalidTemplateCatalogError(TemplateError):
    """A version-controlled catalog does not satisfy its strict schema."""


class TemplateParameterError(TemplateError):
    """Supplied or sampled parameters violate a typed specification."""


class TemplateGenerationError(TemplateError):
    """A valid template could not produce a valid deterministic instance."""
