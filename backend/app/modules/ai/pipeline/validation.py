"""Request/evidence gates and independent quantitative claim execution."""
from __future__ import annotations

import asyncio
import json
import math
import re
import sys
from decimal import Decimal, InvalidOperation
from pathlib import Path
from app.common.errors import ValidationError
from app.modules.questions.schemas import QuestionCreateIn
from .schemas import Classification, GeneratedContent, GenerationContext


class PipelineValidationError(ValidationError):
    def __init__(self, check: str):
        self.check = check
        super().__init__(f"Generated question failed {check} validation.")


def require(condition: bool, check: str):
    if not condition:
        raise PipelineValidationError(check)


def unit_key(value: str | None):
    # Representation equivalence only, never implicit dimensional conversion.
    return value.strip().replace("²", "^2").replace("³", "^3") if isinstance(value, str) else value


async def compute(expression: str, timeout_ms: int = 2000):
    require(0 < timeout_ms <= 2000, "sandbox_timeout_config")
    require(len(expression) <= 512, "solution_expression_size")
    proc = await asyncio.create_subprocess_exec(sys.executable, "-I", str(Path(__file__).with_name("expression_worker.py")),
                                               stdin=asyncio.subprocess.PIPE, stdout=asyncio.subprocess.PIPE,
                                               stderr=asyncio.subprocess.DEVNULL)
    try:
        # Trusted interpreter/SymPy startup is separate from execution of model
        # input. No expression is delivered until OS limits and I/O denial are
        # installed. Cold Windows imports can exceed the 2s execution budget.
        ready = await asyncio.wait_for(proc.stdout.readline(), 15)
        require(ready.strip() == b"READY", "sandbox_initialization")
        output, _ = await asyncio.wait_for(proc.communicate(json.dumps({"expression": expression}).encode()), timeout_ms / 1000)
    except BaseException:
        if proc.returncode is None:
            proc.kill()
        await proc.wait()
        raise
    require(proc.returncode == 0, "solution_execution")
    return json.loads(output)["value"]


class ValidationEngine:
    def __init__(self, timeout_ms: int = 2000):
        self.timeout_ms = timeout_ms

    def request(self, question: QuestionCreateIn, context: GenerationContext):
        slot = context.slot
        for name in ("qtype", "marks", "difficulty"):
            require(getattr(question, name) == getattr(slot, name), f"request_{name}")
        require(slot.bloom is None or question.bloom == slot.bloom, "request_bloom")
        require(question.language == context.language, "request_language")
        for name in ("board_id", "class_id", "subject_id"):
            require(getattr(question, name) == getattr(slot, name), f"curriculum_{name}")
        require(question.chapter_id in slot.chapter_ids, "curriculum_chapter")
        require(set(question.learning_outcome_ids) == set(slot.learning_outcome_ids), "curriculum_outcomes")
        require(bool(question.stem.strip()), "stem")
        if question.qtype == "mcq":
            require(len(question.options) == 4, "mcq_four_options")
            require(all(isinstance(o, dict) and isinstance(o.get("text"), str)
                        and type(o.get("is_correct")) is bool for o in question.options), "mcq_option_shape")
            texts = [o.get("text", "").strip().casefold() for o in question.options]
            require(all(texts) and len(set(texts)) == 4, "mcq_distinct_options")
            index = question.answer["index"]
            require(type(index) is int and 0 <= index < 4 and question.options[index].get("is_correct") is True, "mcq_key")
        if question.qtype in ("short_answer", "long_answer"):
            key = "key_points" if question.qtype == "short_answer" else "marking_scheme"
            require(isinstance(question.rubric, dict) and bool(question.rubric.get(key)), "rubric_completeness")
            if question.qtype == "long_answer":
                try:
                    points = question.rubric[key]
                    require(all(isinstance(p, dict) and p.get("point") and Decimal(str(p["marks"])) > 0 for p in points), "rubric_points")
                    require(sum(Decimal(str(p["marks"])) for p in points) == question.marks, "rubric_marks")
                except (KeyError, TypeError, InvalidOperation) as exc:
                    raise PipelineValidationError("rubric_marks") from exc
        if question.qtype == "numerical":
            require(isinstance(question.rubric, dict) and isinstance(question.rubric.get("steps"), list)
                    and bool(question.rubric["steps"]) and all(isinstance(s, str) and s.strip() for s in question.rubric["steps"]), "rubric_completeness")

    async def generated(self, question: GeneratedContent, context: GenerationContext) -> list[str]:
        self.request(question, context)
        ids = {c.question_id for c in context.retrieval.candidates}
        require(set(question.cited_question_ids) <= ids, "source_citations")
        require(all(0 <= i < len(context.curriculum.textbook_snippets) for i in question.cited_snippet_indices), "snippet_citations")
        require(bool(question.cited_question_ids or question.cited_snippet_indices), "grounding_citation")
        qt = question.qtype
        flags = []
        numeric = qt in ("numerical", "true_false")
        observed = None
        if qt == "numerical":
            observed = question.answer["value"]
            require(type(observed) in (int, float) and math.isfinite(observed), "numeric_answer")
            tolerance = question.answer.get("tolerance", 0)
            require(type(tolerance) in (int, float) and math.isfinite(tolerance) and 0 <= tolerance <= 0.01, "numeric_tolerance")
            require(unit_key(question.solution_units) == unit_key(question.answer["units"]), "solution_units")
        elif qt == "true_false":
            observed = question.answer["value"]
        elif qt in ("mcq", "one_word", "fill_blank"):
            values = ([o["text"] for o in question.options] if qt == "mcq" else
                      question.answer["blanks"] if qt == "fill_blank" else [question.answer["value"]])
            def number(value):
                text = str(value).strip()
                match = re.fullmatch(r"([+-]?(?:\d+(?:\.\d*)?|\.\d+)(?:[eE][+-]?\d+)?)\s*(.*)", text)
                if match is None: raise ValueError("not a numeric answer")
                suffix = match.group(2)
                if suffix and unit_key(suffix) != unit_key(question.solution_units): raise ValueError("option units differ from derivation")
                return float(match.group(1))
            quantitative_claim = bool(question.solution_expression) or any(re.match(r"^[+-]?(?:\d|\.\d)", str(v).strip()) for v in values)
            try:
                numbers = [number(v) for v in values]
                require(all(math.isfinite(v) for v in numbers), "finite_options")
                numeric = True
                observed = numbers[question.answer["index"]] if qt == "mcq" else numbers[0]
                require(qt != "fill_blank" or len(numbers) == 1, "numeric_blank_coverage")
            except (TypeError, ValueError):
                require(not quantitative_claim, "quantitative_options_unverifiable")
                flags.append("correctness_advisory_only")
        else:
            flags.append("correctness_advisory_only")
        if numeric:
            require(bool(question.solution_steps and question.solution_expression), "executable_solution_required")
            try:
                computed = await compute(question.solution_expression, self.timeout_ms)
            except TimeoutError as exc:
                raise PipelineValidationError("solution_timeout") from exc
            if qt == "true_false":
                require(type(computed) is bool and computed is observed, "executed_boolean_answer")
            else:
                require(type(computed) is not bool and abs(computed - observed) <= (question.answer.get("tolerance", 0) if qt == "numerical" else 1e-9), "executed_answer")
                if qt == "mcq":
                    require(sum(abs(v - computed) <= 1e-9 for v in numbers) == 1, "mcq_unique_numeric_answer")
        if re.search(r"```|\*\*|<[^>]+>", question.stem): flags.append("format_artifacts")
        if qt == "mcq" and question.solution_steps:
            solution = question.solution_steps
            # Review only: missing separators before a numbered step or sentence.
            if re.search(r"(?<!\d)[.!?]\d+[.)]\s+|[.!?][A-Z][a-z]", solution):
                flags.append("solution_format_run_on")
            # Inspect decoded text: doubled command slashes and JSON-consumed
            # LaTeX escapes (e.g. \t in \times or \f in \frac) are artifacts.
            prose = re.sub(r"(?s)\\\(.*?\\\)|\\\[.*?\\\]|\$\$.*?\$\$|(?<!\\)\$.*?(?<!\\)\$", "", solution)
            if (re.search(r"\\{2,}[A-Za-z]+", solution)
                    or re.search(r"[\x00-\x08\x0b\x0c\x0e-\x1f]", solution)
                    or re.search(r"[\t\r](?:imes|ext|heta|au|ight|ho)\b", solution)
                    # A single decoded command also needs math delimiters;
                    # otherwise the captured plain-text \times renders literally.
                    or re.search(r"\\(?:times|frac|sqrt|cdot|text|theta|tau|rho|left|right)\b", prose)):
                flags.append("solution_format_latex_escape")
        if not question.stem.rstrip().endswith(("?", ".", "!", "___")): flags.append("format_punctuation")
        return flags

    def classified(self, classification: Classification, context: GenerationContext) -> list[str]:
        require(classification.curriculum_aligned, "content_curriculum_alignment")
        require(classification.language_matches, "content_language")
        flags = [f"grammar:{f}" for f in classification.grammar_flags]
        flags += [f"format:{f}" for f in classification.format_flags]
        if classification.difficulty != context.slot.difficulty: flags.append("classifier_difficulty_mismatch")
        if context.slot.bloom and classification.bloom != context.slot.bloom: flags.append("classifier_bloom_mismatch")
        if classification.confidence < 0.7: flags.append("classifier_low_confidence")
        if classification.qualitative_correctness_confidence < 0.7: flags.append("qualitative_correctness_review")
        return flags
