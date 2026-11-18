"""Immutable value objects for typed deterministic question templates."""

from __future__ import annotations

import decimal
import re
import string
from dataclasses import dataclass
from typing import Any, Mapping

from packages.prompts.templates.errors import InvalidTemplateCatalogError

TEMPLATE_CATALOG_SCHEMA_VERSION = 1
SUPPORTED_TEMPLATE_QTYPES = (
    "numerical",
    "fill_blank",
    "one_word",
    "mcq",
    "true_false",
)

_IDENTIFIER_RE = re.compile(r"[a-z][a-z0-9]*(?:_[a-z0-9]+)*\Z")
_REFERENCE_RE = re.compile(
    r"(?P<template_id>[a-z][a-z0-9]*(?:_[a-z0-9]+)*)/v(?P<version>[1-9][0-9]*)\Z"
)


@dataclass(frozen=True, slots=True)
class TemplateReference:
    template_id: str
    version: int

    @classmethod
    def parse(cls, value: str) -> "TemplateReference":
        if not isinstance(value, str):
            raise TemplateNotFoundError("template_ref must be a string")
        match = _REFERENCE_RE.fullmatch(value)
        if match is None:
            raise TemplateNotFoundError(
                "template_ref must match '<lower_snake_case_id>/v<positive_integer>'"
            )
        return cls(match.group("template_id"), int(match.group("version")))

    def __str__(self) -> str:
        return f"{self.template_id}/v{self.version}"


@dataclass(frozen=True, slots=True)
class IntegerPlaceholder:
    minimum: int
    maximum: int
    excluded: tuple[int, ...] = ()

    def accepts(self, value: Any) -> bool:
        return (
            type(value) is int
            and self.minimum <= value <= self.maximum
            and value not in self.excluded
        )


@dataclass(frozen=True, slots=True)
class ChoicePlaceholder:
    values: tuple[int | str, ...]

    def accepts(self, value: Any) -> bool:
        return type(value) in (int, str) and value in self.values


PlaceholderSpec = IntegerPlaceholder | ChoicePlaceholder


@dataclass(frozen=True, slots=True)
class ParameterConstraint:
    kind: str
    left: str
    right: str

    def is_satisfied(self, values: Mapping[str, int | str]) -> bool:
        left = values[self.left]
        right = values[self.right]
        if self.kind == "not_equal":
            return left != right
        if type(left) is not int or type(right) is not int:
            return False
        if self.kind == "greater_than":
            return left > right
        if self.kind == "divisible":
            return right != 0 and left % right == 0
        return False


@dataclass(frozen=True, slots=True)
class QuestionTemplate:
    template_id: str
    version: int
    qtype: str
    title: str
    stem_template: str
    placeholders: Mapping[str, PlaceholderSpec]
    derived_fields: tuple[str, ...]
    constraints: tuple[ParameterConstraint, ...]
    generator_function: str
    answer_function: str
    marks: decimal.Decimal
    difficulty: str
    bloom: str
    language: str
    units: str | None
    distractor_offsets: tuple[int, ...]

    @property
    def template_ref(self) -> str:
        return f"{self.template_id}/v{self.version}"


@dataclass(frozen=True, slots=True)
class TemplateCatalog:
    schema_version: int
    catalog_version: int
    templates: tuple[QuestionTemplate, ...]

    @classmethod
    def from_mapping(cls, value: Any) -> "TemplateCatalog":
        root = _mapping(value, "catalog")
        _exact_keys(root, {"schema_version", "catalog_version", "templates"}, "catalog")
        if (
            type(root["schema_version"]) is not int
            or root["schema_version"] != TEMPLATE_CATALOG_SCHEMA_VERSION
        ):
            raise InvalidTemplateCatalogError(
                f"schema_version must be {TEMPLATE_CATALOG_SCHEMA_VERSION}"
            )
        catalog_version = root["catalog_version"]
        if type(catalog_version) is not int or catalog_version < 1:
            raise InvalidTemplateCatalogError("catalog_version must be a positive integer")
        raw_templates = root["templates"]
        if not isinstance(raw_templates, list) or not raw_templates:
            raise InvalidTemplateCatalogError("templates must be a non-empty array")
        templates = tuple(_parse_template(item) for item in raw_templates)
        refs = [template.template_ref for template in templates]
        if len(refs) != len(set(refs)):
            raise InvalidTemplateCatalogError("template references must be unique")
        return cls(TEMPLATE_CATALOG_SCHEMA_VERSION, catalog_version, templates)


def _parse_template(value: Any) -> QuestionTemplate:
    item = _mapping(value, "template")
    expected = {
        "id", "version", "qtype", "title", "stem", "placeholders",
        "derived_fields", "constraints", "generator_function", "answer_function",
        "marks", "difficulty", "bloom", "language", "units", "distractor_offsets",
    }
    _exact_keys(item, expected, "template")
    template_id = _identifier(item["id"], "template.id")
    version = item["version"]
    if type(version) is not int or version < 1:
        raise InvalidTemplateCatalogError("template.version must be a positive integer")
    qtype = item["qtype"]
    if qtype not in SUPPORTED_TEMPLATE_QTYPES:
        raise InvalidTemplateCatalogError(
            f"template.qtype must be one of {SUPPORTED_TEMPLATE_QTYPES}"
        )
    title = _nonblank(item["title"], "template.title")
    stem = _nonblank(item["stem"], "template.stem")
    placeholder_map = _mapping(item["placeholders"], "template.placeholders")
    if not placeholder_map:
        raise InvalidTemplateCatalogError("template.placeholders must not be empty")
    placeholders = {
        _identifier(name, "placeholder name"): _parse_placeholder(spec, name)
        for name, spec in placeholder_map.items()
    }
    derived_fields = _string_tuple(item["derived_fields"], "derived_fields")
    if set(derived_fields) & set(placeholders):
        raise InvalidTemplateCatalogError("derived_fields cannot shadow typed placeholders")
    field_names: set[str] = set()
    for _, field_name, format_spec, conversion in string.Formatter().parse(stem):
        if field_name is None:
            continue
        if not _IDENTIFIER_RE.fullmatch(field_name) or format_spec or conversion:
            raise InvalidTemplateCatalogError(
                "stem placeholders must be simple lower_snake_case names"
            )
        field_names.add(field_name)
    available_fields = set(placeholders) | set(derived_fields)
    if field_names != available_fields:
        raise InvalidTemplateCatalogError(
            f"stem fields must exactly match placeholders plus derived_fields; "
            f"expected {sorted(available_fields)}, found {sorted(field_names)}"
        )
    constraints_raw = item["constraints"]
    if not isinstance(constraints_raw, list):
        raise InvalidTemplateCatalogError("constraints must be an array")
    constraints = tuple(_parse_constraint(raw, set(placeholders)) for raw in constraints_raw)
    generator_function = _identifier(item["generator_function"], "generator_function")
    answer_function = _identifier(item["answer_function"], "answer_function")
    try:
        marks = decimal.Decimal(str(item["marks"]))
    except decimal.InvalidOperation as exc:
        raise InvalidTemplateCatalogError("marks must be decimal-compatible") from exc
    if marks <= 0:
        raise InvalidTemplateCatalogError("marks must be positive")
    difficulty = item["difficulty"]
    if difficulty not in ("easy", "medium", "hard"):
        raise InvalidTemplateCatalogError("difficulty must be easy, medium, or hard")
    bloom = item["bloom"]
    if bloom not in ("remember", "understand", "apply", "analyze", "evaluate", "create"):
        raise InvalidTemplateCatalogError("bloom is invalid")
    language = _nonblank(item["language"], "language")
    units = item["units"]
    if units is not None and (not isinstance(units, str) or not units.strip()):
        raise InvalidTemplateCatalogError("units must be null or a non-blank string")
    if qtype == "numerical" and units is None:
        raise InvalidTemplateCatalogError("numerical templates require units")
    offsets_raw = item["distractor_offsets"]
    if not isinstance(offsets_raw, list) or any(type(v) is not int for v in offsets_raw):
        raise InvalidTemplateCatalogError("distractor_offsets must be an integer array")
    offsets = tuple(offsets_raw)
    if qtype == "mcq" and (len(offsets) != 3 or 0 in offsets or len(set(offsets)) != 3):
        raise InvalidTemplateCatalogError("mcq requires three unique non-zero distractor offsets")
    if qtype != "mcq" and offsets:
        raise InvalidTemplateCatalogError("only mcq templates may define distractor offsets")
    return QuestionTemplate(
        template_id, version, qtype, title, stem, placeholders, derived_fields,
        constraints, generator_function, answer_function, marks, difficulty,
        bloom, language, units, offsets,
    )


def _parse_placeholder(value: Any, name: str) -> PlaceholderSpec:
    spec = _mapping(value, f"placeholder {name}")
    kind = spec.get("type")
    if kind == "integer":
        _exact_keys(spec, {"type", "min", "max", "exclude"}, f"placeholder {name}")
        minimum, maximum = spec["min"], spec["max"]
        excluded = spec["exclude"]
        if type(minimum) is not int or type(maximum) is not int or minimum > maximum:
            raise InvalidTemplateCatalogError(f"placeholder {name} has invalid integer range")
        if not isinstance(excluded, list) or any(type(v) is not int for v in excluded):
            raise InvalidTemplateCatalogError(f"placeholder {name}.exclude must be integers")
        parsed = IntegerPlaceholder(minimum, maximum, tuple(excluded))
        if not any(parsed.accepts(v) for v in range(minimum, maximum + 1)):
            raise InvalidTemplateCatalogError(f"placeholder {name} has no allowed values")
        return parsed
    if kind == "choice":
        _exact_keys(spec, {"type", "values"}, f"placeholder {name}")
        values = spec["values"]
        if (
            not isinstance(values, list) or not values
            or any(type(v) not in (int, str) for v in values)
            or len(values) != len(set((type(v), v) for v in values))
        ):
            raise InvalidTemplateCatalogError(f"placeholder {name}.values must be unique int/string values")
        return ChoicePlaceholder(tuple(values))
    raise InvalidTemplateCatalogError(f"placeholder {name}.type must be integer or choice")


def _parse_constraint(value: Any, names: set[str]) -> ParameterConstraint:
    raw = _mapping(value, "constraint")
    _exact_keys(raw, {"kind", "left", "right"}, "constraint")
    kind = raw["kind"]
    if kind not in ("not_equal", "greater_than", "divisible"):
        raise InvalidTemplateCatalogError("constraint.kind is invalid")
    left = _identifier(raw["left"], "constraint.left")
    right = _identifier(raw["right"], "constraint.right")
    if left not in names or right not in names:
        raise InvalidTemplateCatalogError("constraints may only reference typed placeholders")
    return ParameterConstraint(kind, left, right)


def _mapping(value: Any, location: str) -> Mapping[str, Any]:
    if not isinstance(value, Mapping) or not all(isinstance(k, str) for k in value):
        raise InvalidTemplateCatalogError(f"{location} must be an object with string keys")
    return value


def _exact_keys(value: Mapping[str, Any], expected: set[str], location: str) -> None:
    missing = sorted(expected - set(value))
    unexpected = sorted(set(value) - expected)
    if missing or unexpected:
        raise InvalidTemplateCatalogError(
            f"{location} keys differ: missing={missing}, unexpected={unexpected}"
        )


def _identifier(value: Any, location: str) -> str:
    if not isinstance(value, str) or not _IDENTIFIER_RE.fullmatch(value):
        raise InvalidTemplateCatalogError(f"{location} must be lower_snake_case")
    return value


def _nonblank(value: Any, location: str) -> str:
    if not isinstance(value, str) or not value.strip() or "\x00" in value:
        raise InvalidTemplateCatalogError(f"{location} must be a non-blank string")
    return value


def _string_tuple(value: Any, location: str) -> tuple[str, ...]:
    if not isinstance(value, list):
        raise InvalidTemplateCatalogError(f"{location} must be an array")
    result = tuple(_identifier(item, location) for item in value)
    if len(result) != len(set(result)):
        raise InvalidTemplateCatalogError(f"{location} values must be unique")
    return result
