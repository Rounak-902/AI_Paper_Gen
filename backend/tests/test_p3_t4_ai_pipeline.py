"""Branch budgets, typed EH-3 failures, isolated execution and P2 dedup reuse."""
from __future__ import annotations

import json
import uuid
from dataclasses import asdict
from pathlib import Path
from unittest.mock import AsyncMock

import pytest
from sqlalchemy import func, select

from app.modules.ai.pipeline import (ContextBuilder, CurriculumContext, GenerationPipeline,
                                    PipelinePrompts, PipelineValidationError, RetryManager, SimilarityResult)
from app.modules.ai.pipeline.parser import ResponseParser
from app.modules.ai.pipeline.schemas import Classification, GeneratedContent
from app.modules.ai.pipeline.service import render_request
from app.modules.ai.pipeline.validation import compute
from app.modules.ai.providers.errors import LlmProviderError, ProviderErrorKind, ProviderOperation
from app.modules.ai.providers.gemini import GeminiAdapter
from app.modules.ai.providers.schemas import LlmResponse
from app.modules.prompts.service import ResolvedPrompt
from app.modules.questions.schemas import QuestionCreateIn
from app.modules.retrieval.schemas import BlueprintSlot, SlotRetrievalResult
from packages.prompts import load_prompt


def inputs(qtype="numerical", templates=()):
    ids = {name: uuid.uuid4() for name in ("board_id", "class_id", "subject_id", "curriculum_version_id")}
    chapter = uuid.uuid4()
    slot = BlueprintSlot(**ids, slot_id="a1", chapter_ids=[chapter], qtype=qtype,
                         marks="2.0" if templates and qtype == "numerical" else "1.0", difficulty="easy", bloom="apply")
    curriculum = CurriculumContext(**ids, chapter_ids=[chapter], description="Class 10 arithmetic.",
                                   textbook_snippets=["Addition combines quantities: 2+3=5."], template_refs=list(templates))
    retrieval = SlotRetrievalResult(slot_id="a1", candidates=[], metadata_candidate_count=0,
                                   duplicate_candidates_removed=0, required_candidates=1, gap=True)
    return slot, curriculum, retrieval


def context(qtype="numerical", templates=()):
    slot, curriculum, retrieval = inputs(qtype, templates)
    return ContextBuilder().build(slot=slot, curriculum=curriculum, retrieval=retrieval, seed=11)


def prompts():
    def resolved(task):
        artifact = load_prompt(task + "/v1")
        return ResolvedPrompt(uuid.uuid4(), task, 1, "configured-test-model", artifact.template_ref,
                              artifact.layers, artifact.raw_text, artifact.content_sha256)
    return PipelinePrompts(resolved("generate_question"), resolved("classify_question"))


def content(ctx, **overrides):
    slot = ctx.slot
    data = dict(qtype=slot.qtype, marks=str(slot.marks), difficulty=slot.difficulty, bloom=slot.bloom,
                language="en", stem="What is 2 + 3?", answer={"value": 5, "units": "count", "tolerance": 0},
                rubric={"steps": ["2 + 3 = 5"]}, chapter_id=str(slot.chapter_ids[0]),
                learning_outcome_ids=[], cited_snippet_indices=[0], solution_steps="Add 2 and 3.",
                solution_expression="2+3", solution_units="count",
                **{name: str(getattr(slot, name)) for name in ("board_id", "class_id", "subject_id")})
    if slot.qtype == "short_answer":
        data.update(answer={"text": "Addition combines quantities."}, rubric={"key_points": ["combining quantities"]},
                    solution_steps=None, solution_expression=None, solution_units=None)
    data.update(overrides)
    return data


def classification(**overrides):
    data = dict(difficulty="easy", bloom="apply", confidence=0.95, curriculum_aligned=True,
                language_matches=True, grammar_flags=[], format_flags=[],
                qualitative_correctness_confidence=0.95, evidence="The question directly tests addition.")
    data.update(overrides)
    return data


def response(data):
    return LlmResponse(content=json.dumps(data), model="configured-test-model", provider="gemini",
                       tokens_in=10, tokens_out=20, latency_ms=1, finish_reason="STOP")


def failure(operation=ProviderOperation.GENERATE, retryable=True, **kwargs):
    return LlmProviderError(provider="gemini", operation=operation, model="configured-test-model",
                            kind=ProviderErrorKind.UNAVAILABLE, retryable=retryable, **kwargs)


def install_budget(monkeypatch, generates, classifications):
    # P3-T3.5's exact AsyncMock(side_effect=AssertionError) raising-spy pattern.
    generate = AsyncMock(side_effect=list(generates) + [AssertionError("unexpected generate")] if generates else AssertionError("unexpected generate"))
    classify = AsyncMock(side_effect=list(classifications) + [AssertionError("unexpected classify")] if classifications else AssertionError("unexpected classify"))
    embed = AsyncMock(side_effect=AssertionError("unexpected embed"))
    for name, spy in (("generate", generate), ("classify", classify), ("embed", embed)):
        monkeypatch.setattr(GeminiAdapter, name, spy)
    return generate, classify, embed


def counts(spies):
    return dict(zip(("generate", "classify", "embed"), (s.await_count for s in spies)))


async def no_similarity(_):
    return SimilarityResult()


async def test_template_branch_raising_gemini_tripwire(monkeypatch):
    spies = install_budget(monkeypatch, [], [])
    ctx = context(templates=["linear_evaluation/v1"])
    result = await GenerationPipeline(provider=object.__new__(GeminiAdapter), similarity_checker=no_similarity).run(context=ctx)
    assert result.branch == "template" and result.question.prompt_version_id is None
    assert counts(spies) == {"generate": 0, "classify": 0, "embed": 0}
    print("P3T4_TEMPLATE_TRIPWIRE=" + json.dumps(counts(spies)))


async def test_fallback_branch_operation_budget(monkeypatch):
    ctx = context()
    spies = install_budget(monkeypatch, [response(content(ctx))], [response(classification())])
    result = await GenerationPipeline(provider=object.__new__(GeminiAdapter), similarity_checker=no_similarity).run(context=ctx, prompts=prompts())
    assert result.branch == "llm" and result.question.source == "ai_generated"
    assert counts(spies) == {"generate": 1, "classify": 1, "embed": 0}
    print("P3T4_FALLBACK_TRIPWIRE=" + json.dumps(counts(spies)))


async def test_generation_and_classification_use_deployment_max_tokens(monkeypatch):
    monkeypatch.setattr("app.modules.ai.pipeline.service.get_settings",
                        lambda: type("Configured", (), {"AI_MAX_TOKENS": 2048})())
    ctx = context()
    spies = install_budget(monkeypatch, [response(content(ctx))], [response(classification())])
    await GenerationPipeline(provider=object.__new__(GeminiAdapter), similarity_checker=no_similarity).run(
        context=ctx, prompts=prompts())
    generation_request = spies[0].call_args.args[0]
    classification_request = spies[1].call_args.args[0]
    assert generation_request.max_tokens == classification_request.max_tokens == 2048
    assert generation_request.max_tokens != 4096


async def test_classifier_retry_does_not_replay_generation(monkeypatch):
    ctx = context()
    spies = install_budget(monkeypatch, [response(content(ctx))],
                           [failure(ProviderOperation.CLASSIFY), response(classification())])
    delays = []
    async def sleep(delay): delays.append(delay)
    result = await GenerationPipeline(provider=object.__new__(GeminiAdapter), similarity_checker=no_similarity,
                                      retry=RetryManager(sleep=sleep)).run(context=ctx, prompts=prompts())
    assert counts(spies) == {"generate": 1, "classify": 2, "embed": 0} and delays == [0.5]
    print("P3T4_CLASSIFY_RETRY=" + json.dumps({"calls": counts(spies), "delays": delays,
                                               "events": [asdict(e) for e in result.retries]}))


async def test_retry_exhaustion_is_three_attempts_and_original_error(monkeypatch):
    ctx = context()
    error = failure()
    spies = install_budget(monkeypatch, [error] * 3, [])
    delays, events = [], []
    async def sleep(delay): delays.append(delay)
    with pytest.raises(LlmProviderError) as caught:
        await RetryManager(sleep=sleep).run("generate", lambda: GeminiAdapter.generate(None, None), events)
    assert caught.value is error and delays == [0.5, 1.0]
    assert counts(spies) == {"generate": 3, "classify": 0, "embed": 0}
    print("P3T4_RETRY_EXHAUSTED=" + json.dumps({"calls": counts(spies), "delays": delays,
                                              "events": [asdict(e) for e in events]}))


@pytest.mark.parametrize("advice,retryable", [(None, False), (31, True), (float("inf"), True)])
async def test_retry_terminal_conditions(advice, retryable):
    error = failure(retryable=retryable, retry_after_seconds=advice)
    call = AsyncMock(side_effect=error)
    with pytest.raises(LlmProviderError): await RetryManager().run("generate", call, [])
    assert call.await_count == 1


async def test_retry_after_is_respected():
    call = AsyncMock(side_effect=[failure(retry_after_seconds=2), "ok"])
    sleep = AsyncMock()
    assert await RetryManager(sleep=sleep).run("generate", call, []) == "ok"
    sleep.assert_awaited_once_with(2)


@pytest.mark.parametrize("raw", ['{}', '{"a":1,"a":2}', '```json\n{}\n```', '{"confidence":NaN}'])
def test_parser_real_eh3_type(raw):
    out = response({}).model_copy(update={"content": raw})
    with pytest.raises(LlmProviderError) as caught: ResponseParser.parse(out, Classification, ProviderOperation.CLASSIFY)
    assert caught.value.eh_ref == "EH-3" and caught.value.status_code == 502


async def test_invalid_response_retry_is_not_a_double_call(monkeypatch):
    ctx = context()
    spies = install_budget(monkeypatch, [response({}), response(content(ctx))], [response(classification())])
    result = await GenerationPipeline(provider=object.__new__(GeminiAdapter), similarity_checker=no_similarity,
                                      retry=RetryManager(sleep=AsyncMock())).run(context=ctx, prompts=prompts())
    assert counts(spies) == {"generate": 2, "classify": 1, "embed": 0}
    assert result.retries[0].kind == "invalid_response"


@pytest.mark.parametrize("overrides,check", [({"marks": "2"}, "request_marks"),
    ({"chapter_id": str(uuid.uuid4())}, "curriculum_chapter"), ({"answer": {"value": 6, "units": "count"}}, "executed_answer"),
    ({"solution_units": "cm"}, "solution_units"), ({"cited_snippet_indices": [99]}, "snippet_citations"),
    ({"solution_expression": None}, "executable_solution_required")])
async def test_generated_claims_rejected_before_classification(monkeypatch, overrides, check):
    ctx = context()
    spies = install_budget(monkeypatch, [response(content(ctx, **overrides))], [])
    with pytest.raises(PipelineValidationError) as caught:
        await GenerationPipeline(provider=object.__new__(GeminiAdapter), similarity_checker=no_similarity).run(context=ctx, prompts=prompts())
    assert caught.value.check == check and counts(spies)["classify"] == 0


async def test_qualitative_correctness_never_blocks(monkeypatch):
    ctx = context("short_answer")
    spies = install_budget(monkeypatch, [response(content(ctx))],
                           [response(classification(qualitative_correctness_confidence=0.0, grammar_flags=["Review wording."]))])
    result = await GenerationPipeline(provider=object.__new__(GeminiAdapter), similarity_checker=no_similarity).run(context=ctx, prompts=prompts())
    assert "qualitative_correctness_review" in result.flags and "correctness_advisory_only" in result.flags


@pytest.mark.parametrize("expression", ["__import__('os').system('whoami')", "open('secret')", "(1).__class__", "2**99999", "[1]*999999", "1/0"])
async def test_sandbox_rejects_executable_or_unbounded_text(expression):
    with pytest.raises(PipelineValidationError): await compute(expression)


async def test_sandbox_sympy_and_boolean_execution():
    assert await compute("Rational(1,3)*9") == 3
    assert await compute("sqrt(144)") == 12
    assert await compute("2+3 == 5") is True


async def test_sandbox_timeout_kills_and_reaps_real_process(monkeypatch, tmp_path):
    import asyncio
    import sys
    worker = tmp_path / "sleep_worker.py"
    worker.write_text("import time\nprint('READY', flush=True)\ntime.sleep(30)\n")
    original = asyncio.create_subprocess_exec
    processes = []
    async def launch(*args, **kwargs):
        process = await original(sys.executable, "-I", str(worker), **kwargs)
        processes.append(process)
        return process
    monkeypatch.setattr(asyncio, "create_subprocess_exec", launch)
    with pytest.raises(TimeoutError): await compute("1+1", timeout_ms=50)
    assert processes[0].returncode is not None
    print("P3T4_SANDBOX_TIMEOUT=" + json.dumps({"deadline_ms": 50, "reaped": True, "returncode": processes[0].returncode}))


@pytest.mark.parametrize("expression,options,answer,check", [
    ("2+3", ["5", "5.0", "6", "7"], 0, "mcq_unique_numeric_answer"),
    ("2+3", ["4", "5", "6", "7"], 0, "executed_answer"),
])
async def test_numeric_mcq_independent_key_gate(expression, options, answer, check):
    from app.modules.ai.pipeline import ValidationEngine
    ctx = context("mcq")
    data = content(ctx, options=[{"text": text, "is_correct": i == answer} for i, text in enumerate(options)],
                   answer={"index": answer}, solution_expression=expression)
    with pytest.raises(PipelineValidationError) as caught:
        await ValidationEngine().generated(GeneratedContent.model_validate(data), ctx)
    assert caught.value.check == check


async def test_boolean_claim_is_computed():
    from app.modules.ai.pipeline import ValidationEngine
    ctx = context("true_false")
    data = content(ctx, answer={"value": False}, solution_expression="2+3 == 5")
    with pytest.raises(PipelineValidationError) as caught:
        await ValidationEngine().generated(GeneratedContent.model_validate(data), ctx)
    assert caught.value.check == "executed_boolean_answer"


async def test_numeric_options_with_units_cannot_bypass_execution():
    from app.modules.ai.pipeline import ValidationEngine
    ctx = context("mcq")
    data = content(ctx, options=[{"text": str(v) + " cm²", "is_correct": i == 0}
                                for i, v in enumerate((5, 4, 6, 7))], answer={"index": 0},
                   solution_units="cm^2", solution_expression="2+3")
    assert await ValidationEngine().generated(GeneratedContent.model_validate(data), ctx) == []
    data["answer"] = {"index": 1}
    data["options"][0]["is_correct"] = False
    data["options"][1]["is_correct"] = True
    with pytest.raises(PipelineValidationError) as caught:
        await ValidationEngine().generated(GeneratedContent.model_validate(data), ctx)
    assert caught.value.check == "executed_answer"


@pytest.mark.parametrize("snippet_count", [0, 1, 3])
async def test_mcq_prompt_supplies_explicit_citation_lists(monkeypatch, snippet_count):
    monkeypatch.setattr("app.modules.ai.pipeline.validation.compute", AsyncMock(return_value=5))
    ctx = context("mcq")
    ctx.curriculum.textbook_snippets = ["Addition combines quantities."] * snippet_count
    spies = install_budget(monkeypatch, [response(content(ctx, options=[
        {"text": str(v), "is_correct": i == 0} for i, v in enumerate((5, 4, 6, 7))],
        answer={"index": 0}))], [response(classification())])
    # Capture the real production request before validation (zero snippets
    # deliberately cannot ground this fixture).
    try:
        await GenerationPipeline(provider=object.__new__(GeminiAdapter), similarity_checker=no_similarity).run(
            context=ctx, prompts=prompts())
    except PipelineValidationError as exc:
        assert snippet_count == 0 and exc.check == "snippet_citations"
    request = spies[0].call_args.args[0]
    constraints = json.loads(request.messages[-1].content.split("<QUESTION_CONSTRAINTS>")[1].split(
        "</QUESTION_CONSTRAINTS>")[0])
    assert constraints["citation_context"] == {
        "valid_textbook_snippet_indices": list(range(snippet_count)), "valid_retrieved_question_ids": []}
    assert "selected only from that explicit index list" in request.messages[0].content


@pytest.mark.parametrize("solution,expected", [
    (r"1. Add the values.2. Calculate 2 \\times 3.",
     ["solution_format_run_on", "solution_format_latex_escape"]),
    ("Add the values.Then calculate the sum.", ["solution_format_run_on"]),
    ("Compute 2 " + "\t" + "imes 3.", ["solution_format_latex_escape"]),
    ("Compute " + "\f" + "rac{6}{2}.", ["solution_format_latex_escape"]),
    (r"Compute 2 \times 3.", ["solution_format_latex_escape"]),
    ("1. Add the values.\n2. Calculate \\(2 \\times 3\\).", []),
    (r"Compute $2 \times 3$ or $$\frac{6}{2}$$.", []),
    ("The decimal is 3.14. Then add the values.", []),
])
async def test_mcq_solution_formatting_is_review_only(monkeypatch, solution, expected):
    from app.modules.ai.pipeline import ValidationEngine
    monkeypatch.setattr("app.modules.ai.pipeline.validation.compute", AsyncMock(return_value=5))
    ctx = context("mcq")
    question = GeneratedContent.model_validate(content(ctx, options=[
        {"text": str(v), "is_correct": i == 0} for i, v in enumerate((5, 4, 6, 7))],
        answer={"index": 0}, solution_steps=solution))
    flags = await ValidationEngine().generated(question, ctx)
    assert flags == expected
    assert question.solution_steps == solution


async def test_captured_mcq_solution_produces_explicit_review_flags(monkeypatch):
    from app.modules.ai.pipeline import ValidationEngine
    monkeypatch.setattr("app.modules.ai.pipeline.validation.compute", AsyncMock(return_value=5))
    evidence = Path(__file__).parents[2] / "docs/docs/evidence/p3-t4/resume-2026-09-13T104233Z-23d5a0f4/mcq.txt"
    raw = next(line.split("=", 1)[1] for line in evidence.read_text(encoding="utf-8").splitlines()
               if line.startswith("P3T4_RAW_API_GENERATE="))
    solution = json.loads(json.loads(raw)["content"])["solution_steps"]
    ctx = context("mcq")
    question = GeneratedContent.model_validate(content(ctx, options=[
        {"text": str(v), "is_correct": i == 0} for i, v in enumerate((5, 4, 6, 7))],
        answer={"index": 0}, solution_steps=solution))
    flags = await ValidationEngine().generated(question, ctx)
    assert flags == ["solution_format_run_on", "solution_format_latex_escape"]
    print("P3T4_MCQ_OFFLINE_FORMAT=" + json.dumps({"solution_steps": solution, "flags": flags,
        "hard_rejection": False}, ensure_ascii=False))


async def test_other_question_types_keep_existing_solution_format_policy():
    from app.modules.ai.pipeline import ValidationEngine
    ctx = context()
    question = GeneratedContent.model_validate(content(ctx, solution_steps=r"Add the values.2. Use \\times."))
    assert await ValidationEngine().generated(question, ctx) == []


async def test_mcq_solution_review_flags_are_persisted_without_retries(monkeypatch):
    monkeypatch.setattr("app.modules.ai.pipeline.validation.compute", AsyncMock(return_value=5))
    ctx = context("mcq")
    spies = install_budget(monkeypatch, [response(content(ctx, options=[
        {"text": str(v), "is_correct": i == 0} for i, v in enumerate((5, 4, 6, 7))],
        answer={"index": 0}, solution_steps=r"1. Add values.2. Use \times."))], [response(classification())])
    result = await GenerationPipeline(provider=object.__new__(GeminiAdapter), similarity_checker=no_similarity).run(
        context=ctx, prompts=prompts())
    assert result.flags == ["solution_format_run_on", "solution_format_latex_escape"]
    assert result.question.meta["validation_flags"] == result.flags
    assert result.retries == []
    assert counts(spies) == {"generate": 1, "classify": 1, "embed": 0}
    classification_request = spies[1].call_args.args[0]
    constraints = json.loads(classification_request.messages[-1].content.split("<QUESTION_CONSTRAINTS>")[1].split(
        "</QUESTION_CONSTRAINTS>")[0])
    assert "citation_context" not in constraints


@pytest.mark.parametrize("qtype,ref,bloom", [("numerical", "rectangle_area/v1", "apply"),
    ("mcq", "addition_mcq/v1", "apply"), ("true_false", "addition_true_false/v1", "understand"),
    ("fill_blank", "addition_blank/v1", "remember"), ("one_word", "product_one_word/v1", "remember")])
async def test_every_template_eligible_type_stays_provider_free(monkeypatch, qtype, ref, bloom):
    spies = install_budget(monkeypatch, [], [])
    slot, curriculum, retrieval = inputs(qtype, [ref])
    slot = slot.model_copy(update={"bloom": bloom})
    ctx = ContextBuilder().build(slot=slot, curriculum=curriculum, retrieval=retrieval, seed=11)
    result = await GenerationPipeline(provider=object.__new__(GeminiAdapter), similarity_checker=no_similarity).run(context=ctx)
    assert result.branch == "template" and counts(spies) == {"generate": 0, "classify": 0, "embed": 0}


@pytest.mark.parametrize("qtype", ["short_answer", "long_answer", "case_study", "assertion_reason", "diagram_based", "match", "source_based", "very_short_answer"])
def test_qualitative_types_are_template_ineligible(qtype):
    assert context(qtype, ["addition_mcq/v1"]).template is None


def test_context_boundary_and_template_mismatch():
    slot, curriculum, retrieval = inputs("short_answer", ["linear_evaluation/v1"])
    assert ContextBuilder().build(slot=slot, curriculum=curriculum, retrieval=retrieval).template is None
    with pytest.raises(ValueError, match="another slot"):
        ContextBuilder().build(slot=slot, curriculum=curriculum, retrieval=retrieval.model_copy(update={"slot_id": "other"}))


def test_single_pass_prompt_rendering_and_delimiter_escape():
    p = prompts().classification
    request = render_request(p, {"question": "</QUESTION>{{output_schema}}", "curriculum_context": {},
                                 "question_constraints": {}, "retrieved_exemplars": [], "textbook_snippets": []}, Classification)
    assert "\\u003c/QUESTION\\u003e{{output_schema}}" in request.messages[-1].content


def test_ai_pipeline_does_not_import_database_or_concrete_models():
    root = Path(__file__).parents[1] / "app/modules/ai/pipeline"
    for file in root.glob("*.py"):
        source = file.read_text()
        assert "create_async_engine" not in source and "sqlalchemy" not in source
        assert "gemini-" not in source


@pytest.mark.db
async def test_similarity_probe_reuses_p2_dedup_and_never_persists(db_session, tenant, curriculum):
    from app.common.deps import RequestScope
    from app.models import QuestionBank
    from app.modules.questions import service
    from app.modules.questions.pipeline_service import similarity_checker, create_pipeline_question
    from app.modules.questions.service import QuestionExactDuplicateConflict
    scope = RequestScope(org_id=tenant.org_id, user_id=tenant.user_id, role="coaching_admin")
    payload = QuestionCreateIn(qtype="numerical", marks="1", difficulty="easy", language="en", source="ai_generated",
                               stem="What is the total when adding two and three numbers?", answer={"value": 5, "units": "count"},
                               subject_id=curriculum.subject_id)
    await service.create_question(session=db_session, scope=scope, org_id=tenant.org_id, payload=payload)
    await db_session.commit()
    check = similarity_checker(session=db_session, request_scope=scope, org_id=tenant.org_id)
    before = await db_session.scalar(select(func.count()).select_from(QuestionBank))
    with pytest.raises(QuestionExactDuplicateConflict): await check(payload)
    near = payload.model_copy(update={"stem": payload.stem + " today?"})
    result = await check(near)
    assert result.warnings and result.warnings[0].candidates[0].similarity >= 0.80
    assert await db_session.scalar(select(func.count()).select_from(QuestionBank)) == before
    print("P3T4_REAL_DEDUP=" + json.dumps({"before": before, "after_probe": before,
                                          "warning": result.warnings[0].model_dump(mode="json")}))
