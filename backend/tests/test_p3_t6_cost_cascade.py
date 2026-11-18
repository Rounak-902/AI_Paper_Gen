"""Executable P3-T6 cascade, budget, telemetry, and escalation evidence."""
from __future__ import annotations

import json
import uuid
from decimal import Decimal
from unittest.mock import AsyncMock

import pytest

from app.modules.ai.pipeline import (
    ContextBuilder,
    CostCascade,
    CostRates,
    CurriculumContext,
    InMemoryExactResultCache,
    InMemoryTokenBudgetGate,
    SimilarityResult,
    TokenPrice,
)
from app.modules.ai.pipeline.context import ModelSelector
from app.modules.ai.providers.errors import LlmProviderError, ProviderErrorKind, ProviderOperation
from app.modules.ai.providers.schemas import LlmResponse
from app.common.deps import RequestScope
from app.modules.questions import service as question_service
from app.modules.questions.pipeline_service import bank_question_resolver
from app.modules.questions.schemas import QuestionCreateIn
from app.modules.retrieval.schemas import BlueprintSlot, ScoredCandidate, SlotRetrievalResult
from test_p3_t4_ai_pipeline import classification, content, context, prompts


@pytest.fixture(autouse=True)
def deterministic_existing_verification_sandbox(monkeypatch):
    """Cascade tests do not re-test P3-T5 process startup under machine load."""
    monkeypatch.setattr(
        "app.modules.ai.pipeline.validation.compute",
        AsyncMock(return_value=5),
    )


async def no_similarity(_):
    return SimilarityResult()


async def no_bank(_):
    return None


def rates() -> CostRates:
    return CostRates(
        flash=TokenPrice(Decimal("1.00"), Decimal("2.00")),
        pro=TokenPrice(Decimal("3.00"), Decimal("4.00")),
        escalation=TokenPrice(Decimal("5.00"), Decimal("6.00")),
    )


class FakeProvider:
    id = "gemini"

    def __init__(self, generation=None, classifications=None):
        self.generation = list(generation or [])
        self.classifications = list(classifications or [])
        self.generate_requests = []
        self.classify_requests = []

    async def generate(self, request):
        self.generate_requests.append(request)
        value = self.generation.pop(0)
        if isinstance(value, BaseException):
            raise value
        return value

    async def classify(self, request):
        self.classify_requests.append(request)
        value = self.classifications.pop(0)
        if isinstance(value, BaseException):
            raise value
        return value

    async def embed(self, _):
        raise AssertionError("embedding is outside the cascade")


def llm_response(payload, *, tokens_in=10, tokens_out=20):
    return LlmResponse(
        content=json.dumps(payload),
        model="configured-test-model",
        provider="gemini",
        tokens_in=tokens_in,
        tokens_out=tokens_out,
        latency_ms=1,
        finish_reason="STOP",
    )


def cascade(provider, *, cache=None, budget=50_000, bank=no_bank):
    return CostCascade(
        provider=provider,
        cache=cache or InMemoryExactResultCache(),
        bank_resolver=bank,
        budget=InMemoryTokenBudgetGate(budget),
        rates=rates(),
        similarity_checker=no_similarity,
        escalation_thinking_tokens=4096,
    )


def bank_candidate(ctx):
    return ScoredCandidate(
        question_id=uuid.uuid4(),
        rank=1,
        pre_coverage_rank=1,
        score=1,
        metadata_score=1,
        fts_score=1,
        trigram_score=1,
        vector_score=0,
        coverage_priority=0,
        chapter_id=ctx.slot.chapter_ids[0],
        matched_chapter_ids=ctx.slot.chapter_ids,
        learning_outcome_ids=ctx.slot.learning_outcome_ids,
        qtype=ctx.slot.qtype,
        marks=ctx.slot.marks,
        difficulty=ctx.slot.difficulty,
        bloom=ctx.slot.bloom,
        source="institute_bank",
        scope="org",
        stem="What is 2 + 3?",
    )


def bank_question(ctx):
    return QuestionCreateIn(
        qtype="numerical",
        marks=ctx.slot.marks,
        difficulty=ctx.slot.difficulty,
        bloom=ctx.slot.bloom,
        language=ctx.language,
        source="institute_bank",
        scope="org",
        board_id=ctx.slot.board_id,
        class_id=ctx.slot.class_id,
        subject_id=ctx.slot.subject_id,
        chapter_id=ctx.slot.chapter_ids[0],
        learning_outcome_ids=ctx.slot.learning_outcome_ids,
        stem="What is 2 + 3?",
        answer={"value": 5, "units": "count", "tolerance": 0},
        rubric={"steps": ["Add 2 and 3."]},
    )


async def test_real_rung_0_1_2_traces_never_touch_provider():
    tenant_id = uuid.uuid4()

    # Rung 1 uses a real P3-T1-shaped ranked candidate and an injected P2 resolver.
    ctx1 = context()
    candidate = bank_candidate(ctx1)
    ctx1.retrieval.candidates = [candidate]
    provider1 = FakeProvider()

    async def resolve(received):
        assert received.question_id == candidate.question_id
        return bank_question(ctx1)

    result1 = await cascade(provider1, bank=resolve).run(tenant_id=tenant_id, context=ctx1)
    assert result1.cascade_rung == 1
    assert provider1.generate_requests == provider1.classify_requests == []

    # Rung 2 uses the already-shipped template branch.
    ctx2 = context(templates=["linear_evaluation/v1"])
    provider2 = FakeProvider()
    shared_cache = InMemoryExactResultCache()
    runner2 = cascade(provider2, cache=shared_cache)
    result2 = await runner2.run(tenant_id=tenant_id, context=ctx2)
    assert result2.cascade_rung == 2
    assert provider2.generate_requests == provider2.classify_requests == []

    # The identical request now resolves at rung 0 from the exact compound key.
    result0 = await runner2.run(tenant_id=tenant_id, context=ctx2)
    assert result0.cascade_rung == 0
    assert provider2.generate_requests == provider2.classify_requests == []

    evidence = {
        "rung_0": [item.__dict__ if hasattr(item, "__dict__") else {
            "cascade_rung": item.cascade_rung, "outcome": item.outcome, "detail": item.detail
        } for item in result0.trace],
        "rung_1": [{"cascade_rung": item.cascade_rung, "outcome": item.outcome} for item in result1.trace],
        "rung_2": [{"cascade_rung": item.cascade_rung, "outcome": item.outcome} for item in result2.trace],
        "gemini_calls": 0,
    }
    print("P3T6_SOFTWARE_RUNG_TRACES=" + json.dumps(evidence))


@pytest.mark.db
async def test_rung_1_uses_real_question_bank_authorized_read(
    db_session, tenant, curriculum
):
    scope = RequestScope(
        org_id=tenant.org_id,
        user_id=tenant.user_id,
        role="coaching_admin",
    )
    stored = await question_service.create_question(
        session=db_session,
        scope=scope,
        org_id=tenant.org_id,
        payload=QuestionCreateIn(
            qtype="numerical",
            marks="1",
            difficulty="easy",
            bloom="apply",
            language="en",
            source="institute_bank",
            board_id=curriculum.board_id,
            class_id=curriculum.class_id,
            subject_id=curriculum.subject_id,
            chapter_id=curriculum.chapter_id,
            stem="Calculate 2 + 3.",
            answer={"value": 5, "units": "count", "tolerance": 0},
            rubric={"steps": ["Add 2 and 3."]},
        ),
    )
    await db_session.commit()

    slot = BlueprintSlot(
        slot_id="real-bank-1",
        board_id=curriculum.board_id,
        class_id=curriculum.class_id,
        subject_id=curriculum.subject_id,
        curriculum_version_id=curriculum.curriculum_version_id,
        chapter_ids=[curriculum.chapter_id],
        qtype="numerical",
        marks="1",
        difficulty="easy",
        bloom="apply",
    )
    candidate = ScoredCandidate(
        question_id=stored.id,
        rank=1,
        pre_coverage_rank=1,
        score=1,
        metadata_score=1,
        fts_score=1,
        trigram_score=1,
        vector_score=0,
        coverage_priority=0,
        chapter_id=curriculum.chapter_id,
        matched_chapter_ids=[curriculum.chapter_id],
        learning_outcome_ids=[],
        qtype="numerical",
        marks=Decimal("1"),
        difficulty="easy",
        bloom="apply",
        source="institute_bank",
        scope="org",
        stem="Calculate 2 + 3.",
    )
    retrieval = SlotRetrievalResult(
        slot_id=slot.slot_id,
        candidates=[candidate],
        metadata_candidate_count=1,
        duplicate_candidates_removed=0,
        required_candidates=1,
        gap=False,
    )
    curriculum_context = CurriculumContext(
        board_id=curriculum.board_id,
        class_id=curriculum.class_id,
        subject_id=curriculum.subject_id,
        curriculum_version_id=curriculum.curriculum_version_id,
        chapter_ids=[curriculum.chapter_id],
        description="Real Numbers",
    )
    generation_context = ContextBuilder().build(
        slot=slot,
        retrieval=retrieval,
        curriculum=curriculum_context,
    )
    provider = FakeProvider()
    runner = cascade(
        provider,
        bank=bank_question_resolver(
            session=db_session,
            request_scope=scope,
            org_id=tenant.org_id,
        ),
    )
    result = await runner.run(tenant_id=tenant.org_id, context=generation_context)

    assert result.cascade_rung == 1
    assert result.question.meta["reused_question_id"] == str(stored.id)
    assert provider.generate_requests == provider.classify_requests == []
    print("P3T6_REAL_BANK_RUNG=" + json.dumps({
        "cascade_rung": result.cascade_rung,
        "question_id": str(stored.id),
        "gemini_calls": 0,
    }))


async def test_budget_exhausted_enters_rung_7_before_provider_call():
    provider = FakeProvider(generation=[AssertionError("budget gate was bypassed")])
    result = await cascade(provider, budget=1).run(
        tenant_id=uuid.uuid4(),
        context=context(),
        prompts=prompts(),
    )

    assert result.status == "degraded"
    assert result.cascade_rung == 7
    assert result.needs_manual_authoring is True
    assert result.question is None
    assert result.degrade_reason == "budget_exhausted"
    assert provider.generate_requests == provider.classify_requests == []
    print("P3T6_BUDGET_DEGRADE=" + json.dumps({
        "status": result.status,
        "cascade_rung": result.cascade_rung,
        "needs_manual_authoring": result.needs_manual_authoring,
        "provider_calls": 0,
        "http_500": False,
    }))


async def test_real_per_call_cost_and_rung_telemetry():
    ctx = context()
    provider = FakeProvider(
        generation=[llm_response(content(ctx))],
        classifications=[llm_response(classification(), tokens_in=5, tokens_out=7)],
    )
    result = await cascade(provider).run(
        tenant_id=uuid.uuid4(),
        context=ctx,
        prompts=prompts(),
    )

    assert result.cascade_rung == 5
    assert [(event.operation, event.cascade_rung) for event in result.telemetry] == [
        ("generate", 5),
        ("classify", 4),
    ]
    assert all(event.ai_cost_usd > 0 for event in result.telemetry)
    assert result.question.meta["cascade_rung"] == 5
    assert len(result.question.meta["ai_call_telemetry"]) == 2
    print("P3T6_AI_TELEMETRY=" + json.dumps([event.as_dict() for event in result.telemetry]))


async def test_local_classifier_uses_threshold_and_skips_flash():
    ctx = context()
    ctx.slot.difficulty = "medium"
    generated = content(ctx, stem="Calculate 2 + 3.")
    provider = FakeProvider(generation=[llm_response(generated)])
    result = await cascade(provider).run(
        tenant_id=uuid.uuid4(), context=ctx, prompts=prompts()
    )

    assert result.cascade_rung == 5
    assert result.question.meta["classification_source"] == "local"
    assert result.question.meta["classification_rule_confidence"] >= 0.7
    assert len(provider.generate_requests) == 1
    assert provider.classify_requests == []
    assert [(event.operation, event.cascade_rung) for event in result.telemetry] == [("generate", 5)]


def test_model_selector_distinguishes_flash_pro_and_escalation_by_task():
    assert ModelSelector.select_llm_tier("classify_question") == "flash"
    assert ModelSelector.select_llm_tier("generate_question") == "pro"
    assert ModelSelector.select_llm_tier(
        "generate_question", reasoning_effort="extended"
    ) == "pro_extended"


async def test_escalation_only_after_two_verification_failures():
    ctx = context()
    invalid = content(ctx, marks="2")
    valid = content(ctx)
    provider = FakeProvider(
        generation=[llm_response(invalid), llm_response(invalid), llm_response(valid)],
        classifications=[llm_response(classification())],
    )
    result = await cascade(provider).run(
        tenant_id=uuid.uuid4(), context=ctx, prompts=prompts()
    )

    assert result.cascade_rung == 6
    assert [request.reasoning_effort for request in provider.generate_requests] == [
        "standard",
        "standard",
        "extended",
    ]
    verification = [item for item in result.trace if item.cascade_rung == 5]
    assert [item.outcome for item in verification] == ["retry", "miss"]
    assert "verification_failures=2" in next(
        item.detail for item in result.trace if item.cascade_rung == 6
    )


async def test_provider_down_degrades_without_hard_failure():
    failure = LlmProviderError(
        provider="gemini",
        operation=ProviderOperation.GENERATE,
        model="configured-test-model",
        kind=ProviderErrorKind.UNAVAILABLE,
        retryable=False,
    )
    provider = FakeProvider(generation=[failure])
    result = await cascade(provider).run(
        tenant_id=uuid.uuid4(), context=context(), prompts=prompts()
    )
    assert result.cascade_rung == 7
    assert result.degrade_reason == "provider_down"
    assert result.needs_manual_authoring is True


async def test_scheduled_audit_contract_enters_extended_rung_directly():
    ctx = context()
    provider = FakeProvider(
        generation=[llm_response(content(ctx))],
        classifications=[llm_response(classification())],
    )
    result = await cascade(provider).run(
        tenant_id=uuid.uuid4(),
        context=ctx,
        prompts=prompts(),
        scheduled_audit=True,
    )
    assert result.cascade_rung == 6
    assert provider.generate_requests[0].reasoning_effort == "extended"
    assert all(item.outcome == "skip" for item in result.trace[:6])
