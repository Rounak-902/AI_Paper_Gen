"""Deterministic CI runner through the real P3-T6 cascade.

This adapter exists so pull requests can exercise the complete evaluator without a
network secret or a nondeterministic provider.  Production/offline model studies pass
their own provider-backed case runner to :class:`OfflineEvaluator`; both paths consume
the same ``ResolvedPrompt``/``PipelinePrompts`` types from P3-T3.
"""

from __future__ import annotations

import json
import math
import time
import uuid
from typing import Any

from app.modules.ai.pipeline import (
    CostCascade,
    CostRates,
    InMemoryExactResultCache,
    InMemoryTokenBudgetGate,
    PipelinePrompts,
    SimilarityResult,
)
from app.modules.ai.providers.base import LlmProvider
from app.modules.ai.providers.schemas import LlmRequest, LlmResponse
from app.modules.prompts.service import ResolvedPrompt
from packages.prompts import load_prompt

from .benchmark import GoldenCase
from .harness import EvalAttempt, EvaluationVariant


_PROMPT_ID_NAMESPACE = uuid.UUID("43513e45-ec4d-4b1d-b78a-31d8b599b787")
_TENANT_ID = uuid.UUID("d67f5ec6-e8aa-41b7-a665-794009a00b19")


def resolved_artifact_prompt(template_ref: str, *, model: str) -> ResolvedPrompt:
    """Adapt a reviewed artifact to P3-T3's resolved-prompt contract for CI replay.

    Live comparisons should obtain this same type from ``PromptResolver``, which reads
    the existing ``prompt_versions`` table.  The deterministic UUID here identifies
    an artifact-only CI binding and is never persisted or promoted.
    """

    artifact = load_prompt(template_ref)
    prompt_id = uuid.uuid5(
        _PROMPT_ID_NAMESPACE,
        f"artifact-replay:{artifact.template_ref}:{artifact.content_sha256}:{model}",
    )
    return ResolvedPrompt(
        prompt_version_id=prompt_id,
        task=artifact.task,
        version=artifact.version,
        model=model,
        template_ref=artifact.template_ref,
        layers=artifact.layers,
        source_text=artifact.raw_text,
        content_sha256=artifact.content_sha256,
    )


class _GoldenReplayProvider(LlmProvider):
    id = "golden-replay"

    def __init__(self, *, case: GoldenCase, prompts: PipelinePrompts) -> None:
        self.case = case
        self.prompts = prompts

    async def generate(self, request: LlmRequest) -> LlmResponse:
        payload = _generated_payload(self.case)
        content = json.dumps(payload, ensure_ascii=False, separators=(",", ":"))
        return _response(
            request=request,
            content=content,
            model=self.prompts.generation.model,
        )

    async def classify(self, request: LlmRequest) -> LlmResponse:
        question = self.case.question
        payload = {
            "difficulty": question.difficulty,
            "bloom": question.bloom or "understand",
            "confidence": 1.0,
            "curriculum_aligned": True,
            "language_matches": True,
            "grammar_flags": [],
            "format_flags": [],
            "qualitative_correctness_confidence": 1.0,
            "evidence": "Golden replay is bound to the P2 curriculum-bank row.",
        }
        return _response(
            request=request,
            content=json.dumps(payload, separators=(",", ":")),
            model=self.prompts.classification.model,
        )

    async def embed(self, texts: list[str]) -> list[list[float]]:
        raise AssertionError("ADR-007 keeps embeddings disabled in P3-T7 evaluation")


class ReplayCascadeRunner:
    """Run bank-backed expected outputs through CostCascade and its telemetry."""

    def __init__(
        self,
        *,
        rates: CostRates,
        token_budget: int = 1_000_000,
        escalation_thinking_tokens: int = 4096,
    ) -> None:
        self.rates = rates
        self.token_budget = token_budget
        self.escalation_thinking_tokens = escalation_thinking_tokens

    async def __call__(
        self, case: GoldenCase, variant: EvaluationVariant
    ) -> EvalAttempt:
        async def no_bank(_):
            return None

        async def no_similarity(_):
            return SimilarityResult()

        provider = _GoldenReplayProvider(case=case, prompts=variant.prompts)
        cascade = CostCascade(
            provider=provider,
            cache=InMemoryExactResultCache(),
            bank_resolver=no_bank,
            budget=InMemoryTokenBudgetGate(self.token_budget),
            rates=self.rates,
            similarity_checker=no_similarity,
            escalation_thinking_tokens=self.escalation_thinking_tokens,
            # CI replay evaluates generation/validation. Classification behavior
            # already has dedicated P3-T6 coverage; accepting all local-confidence
            # levels avoids turning this benchmark into a second classifier suite.
            classification_threshold=0.0,
        )
        started = time.perf_counter()
        result = await cascade.run(
            tenant_id=_TENANT_ID,
            context=case.generation_context(),
            prompts=variant.prompts,
        )
        elapsed_ms = (time.perf_counter() - started) * 1000
        return EvalAttempt.from_cascade(
            case=case,
            result=result,
            latency_ms=elapsed_ms,
            # The replay provider emits json.dumps of a schema-derived payload
            # whose IDs come from this exact case. Preserve those observable
            # facts even when a later answer-correctness gate makes the cascade
            # discard its partial PipelineResult.
            format_compliant_first_attempt=True,
            curriculum_aligned=True,
        )


def _response(*, request: LlmRequest, content: str, model: str) -> LlmResponse:
    # A replay has no provider tokenizer. These counts are deterministic replay
    # usage facts; critically, pricing remains exclusively in BudgetedProvider,
    # which emits the ai_cost_usd/cascade_rung events consumed by the evaluator.
    request_text = " ".join(message.content for message in request.messages)
    tokens_in = max(1, math.ceil(len(request_text.encode("utf-8")) / 4))
    tokens_out = max(1, math.ceil(len(content.encode("utf-8")) / 4))
    return LlmResponse(
        content=content,
        model=model,
        model_version=f"{model}-offline-replay",
        provider="golden-replay",
        tokens_in=tokens_in,
        tokens_out=tokens_out,
        latency_ms=1,
        finish_reason="STOP",
    )


def _generated_payload(case: GoldenCase) -> dict[str, Any]:
    question = case.question
    payload = question.model_dump(mode="json")
    payload.update(
        cited_question_ids=[str(case.reference_question_id)],
        cited_snippet_indices=[],
        solution_steps=None,
        solution_expression=None,
        solution_units=None,
    )
    if question.qtype == "numerical":
        value = question.answer["value"]
        payload.update(
            solution_steps="Use the reviewed P2 bank answer and verify the final value.",
            solution_expression=repr(value),
            solution_units=question.answer["units"],
        )
    elif question.qtype == "true_false":
        value = bool(question.answer["value"])
        payload.update(
            solution_steps="Evaluate the reviewed proposition as true or false.",
            # The shipped sandbox intentionally accepts comparisons, not bare
            # boolean literals, as its closed-language boolean form.
            solution_expression="1 == 1" if value else "1 == 0",
        )
    return payload


__all__ = ["ReplayCascadeRunner", "resolved_artifact_prompt"]
