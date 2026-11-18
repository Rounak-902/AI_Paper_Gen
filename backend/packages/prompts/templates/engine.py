"""Pure deterministic generation from typed, version-controlled templates."""

from __future__ import annotations

import math
import random
from dataclasses import dataclass
from typing import Any, Callable, Mapping

from packages.prompts.templates.errors import (
    TemplateGenerationError,
    TemplateParameterError,
)
from packages.prompts.templates.loader import TemplateCatalogStore
from packages.prompts.templates.schema import (
    ChoicePlaceholder,
    IntegerPlaceholder,
    QuestionTemplate,
)

MAX_PARAMETER_ATTEMPTS = 256


@dataclass(frozen=True, slots=True)
class Computation:
    value: int | float
    derived: Mapping[str, int | str]


@dataclass(frozen=True, slots=True)
class GeneratedQuestion:
    template_ref: str
    qtype: str
    stem: str
    options: Any
    answer: Mapping[str, Any]
    rubric: Mapping[str, Any] | None
    marks: str
    difficulty: str
    bloom: str
    language: str
    parameters: Mapping[str, int | str]
    answer_function: str

    def as_question_fields(self) -> dict[str, Any]:
        """Return only fields owned by a question template, ready for P2 validation."""
        return {
            "qtype": self.qtype,
            "marks": self.marks,
            "difficulty": self.difficulty,
            "bloom": self.bloom,
            "language": self.language,
            "source": "template_generated",
            "stem": self.stem,
            "options": self.options,
            "answer": dict(self.answer),
            "rubric": dict(self.rubric) if self.rubric is not None else None,
            "meta": {
                "template_ref": self.template_ref,
                "template_parameters": dict(self.parameters),
                "answer_function": self.answer_function,
                "deterministic_answer": True,
                "llm_calls": 0,
            },
        }


def _linear(values: Mapping[str, int | str]) -> Computation:
    return Computation(_int(values, "a") * _int(values, "x") + _int(values, "b"), {})


def _rectangle_area(values: Mapping[str, int | str]) -> Computation:
    return Computation(_int(values, "length") * _int(values, "width"), {})


def _distance(values: Mapping[str, int | str]) -> Computation:
    return Computation(_int(values, "speed") * _int(values, "time"), {})


def _sum(values: Mapping[str, int | str]) -> Computation:
    return Computation(_int(values, "a") + _int(values, "b"), {})


def _difference(values: Mapping[str, int | str]) -> Computation:
    return Computation(_int(values, "minuend") - _int(values, "subtrahend"), {})


def _percentage(values: Mapping[str, int | str]) -> Computation:
    result = _int(values, "percent") * _int(values, "whole") / 100
    return Computation(_normal_number(result), {})


def _product(values: Mapping[str, int | str]) -> Computation:
    return Computation(_int(values, "a") * _int(values, "b"), {})


def _quotient(values: Mapping[str, int | str]) -> Computation:
    return Computation(_int(values, "dividend") // _int(values, "divisor"), {})


def _remainder(values: Mapping[str, int | str]) -> Computation:
    return Computation(_int(values, "dividend") % _int(values, "divisor"), {})


def _perimeter(values: Mapping[str, int | str]) -> Computation:
    return Computation(2 * (_int(values, "length") + _int(values, "width")), {})


def _power(values: Mapping[str, int | str]) -> Computation:
    return Computation(_int(values, "base") ** _int(values, "exponent"), {})


def _sum_claim(values: Mapping[str, int | str]) -> Computation:
    return Computation(int(_int(values, "delta") == 0), {})


def _product_claim(values: Mapping[str, int | str]) -> Computation:
    return Computation(int(_int(values, "delta") == 0), {})


def _square_claim(values: Mapping[str, int | str]) -> Computation:
    return Computation(int(_int(values, "delta") == 0), {})


_ANSWER_FUNCTIONS: dict[str, Callable[[Mapping[str, int | str]], Computation]] = {
    "linear_expression": _linear,
    "rectangle_area": _rectangle_area,
    "distance": _distance,
    "sum": _sum,
    "difference": _difference,
    "percentage": _percentage,
    "product": _product,
    "quotient": _quotient,
    "remainder": _remainder,
    "perimeter": _perimeter,
    "power": _power,
    "sum_claim": _sum_claim,
    "product_claim": _product_claim,
    "square_claim": _square_claim,
}


class TemplateEngine:
    """Instantiate reviewed templates without importing or invoking an LLM provider."""

    def __init__(self, store: TemplateCatalogStore | None = None) -> None:
        self._store = store or TemplateCatalogStore()

    def generate(
        self,
        template_ref: str,
        *,
        seed: int | None = None,
        parameters: Mapping[str, int | str] | None = None,
    ) -> GeneratedQuestion:
        template = self._store.load(template_ref)
        if template.generator_function != "sample_typed_placeholders":
            raise TemplateGenerationError(
                f"unknown generator function {template.generator_function!r}"
            )
        if template.answer_function not in _ANSWER_FUNCTIONS:
            raise TemplateGenerationError(
                f"unknown answer function {template.answer_function!r}"
            )
        rng = random.Random(seed)
        sampled = self._parameters(template, rng, parameters)
        computation = _ANSWER_FUNCTIONS[template.answer_function](sampled)
        render_values = {**sampled, **computation.derived}
        try:
            stem = template.stem_template.format_map(render_values)
        except (KeyError, ValueError) as exc:
            raise TemplateGenerationError(
                f"template {template_ref} could not render its validated fields"
            ) from exc
        options, answer, rubric = self._shape_question(
            template, computation.value, rng
        )
        return GeneratedQuestion(
            template_ref=template.template_ref,
            qtype=template.qtype,
            stem=stem,
            options=options,
            answer=answer,
            rubric=rubric,
            marks=str(template.marks),
            difficulty=template.difficulty,
            bloom=template.bloom,
            language=template.language,
            parameters=dict(sampled),
            answer_function=template.answer_function,
        )

    @staticmethod
    def _parameters(
        template: QuestionTemplate,
        rng: random.Random,
        supplied: Mapping[str, int | str] | None,
    ) -> dict[str, int | str]:
        if supplied is not None:
            if set(supplied) != set(template.placeholders):
                raise TemplateParameterError(
                    "supplied parameters must exactly match typed placeholder names"
                )
            values = dict(supplied)
            for name, spec in template.placeholders.items():
                if not spec.accepts(values[name]):
                    raise TemplateParameterError(
                        f"parameter {name!r} violates its typed placeholder specification"
                    )
            if not all(c.is_satisfied(values) for c in template.constraints):
                raise TemplateParameterError("supplied parameters violate template constraints")
            return values

        for _ in range(MAX_PARAMETER_ATTEMPTS):
            values: dict[str, int | str] = {}
            for name, spec in template.placeholders.items():
                if isinstance(spec, IntegerPlaceholder):
                    allowed = [
                        value for value in range(spec.minimum, spec.maximum + 1)
                        if value not in spec.excluded
                    ]
                    values[name] = rng.choice(allowed)
                elif isinstance(spec, ChoicePlaceholder):
                    values[name] = rng.choice(spec.values)
            if all(c.is_satisfied(values) for c in template.constraints):
                return values
        raise TemplateGenerationError(
            f"template {template.template_ref} constraints were unsatisfiable after "
            f"{MAX_PARAMETER_ATTEMPTS} attempts"
        )

    @staticmethod
    def _shape_question(
        template: QuestionTemplate,
        computed_value: int | float,
        rng: random.Random,
    ) -> tuple[Any, Mapping[str, Any], Mapping[str, Any] | None]:
        if template.qtype == "numerical":
            return None, {
                "value": computed_value,
                "units": template.units,
                "tolerance": 0.0,
            }, {"steps": [f"Apply {template.answer_function} to the displayed values."]}
        if template.qtype == "fill_blank":
            return None, {"blanks": [_display(computed_value)]}, None
        if template.qtype == "one_word":
            return None, {"value": _display(computed_value), "synonyms": []}, None
        if template.qtype == "true_false":
            return None, {"value": bool(computed_value)}, None
        if template.qtype == "mcq":
            values = [computed_value] + [
                _normal_number(computed_value + offset)
                for offset in template.distractor_offsets
            ]
            if len(set(values)) != 4:
                raise TemplateGenerationError("mcq computation produced duplicate options")
            rng.shuffle(values)
            correct_index = values.index(computed_value)
            options = [
                {"text": _display(value), "is_correct": index == correct_index}
                for index, value in enumerate(values)
            ]
            return options, {"index": correct_index}, None
        raise TemplateGenerationError(f"unsupported qtype {template.qtype!r}")


def _int(values: Mapping[str, int | str], name: str) -> int:
    value = values[name]
    if type(value) is not int:
        raise TemplateGenerationError(f"answer function requires integer parameter {name!r}")
    return value


def _normal_number(value: int | float) -> int | float:
    if isinstance(value, float) and math.isfinite(value) and value.is_integer():
        return int(value)
    return value


def _display(value: int | float) -> str:
    return str(_normal_number(value))
