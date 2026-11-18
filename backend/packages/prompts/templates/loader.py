"""Read-only loader for the reviewed deterministic template catalog."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from packages.prompts.templates.errors import (
    InvalidTemplateCatalogError,
    TemplateNotFoundError,
)
from packages.prompts.templates.schema import (
    QuestionTemplate,
    TemplateCatalog,
    TemplateReference,
)

DEFAULT_MAX_CATALOG_BYTES = 512 * 1024


class TemplateCatalogStore:
    def __init__(
        self,
        catalog_file: Path | str | None = None,
        *,
        max_catalog_bytes: int = DEFAULT_MAX_CATALOG_BYTES,
    ) -> None:
        if max_catalog_bytes < 1:
            raise ValueError("max_catalog_bytes must be positive")
        self._catalog_file = (
            Path(catalog_file)
            if catalog_file is not None
            else Path(__file__).parent / "catalog" / "v1.json"
        ).resolve()
        self._max_catalog_bytes = max_catalog_bytes
        self._catalog: TemplateCatalog | None = None

    def load_catalog(self) -> TemplateCatalog:
        if self._catalog is not None:
            return self._catalog
        try:
            raw = self._catalog_file.read_bytes()
        except FileNotFoundError as exc:
            raise TemplateNotFoundError(
                f"template catalog does not exist: {self._catalog_file.name}"
            ) from exc
        if len(raw) > self._max_catalog_bytes:
            raise InvalidTemplateCatalogError(
                f"template catalog exceeds {self._max_catalog_bytes} bytes"
            )
        try:
            text = raw.decode("utf-8")
            parsed = json.loads(text, object_pairs_hook=_object_without_duplicates)
        except UnicodeDecodeError as exc:
            raise InvalidTemplateCatalogError("template catalog is not valid UTF-8") from exc
        except json.JSONDecodeError as exc:
            raise InvalidTemplateCatalogError(
                f"template catalog is not valid JSON: {exc.msg}"
            ) from exc
        self._catalog = TemplateCatalog.from_mapping(parsed)
        return self._catalog

    def load(self, template_ref: str) -> QuestionTemplate:
        reference = TemplateReference.parse(template_ref)
        for template in self.load_catalog().templates:
            if (
                template.template_id == reference.template_id
                and template.version == reference.version
            ):
                return template
        raise TemplateNotFoundError(f"no deterministic template exists for {template_ref!r}")


def _object_without_duplicates(pairs: list[tuple[str, Any]]) -> dict[str, Any]:
    result: dict[str, Any] = {}
    for key, value in pairs:
        if key in result:
            raise InvalidTemplateCatalogError(
                f"template catalog contains duplicate JSON key {key!r}"
            )
        result[key] = value
    return result
