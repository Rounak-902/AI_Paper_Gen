"""P3-T7 golden-set metrics, telemetry reuse, and comparison support."""

from __future__ import annotations

import json
import uuid
from dataclasses import replace
from decimal import Decimal
from pathlib import Path

import pytest

from app.modules.ai.evaluation import (
    EvalAttempt,
    EvaluationVariant,
    OfflineEvaluator,
    ReplayCascadeRunner,
    load_curriculum_bank_golden_set,
    resolved_artifact_prompt,
)
from app.modules.ai.pipeline import CostRates, PipelinePrompts, TokenPrice
from app.modules.ai.pipeline.budget import AiCallTelemetry


def _rates() -> CostRates:
    return CostRates(
        flash=TokenPrice(Decimal("1"), Decimal("2")),
        pro=TokenPrice(Decimal("3"), Decimal("4")),
        escalation=TokenPrice(Decimal("5"), Decimal("6")),
    )


def _variant(name: str, generation_model: str = "test-model-generate-v1") -> EvaluationVariant:
    return EvaluationVariant(
        name=name,
        execution_mode="test",
        prompts=PipelinePrompts(
            generation=resolved_artifact_prompt(
                "generate_question/v1", model=generation_model
            ),
            classification=resolved_artifact_prompt(
                "classify_question/v1", model="test-model-classify-v1"
            ),
        ),
    )


def test_golden_set_reuses_complete_real_p2_bank():
    root = Path(__file__).resolve().parents[2]
    bank = root / "sample_import.json"
    curriculum = root / "seed_curriculum_cbse_class10_science.json"
    if not bank.exists() or not curriculum.exists():
        pytest.skip("External sample fixtures are intentionally not shipped")
    cases = load_curriculum_bank_golden_set(
        bank_path=bank,
        curriculum_path=curriculum,
    )
    assert len(cases) == 79
    assert len({case.chapter_name for case in cases}) == 13
    assert len({case.question.qtype for case in cases}) == 12
    assert len({case.benchmark_cell for case in cases}) == 72
    assert all(case.question.source == "textbook" for case in cases)
    assert "diagram_based" not in {case.question.qtype for case in cases}


def test_s4_corpus_is_a_query_layer_over_real_bank_targets():
    root = Path(__file__).resolve().parents[2]
    bank_path = root / "sample_import.json"
    if not bank_path.exists():
        pytest.skip("External sample fixtures are intentionally not shipped")
    bank = json.loads(bank_path.read_text(encoding="utf-8"))
    corpus = json.loads(
        (root / "spikes/retrieval_paraphrase/corpus.json").read_text(encoding="utf-8")
    )
    bank_stems = [row["stem"] for row in bank]
    targets = [pair["target_stem"] for pair in corpus["pairs"]]

    assert len(targets) == len(set(targets)) == 20
    assert all(bank_stems.count(target) == 1 for target in targets)
    assert all(
        pair["paraphrase"] != pair["target_stem"] for pair in corpus["pairs"]
    )
    assert corpus["metric"]["pgvector_adoption_trigger_gap"] == pytest.approx(0.20)
    assert "hand-authored" in corpus["construction"]


async def test_real_cascade_replay_emits_nonzero_p3_t6_cost_and_rung():
    case = load_curriculum_bank_golden_set()[0]
    attempt = await ReplayCascadeRunner(rates=_rates())(case, _variant("baseline"))

    assert attempt.passed_validation is True
    assert attempt.cascade_rung == 5
    assert attempt.ai_cost_usd > 0
    assert attempt.telemetry
    assert all(event.cascade_rung in (4, 5, 6) for event in attempt.telemetry)
    assert attempt.ai_cost_usd == sum(
        (event.ai_cost_usd for event in attempt.telemetry), Decimal("0")
    )


async def test_later_correctness_failure_does_not_erase_format_or_alignment_metrics():
    # This real bank MCQ has numeric-looking options with incompatible units. It
    # parses and aligns correctly, then the independent quantitative gate rejects
    # it. Section 20 metrics must keep those three facts separate.
    case = load_curriculum_bank_golden_set()[12]
    attempt = await ReplayCascadeRunner(rates=_rates())(case, _variant("baseline"))

    assert attempt.passed_validation is False
    assert attempt.format_compliant_first_attempt is True
    assert attempt.curriculum_aligned is True
    assert "quantitative_options_unverifiable" in (attempt.failure_reason or "")


async def test_cost_metric_aggregates_existing_telemetry_without_repricing():
    cases = load_curriculum_bank_golden_set()[:2]
    event = AiCallTelemetry(
        tenant_id=uuid.uuid4(),
        cascade_rung=5,
        operation="generate",
        model="telemetry-model",
        tokens_in=11,
        tokens_out=13,
        reserved_tokens=100,
        ai_cost_usd=Decimal("0.123456"),
        status="ok",
    )

    async def runner(case, _variant):
        return EvalAttempt(
            case_id=case.case_id,
            chapter_name=case.chapter_name,
            qtype=case.question.qtype,
            passed_validation=True,
            format_compliant_first_attempt=True,
            curriculum_aligned=True,
            latency_ms=2.0,
            cascade_rung=5,
            quality_score=1.0,
            telemetry=(event,),
        )

    report, _ = await OfflineEvaluator().evaluate(
        variant=_variant("telemetry"), cases=cases, runner=runner
    )
    assert report.total_cost_usd == Decimal("0.246912")
    assert report.average_cost_usd == Decimal("0.123456")
    assert report.average_tokens_in == 11
    assert report.average_tokens_out == 13
    assert report.telemetry_events == 2
    assert report.cost_by_cascade_rung_usd == {"5": "0.246912"}
    assert report.cost_by_model_task_usd == {
        "telemetry-model:generate": "0.246912"
    }
    assert report.cost_source.startswith("P3-T6 AiCallTelemetry.ai_cost_usd")


async def test_prompt_or_model_comparison_uses_identical_benchmark_cases():
    cases = load_curriculum_bank_golden_set()[:3]
    seen: dict[str, list[str]] = {"baseline": [], "candidate": []}

    async def runner(case, variant):
        seen[variant.name].append(case.case_id)
        candidate = variant.name == "candidate"
        return EvalAttempt(
            case_id=case.case_id,
            chapter_name=case.chapter_name,
            qtype=case.question.qtype,
            passed_validation=True,
            format_compliant_first_attempt=True,
            curriculum_aligned=True,
            latency_ms=1.0 if candidate else 2.0,
            cascade_rung=5,
            quality_score=1.0,
        )

    comparison = await OfflineEvaluator().compare(
        baseline=_variant("baseline", "baseline-model"),
        candidate=_variant("candidate", "candidate-model"),
        cases=cases,
        runner=runner,
    )
    assert comparison.same_benchmark_case_ids is True
    assert seen["baseline"] == seen["candidate"] == [case.case_id for case in cases]
    assert comparison.deltas["average_latency_ms"] == pytest.approx(-1.0)
    assert comparison.baseline.variant["generation_model"] == "baseline-model"
    assert comparison.candidate.variant["generation_model"] == "candidate-model"


async def test_comparison_retains_p3_t3_prompt_version_identity():
    cases = load_curriculum_bank_golden_set()[:1]
    baseline = _variant("baseline", "same-model")
    candidate_prompt = replace(
        baseline.prompts.generation,
        prompt_version_id=uuid.uuid4(),
        version=2,
        template_ref="generate_question/v2",
        content_sha256="b" * 64,
    )
    candidate = EvaluationVariant(
        name="candidate",
        execution_mode="test",
        prompts=PipelinePrompts(
            generation=candidate_prompt,
            classification=baseline.prompts.classification,
        ),
    )

    async def runner(case, _variant):
        return EvalAttempt(
            case_id=case.case_id,
            chapter_name=case.chapter_name,
            qtype=case.question.qtype,
            passed_validation=True,
            format_compliant_first_attempt=True,
            curriculum_aligned=True,
            latency_ms=1.0,
            cascade_rung=5,
            quality_score=1.0,
        )

    comparison = await OfflineEvaluator().compare(
        baseline=baseline,
        candidate=candidate,
        cases=cases,
        runner=runner,
    )
    assert comparison.same_benchmark_case_ids is True
    assert comparison.baseline.variant["generation_prompt_ref"] == "generate_question/v1"
    assert comparison.candidate.variant["generation_prompt_ref"] == "generate_question/v2"
    assert (
        comparison.baseline.variant["generation_prompt_version_id"]
        != comparison.candidate.variant["generation_prompt_version_id"]
    )
