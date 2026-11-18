"""The explicit P3-T6 eight-rung, first-satisfying-result cascade."""
from __future__ import annotations

import hashlib
import inspect
import json
import uuid
from dataclasses import dataclass, field
from typing import Awaitable, Callable, Literal, Protocol

from app.core.config import get_settings
from app.modules.ai.providers.base import LlmProvider
from app.modules.ai.providers.errors import LlmProviderError
from app.modules.questions.schemas import QuestionCreateIn
from app.modules.retrieval.schemas import ScoredCandidate

from .budget import (
    AiCallTelemetry,
    BudgetExhaustedError,
    BudgetedProvider,
    CostRates,
    TokenBudgetGate,
)
from .schemas import GenerationContext, PipelinePrompts, PipelineResult, SimilarityResult
from .service import GenerationPipeline
from .validation import PipelineValidationError, ValidationEngine


ESCALATION_VERIFICATION_FAILURES = 2

RUNG_NAMES: tuple[str, ...] = (
    "exact_cache",
    "bank_reuse",
    "template_generation",
    "rules_local",
    "gemini_flash",
    "gemini_pro",
    "escalation",
    "degrade",
)


@dataclass(frozen=True, slots=True)
class ExactCacheKey:
    tenant_id: uuid.UUID
    content_hash: str
    prompt_hash: str
    model: str


@dataclass(frozen=True, slots=True)
class CachedQuestion:
    question: QuestionCreateIn
    source_rung: int


class ExactResultCache(Protocol):
    async def get(self, key: ExactCacheKey) -> CachedQuestion | None: ...

    async def put(self, key: ExactCacheKey, value: CachedQuestion) -> None: ...


class InMemoryExactResultCache:
    """Tenant-keyed exact cache; safe as a worker-local implementation."""

    def __init__(self) -> None:
        self._values: dict[ExactCacheKey, CachedQuestion] = {}

    async def get(self, key: ExactCacheKey) -> CachedQuestion | None:
        value = self._values.get(key)
        if value is None:
            return None
        return CachedQuestion(value.question.model_copy(deep=True), value.source_rung)

    async def put(self, key: ExactCacheKey, value: CachedQuestion) -> None:
        self._values[key] = CachedQuestion(value.question.model_copy(deep=True), value.source_rung)


BankQuestionResolver = Callable[[ScoredCandidate], Awaitable[QuestionCreateIn | None]]
SimilarityChecker = Callable[[QuestionCreateIn], Awaitable[SimilarityResult]]


@dataclass(frozen=True, slots=True)
class CascadeTrace:
    cascade_rung: int
    name: str
    outcome: Literal["miss", "skip", "retry", "satisfied", "degraded"]
    detail: str


@dataclass(slots=True)
class CascadeResult:
    cascade_rung: int
    status: Literal["satisfied", "degraded"]
    question: QuestionCreateIn | None
    needs_manual_authoring: bool
    trace: list[CascadeTrace] = field(default_factory=list)
    telemetry: list[AiCallTelemetry] = field(default_factory=list)
    pipeline_result: PipelineResult | None = None
    retrieval_candidate_ids: tuple[uuid.UUID, ...] = ()
    degrade_reason: str | None = None

    @property
    def ai_cost_usd(self):
        return sum((event.ai_cost_usd for event in self.telemetry), start=0)


class CostCascade:
    """Run one generation request through rungs 0..7 in policy order.

    Retrieval and persistence remain caller-owned.  The injected bank resolver
    loads P3-T1 candidate IDs through P2's existing question service; the cache
    and token gate can be replaced by Redis/ledger-backed implementations without
    adding a database dependency here.
    """

    def __init__(
        self,
        *,
        provider: LlmProvider,
        cache: ExactResultCache,
        bank_resolver: BankQuestionResolver,
        budget: TokenBudgetGate,
        rates: CostRates,
        similarity_checker: SimilarityChecker,
        escalation_thinking_tokens: int,
        validator: ValidationEngine | None = None,
        telemetry_sink=None,
        classification_threshold: float | None = None,
    ) -> None:
        if classification_threshold is None:
            classification_threshold = get_settings().CLASSIFICATION_FALLBACK_THRESHOLD
        if not 0 <= classification_threshold <= 1:
            raise ValueError("classification_threshold must be in [0, 1]")
        if escalation_thinking_tokens < 1:
            raise ValueError("escalation_thinking_tokens must be positive")
        self.provider = provider
        self.cache = cache
        self.bank_resolver = bank_resolver
        self.budget = budget
        self.rates = rates
        self.similarity_checker = similarity_checker
        self.escalation_thinking_tokens = escalation_thinking_tokens
        self.validator = validator or ValidationEngine()
        self.telemetry_sink = telemetry_sink
        self.classification_threshold = classification_threshold

    async def run(
        self,
        *,
        tenant_id: uuid.UUID,
        context: GenerationContext,
        prompts: PipelinePrompts | None = None,
        verification_failures: int = 0,
        scheduled_audit: bool = False,
    ) -> CascadeResult:
        if verification_failures < 0:
            raise ValueError("verification_failures cannot be negative")
        trace: list[CascadeTrace] = []
        telemetry: list[AiCallTelemetry] = []
        cache_key = self._cache_key(tenant_id, context, prompts)

        if scheduled_audit:
            # No scheduler is introduced in P3-T6.  This explicit entry point is
            # reserved for a future scheduling caller and is directly testable.
            for rung in range(0, 6):
                trace.append(CascadeTrace(rung, RUNG_NAMES[rung], "skip", "scheduled audit starts at escalation"))
            return await self._run_escalation(
                tenant_id=tenant_id,
                context=context,
                prompts=prompts,
                cache_key=cache_key,
                trace=trace,
                telemetry=telemetry,
                reason="scheduled_audit",
            )

        # Rung 0: exact request + prompt/model cache.
        cached = await self.cache.get(cache_key)
        if cached is not None:
            try:
                self.validator.request(cached.question, context)
            except PipelineValidationError as exc:
                trace.append(CascadeTrace(0, RUNG_NAMES[0], "miss", f"cached result failed {exc.check}"))
            else:
                trace.append(CascadeTrace(0, RUNG_NAMES[0], "satisfied", f"source_rung={cached.source_rung}"))
                return CascadeResult(0, "satisfied", cached.question, False, trace)
        else:
            trace.append(CascadeTrace(0, RUNG_NAMES[0], "miss", "no exact key"))

        # Rung 1: use P3-T1's ranked question_bank IDs; do not duplicate retrieval.
        bank_question = await self._bank_question(context)
        if bank_question is not None:
            await self.cache.put(cache_key, CachedQuestion(bank_question, 1))
            trace.append(CascadeTrace(1, RUNG_NAMES[1], "satisfied", "ranked question_bank candidate"))
            return CascadeResult(1, "satisfied", bank_question, False, trace)
        trace.append(CascadeTrace(1, RUNG_NAMES[1], "miss", "no resolvable in-scope candidate"))

        # Rung 2: P3-T3.5 template engine already ran in ContextBuilder.
        if context.template is not None:
            pipeline = GenerationPipeline(
                provider=self.provider,
                similarity_checker=self.similarity_checker,
                validator=self.validator,
                classification_threshold=self.classification_threshold,
            )
            result = await pipeline.run(context=context)
            await self.cache.put(cache_key, CachedQuestion(result.question, 2))
            trace.append(CascadeTrace(2, RUNG_NAMES[2], "satisfied", "validated P3-T3.5 template"))
            return CascadeResult(2, "satisfied", result.question, False, trace, pipeline_result=result)
        trace.append(CascadeTrace(2, RUNG_NAMES[2], "miss", "no eligible reviewed template"))

        # Rungs 3 and 4 are classification subtasks.  Generation has no candidate
        # to classify yet; after rung 5, GenerationPipeline runs local rules first
        # and invokes Flash only below CLASSIFICATION_FALLBACK_THRESHOLD.
        trace.append(CascadeTrace(3, RUNG_NAMES[3], "skip", "awaiting a generated classification subject"))
        trace.append(CascadeTrace(4, RUNG_NAMES[4], "skip", "only a low-confidence local residual may enter"))

        if prompts is None:
            raise ValueError("Gemini Pro/escalation rungs require caller-resolved prompts")

        # Rung 5: standard Pro.  A verification failure is retried once at this
        # rung so escalation cannot occur until the exact threshold of two.
        failures = verification_failures
        while failures < ESCALATION_VERIFICATION_FAILURES:
            try:
                result, _metered = await self._run_pipeline(
                    tenant_id=tenant_id,
                    context=context,
                    prompts=prompts,
                    reasoning_effort="standard",
                    telemetry=telemetry,
                )
                detail = "Gemini Pro generation"
                if result.question.meta.get("classification_source") == "local":
                    detail += "; classification satisfied at rung 3"
                else:
                    detail += "; low-confidence classification used rung 4"
                trace.append(CascadeTrace(5, RUNG_NAMES[5], "satisfied", detail))
                self._attach_telemetry(result.question, telemetry, 5)
                await self.cache.put(cache_key, CachedQuestion(result.question, 5))
                return CascadeResult(
                    5,
                    "satisfied",
                    result.question,
                    False,
                    trace,
                    list(telemetry),
                    result,
                )
            except PipelineValidationError as exc:
                failures += 1
                outcome = "retry" if failures < ESCALATION_VERIFICATION_FAILURES else "miss"
                trace.append(CascadeTrace(5, RUNG_NAMES[5], outcome, f"verification failure {failures}/2: {exc.check}"))
            except BudgetExhaustedError as exc:
                trace.append(CascadeTrace(5, RUNG_NAMES[5], "miss", f"budget gate: {exc}"))
                return self._degrade(context, trace, telemetry, "budget_exhausted")
            except LlmProviderError as exc:
                trace.append(CascadeTrace(5, RUNG_NAMES[5], "miss", f"provider failure: {exc.kind.value}"))
                return self._degrade(context, trace, telemetry, "provider_down")

        return await self._run_escalation(
            tenant_id=tenant_id,
            context=context,
            prompts=prompts,
            cache_key=cache_key,
            trace=trace,
            telemetry=telemetry,
            reason=f"verification_failures={failures}",
        )

    async def _run_escalation(
        self,
        *,
        tenant_id: uuid.UUID,
        context: GenerationContext,
        prompts: PipelinePrompts | None,
        cache_key: ExactCacheKey,
        trace: list[CascadeTrace],
        telemetry: list[AiCallTelemetry],
        reason: str,
    ) -> CascadeResult:
        if prompts is None:
            raise ValueError("escalation requires caller-resolved prompts")
        try:
            result, _metered = await self._run_pipeline(
                tenant_id=tenant_id,
                context=context,
                prompts=prompts,
                reasoning_effort="extended",
                telemetry=telemetry,
            )
        except BudgetExhaustedError as exc:
            trace.append(CascadeTrace(6, RUNG_NAMES[6], "miss", f"budget gate: {exc}"))
            return self._degrade(context, trace, telemetry, "budget_exhausted")
        except LlmProviderError as exc:
            trace.append(CascadeTrace(6, RUNG_NAMES[6], "miss", f"provider failure: {exc.kind.value}"))
            return self._degrade(context, trace, telemetry, "provider_down")
        except PipelineValidationError as exc:
            trace.append(CascadeTrace(6, RUNG_NAMES[6], "miss", f"verification failed: {exc.check}"))
            return self._degrade(context, trace, telemetry, "verification_failed")

        trace.append(CascadeTrace(6, RUNG_NAMES[6], "satisfied", f"extended reasoning: {reason}"))
        self._attach_telemetry(result.question, telemetry, 6)
        await self.cache.put(cache_key, CachedQuestion(result.question, 6))
        return CascadeResult(6, "satisfied", result.question, False, trace, list(telemetry), result)

    async def _run_pipeline(
        self,
        *,
        tenant_id: uuid.UUID,
        context: GenerationContext,
        prompts: PipelinePrompts,
        reasoning_effort: str,
        telemetry: list[AiCallTelemetry],
    ) -> tuple[PipelineResult, BudgetedProvider]:
        async def collect(event: AiCallTelemetry) -> None:
            telemetry.append(event)
            if self.telemetry_sink is not None:
                emitted = self.telemetry_sink(event)
                if inspect.isawaitable(emitted):
                    await emitted

        metered = BudgetedProvider(
            delegate=self.provider,
            tenant_id=tenant_id,
            budget=self.budget,
            rates=self.rates,
            flash_model=prompts.classification.model,
            pro_model=prompts.generation.model,
            escalation_thinking_tokens=self.escalation_thinking_tokens,
            sink=collect,
        )
        pipeline = GenerationPipeline(
            provider=metered,
            similarity_checker=self.similarity_checker,
            validator=self.validator,
            classification_threshold=self.classification_threshold,
        )
        result = await pipeline.run(
            context=context,
            prompts=prompts,
            reasoning_effort=reasoning_effort,
        )
        return result, metered

    async def _bank_question(self, context: GenerationContext) -> QuestionCreateIn | None:
        for candidate in context.retrieval.candidates:
            question = await self.bank_resolver(candidate)
            if question is None:
                continue
            try:
                self.validator.request(question, context)
            except PipelineValidationError:
                continue
            return question
        return None

    @staticmethod
    def _attach_telemetry(
        question: QuestionCreateIn,
        telemetry: list[AiCallTelemetry],
        resolved_rung: int,
    ) -> None:
        question.meta.update(
            cascade_rung=resolved_rung,
            ai_call_telemetry=[event.as_dict() for event in telemetry],
            ai_cost_usd=str(sum((event.ai_cost_usd for event in telemetry), start=0)),
        )

    @staticmethod
    def _degrade(
        context: GenerationContext,
        trace: list[CascadeTrace],
        telemetry: list[AiCallTelemetry],
        reason: str,
    ) -> CascadeResult:
        trace.append(CascadeTrace(7, RUNG_NAMES[7], "degraded", reason))
        return CascadeResult(
            7,
            "degraded",
            None,
            True,
            trace,
            list(telemetry),
            retrieval_candidate_ids=tuple(candidate.question_id for candidate in context.retrieval.candidates),
            degrade_reason=reason,
        )

    @staticmethod
    def _cache_key(
        tenant_id: uuid.UUID,
        context: GenerationContext,
        prompts: PipelinePrompts | None,
    ) -> ExactCacheKey:
        payload = {
            "slot": context.slot.model_dump(mode="json"),
            "retrieval": context.retrieval.model_dump(mode="json"),
            "curriculum": context.curriculum.model_dump(mode="json"),
            "language": context.language,
            "template": context.template.as_question_fields() if context.template is not None else None,
        }
        canonical = json.dumps(payload, sort_keys=True, separators=(",", ":"), ensure_ascii=False, default=str)
        content_hash = hashlib.sha256(canonical.encode("utf-8")).hexdigest()
        if prompts is None:
            return ExactCacheKey(tenant_id, content_hash, "software-only", "software-only")
        prompt_binding = (
            f"{prompts.generation.content_sha256}:{prompts.classification.content_sha256}"
        )
        prompt_hash = hashlib.sha256(prompt_binding.encode("ascii")).hexdigest()
        return ExactCacheKey(
            tenant_id,
            content_hash,
            prompt_hash,
            f"{prompts.generation.model}|{prompts.classification.model}",
        )


__all__ = [
    "BankQuestionResolver",
    "CachedQuestion",
    "CascadeResult",
    "CascadeTrace",
    "CostCascade",
    "ESCALATION_VERIFICATION_FAILURES",
    "ExactCacheKey",
    "ExactResultCache",
    "InMemoryExactResultCache",
    "RUNG_NAMES",
]
