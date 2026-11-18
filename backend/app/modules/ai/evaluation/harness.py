"""Section 20 offline metrics over P3-T6 cascade results."""

from __future__ import annotations

from collections import Counter
from dataclasses import asdict, dataclass, field
from decimal import Decimal
from typing import Awaitable, Callable, Iterable

from app.modules.ai.pipeline.budget import AiCallTelemetry
from app.modules.ai.pipeline.cascade import CascadeResult
from app.modules.ai.pipeline.schemas import PipelinePrompts
from app.modules.questions.schemas import QuestionCreateIn

from .benchmark import GoldenCase


CaseRunner = Callable[[GoldenCase, "EvaluationVariant"], Awaitable["EvalAttempt"]]


@dataclass(frozen=True, slots=True)
class EvaluationVariant:
    """A caller-resolved P3-T3 prompt binding and its configured models."""

    name: str
    prompts: PipelinePrompts
    execution_mode: str = "provider"

    def as_dict(self) -> dict[str, object]:
        return {
            "name": self.name,
            "execution_mode": self.execution_mode,
            "generation_prompt_version_id": str(
                self.prompts.generation.prompt_version_id
            ),
            "generation_prompt_ref": self.prompts.generation.template_ref,
            "generation_prompt_sha256": self.prompts.generation.content_sha256,
            "generation_model": self.prompts.generation.model,
            "classification_prompt_version_id": str(
                self.prompts.classification.prompt_version_id
            ),
            "classification_prompt_ref": self.prompts.classification.template_ref,
            "classification_prompt_sha256": self.prompts.classification.content_sha256,
            "classification_model": self.prompts.classification.model,
        }


@dataclass(frozen=True, slots=True)
class EvalAttempt:
    case_id: str
    chapter_name: str
    qtype: str
    passed_validation: bool
    format_compliant_first_attempt: bool
    curriculum_aligned: bool
    latency_ms: float
    cascade_rung: int
    quality_score: float
    telemetry: tuple[AiCallTelemetry, ...] = ()
    failure_reason: str | None = None

    @property
    def ai_cost_usd(self) -> Decimal:
        # Cost is intentionally not recalculated here. P3-T6 is the sole pricing
        # authority; evaluation only aggregates its emitted telemetry field.
        return sum((event.ai_cost_usd for event in self.telemetry), start=Decimal("0"))

    @property
    def tokens_in(self) -> int:
        return sum(event.tokens_in for event in self.telemetry)

    @property
    def tokens_out(self) -> int:
        return sum(event.tokens_out for event in self.telemetry)

    @classmethod
    def from_cascade(
        cls,
        *,
        case: GoldenCase,
        result: CascadeResult,
        latency_ms: float,
        format_compliant_first_attempt: bool | None = None,
        curriculum_aligned: bool | None = None,
    ) -> "EvalAttempt":
        pipeline = result.pipeline_result
        question = result.question
        passed = result.status == "satisfied" and question is not None and pipeline is not None
        invalid_generation_retry = bool(
            pipeline
            and any(
                event.operation == "generate" and event.kind == "invalid_response"
                for event in pipeline.retries
            )
        )
        format_compliant = (
            bool(passed and not invalid_generation_retry)
            if format_compliant_first_attempt is None
            else format_compliant_first_attempt
        )
        aligned = (
            bool(question and _curriculum_aligned(question, case))
            if curriculum_aligned is None
            else curriculum_aligned
        )
        quality = _quality_score(
            case=case,
            result=result,
            format_compliant=format_compliant,
            curriculum_aligned=aligned,
        )
        reason = result.degrade_reason
        if not passed:
            trace_reason = next(
                (
                    item.detail
                    for item in reversed(result.trace)
                    if item.outcome in ("miss", "degraded")
                    and item.detail not in ("verification_failed",)
                ),
                None,
            )
            reason = trace_reason or reason or "validation_failed"
        return cls(
            case_id=case.case_id,
            chapter_name=case.chapter_name,
            qtype=case.question.qtype,
            passed_validation=passed,
            format_compliant_first_attempt=format_compliant,
            curriculum_aligned=aligned,
            latency_ms=latency_ms,
            cascade_rung=result.cascade_rung,
            quality_score=quality,
            telemetry=tuple(result.telemetry),
            failure_reason=reason,
        )


@dataclass(frozen=True, slots=True)
class EvaluationReport:
    variant: dict[str, object]
    benchmark_source: str
    benchmark_cases: int
    benchmark_cells: int
    chapters: int
    question_types: int
    precision: float
    recall: float
    quality_score: float
    format_compliance: float
    curriculum_alignment: float
    average_latency_ms: float
    average_cost_usd: Decimal
    total_cost_usd: Decimal
    average_tokens_in: float
    average_tokens_out: float
    cascade_rungs: dict[str, int]
    telemetry_events: int
    cost_by_cascade_rung_usd: dict[str, str]
    cost_by_model_task_usd: dict[str, str]
    failed_cases: tuple[dict[str, str], ...] = field(default_factory=tuple)
    cost_source: str = "P3-T6 AiCallTelemetry.ai_cost_usd (cascade_rung preserved)"

    def as_dict(self) -> dict[str, object]:
        value = asdict(self)
        value["average_cost_usd"] = str(self.average_cost_usd)
        value["total_cost_usd"] = str(self.total_cost_usd)
        value["failed_cases"] = list(self.failed_cases)
        return value


@dataclass(frozen=True, slots=True)
class ComparisonReport:
    baseline: EvaluationReport
    candidate: EvaluationReport
    deltas: dict[str, float | str]
    same_benchmark_case_ids: bool

    def as_dict(self) -> dict[str, object]:
        return {
            "baseline": self.baseline.as_dict(),
            "candidate": self.candidate.as_dict(),
            "deltas": self.deltas,
            "same_benchmark_case_ids": self.same_benchmark_case_ids,
        }


class OfflineEvaluator:
    """Run and compare variants against one immutable in-memory benchmark tuple."""

    async def evaluate(
        self,
        *,
        variant: EvaluationVariant,
        cases: Iterable[GoldenCase],
        runner: CaseRunner,
        benchmark_source: str = "user-provided golden set",
    ) -> tuple[EvaluationReport, tuple[EvalAttempt, ...]]:
        frozen_cases = tuple(cases)
        if not frozen_cases:
            raise ValueError("offline evaluation requires at least one golden case")
        attempts = tuple([await runner(case, variant) for case in frozen_cases])
        expected_ids = [case.case_id for case in frozen_cases]
        actual_ids = [attempt.case_id for attempt in attempts]
        if actual_ids != expected_ids:
            raise ValueError("runner returned attempts outside benchmark order")

        cells = {case.benchmark_cell for case in frozen_cases}
        successful_cells = {
            (attempt.chapter_name, attempt.qtype)
            for attempt in attempts
            if attempt.passed_validation
        }
        denominator = len(attempts)
        telemetry = [event for attempt in attempts for event in attempt.telemetry]
        total_cost = sum((attempt.ai_cost_usd for attempt in attempts), Decimal("0"))
        cost_by_rung: dict[str, Decimal] = {}
        cost_by_model_task: dict[str, Decimal] = {}
        for event in telemetry:
            rung = str(event.cascade_rung)
            cost_by_rung[rung] = cost_by_rung.get(rung, Decimal("0")) + event.ai_cost_usd
            binding = f"{event.model}:{event.operation}"
            cost_by_model_task[binding] = (
                cost_by_model_task.get(binding, Decimal("0")) + event.ai_cost_usd
            )
        report = EvaluationReport(
            variant=variant.as_dict(),
            benchmark_source=benchmark_source,
            benchmark_cases=denominator,
            benchmark_cells=len(cells),
            chapters=len({case.chapter_name for case in frozen_cases}),
            question_types=len({case.question.qtype for case in frozen_cases}),
            precision=_ratio(sum(item.passed_validation for item in attempts), denominator),
            recall=_ratio(len(successful_cells), len(cells)),
            quality_score=sum(item.quality_score for item in attempts) / denominator,
            format_compliance=_ratio(
                sum(item.format_compliant_first_attempt for item in attempts), denominator
            ),
            curriculum_alignment=_ratio(
                sum(item.curriculum_aligned for item in attempts), denominator
            ),
            average_latency_ms=sum(item.latency_ms for item in attempts) / denominator,
            average_cost_usd=total_cost / denominator,
            total_cost_usd=total_cost,
            average_tokens_in=sum(item.tokens_in for item in attempts) / denominator,
            average_tokens_out=sum(item.tokens_out for item in attempts) / denominator,
            cascade_rungs={
                str(rung): count
                for rung, count in sorted(Counter(item.cascade_rung for item in attempts).items())
            },
            telemetry_events=len(telemetry),
            cost_by_cascade_rung_usd={
                key: str(value) for key, value in sorted(cost_by_rung.items())
            },
            cost_by_model_task_usd={
                key: str(value) for key, value in sorted(cost_by_model_task.items())
            },
            failed_cases=tuple(
                {
                    "case_id": item.case_id,
                    "chapter": item.chapter_name,
                    "qtype": item.qtype,
                    "reason": item.failure_reason or "unknown",
                }
                for item in attempts
                if not item.passed_validation
            ),
        )
        return report, attempts

    async def compare(
        self,
        *,
        baseline: EvaluationVariant,
        candidate: EvaluationVariant,
        cases: Iterable[GoldenCase],
        runner: CaseRunner,
    ) -> ComparisonReport:
        frozen_cases = tuple(cases)
        baseline_report, baseline_attempts = await self.evaluate(
            variant=baseline, cases=frozen_cases, runner=runner
        )
        candidate_report, candidate_attempts = await self.evaluate(
            variant=candidate, cases=frozen_cases, runner=runner
        )
        same_ids = tuple(item.case_id for item in baseline_attempts) == tuple(
            item.case_id for item in candidate_attempts
        )
        if not same_ids:
            raise ValueError("prompt/model comparison did not use the same benchmark cases")
        numeric = (
            "precision",
            "recall",
            "quality_score",
            "format_compliance",
            "curriculum_alignment",
            "average_latency_ms",
            "average_tokens_in",
            "average_tokens_out",
        )
        deltas: dict[str, float | str] = {
            name: getattr(candidate_report, name) - getattr(baseline_report, name)
            for name in numeric
        }
        deltas["average_cost_usd"] = str(
            candidate_report.average_cost_usd - baseline_report.average_cost_usd
        )
        return ComparisonReport(baseline_report, candidate_report, deltas, same_ids)


def _ratio(numerator: int, denominator: int) -> float:
    return numerator / denominator if denominator else 0.0


def _curriculum_aligned(question: QuestionCreateIn, case: GoldenCase) -> bool:
    expected = case.question
    return bool(
        question.board_id == expected.board_id
        and question.class_id == expected.class_id
        and question.subject_id == expected.subject_id
        and question.chapter_id == expected.chapter_id
        and set(question.learning_outcome_ids) == set(expected.learning_outcome_ids)
    )


def _quality_score(
    *,
    case: GoldenCase,
    result: CascadeResult,
    format_compliant: bool,
    curriculum_aligned: bool,
) -> float:
    """Apply §15's exact weights to independently observable pipeline facts."""

    question = result.question
    pipeline = result.pipeline_result
    if question is None or pipeline is None:
        return 0.0
    expected = case.question
    classification = pipeline.classification
    similarity = 1.0
    similarities = [
        float(candidate.similarity)
        for warning in pipeline.similarity.warnings
        for candidate in getattr(warning, "candidates", ())
    ]
    if similarities:
        similarity = max(0.0, 1.0 - max(similarities))
    grammar = 1.0
    if classification is not None:
        grammar = max(0.0, 1.0 - 0.25 * len(classification.grammar_flags))
    coverage = 1.0 if _curriculum_aligned(question, case) else 0.0
    format_score = 1.0 if format_compliant and not any(
        flag.startswith("format") for flag in pipeline.flags
    ) else 0.0
    dimensions = {
        "curriculum": 1.0 if curriculum_aligned else 0.0,
        "difficulty": 1.0 if question.difficulty == expected.difficulty else 0.0,
        "bloom": 1.0 if question.bloom == expected.bloom else 0.0,
        "similarity": similarity,
        "grammar": grammar,
        "coverage": coverage,
        "format": format_score,
    }
    weights = {
        "curriculum": 0.25,
        "difficulty": 0.15,
        "bloom": 0.10,
        "similarity": 0.15,
        "grammar": 0.15,
        "coverage": 0.10,
        "format": 0.10,
    }
    return sum(dimensions[name] * weight for name, weight in weights.items())


__all__ = [
    "ComparisonReport",
    "EvalAttempt",
    "EvaluationReport",
    "EvaluationVariant",
    "OfflineEvaluator",
]
