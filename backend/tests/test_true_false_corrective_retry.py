"""True/false null-solution recovery, terminal rejection, and shared retry bound."""
import json
from dataclasses import asdict
from unittest.mock import AsyncMock

import pytest

from app.modules.ai.pipeline import GenerationPipeline, PipelineValidationError, RetryManager
from app.modules.ai.pipeline.service import TRUE_FALSE_SOLUTION_CORRECTION
from app.modules.ai.providers.errors import LlmProviderError
from app.modules.ai.providers.gemini import GeminiAdapter
from test_p3_t4_ai_pipeline import (classification, content, context, counts, failure,
                                  install_budget, no_similarity, prompts, response)


def boolean_content(ctx, **overrides):
    return content(ctx, **{"answer": {"value": True}, "solution_expression": "2+3 == 5", **overrides})


@pytest.mark.parametrize("field,omitted", [("solution_steps", False), ("solution_expression", False),
                                         ("solution_steps", True), ("solution_expression", True)])
async def test_true_false_null_or_missing_solution_corrected(monkeypatch, field, omitted):
    monkeypatch.setattr("app.modules.ai.pipeline.validation.compute", AsyncMock(return_value=True))
    ctx = context("true_false")
    first = boolean_content(ctx, **{field: None})
    if omitted:
        first.pop(field)
    replacement = boolean_content(ctx)
    spies = install_budget(monkeypatch, [response(first), response(replacement)], [response(classification())])
    sleep = AsyncMock()
    result = await GenerationPipeline(provider=object.__new__(GeminiAdapter), similarity_checker=no_similarity,
                                      retry=RetryManager(sleep=sleep)).run(context=ctx, prompts=prompts())
    first_request, second_request = [call.args[0] for call in spies[0].call_args_list]
    assert second_request.messages[:-1] == first_request.messages
    assert second_request.messages[-1].role == "user"
    assert second_request.messages[-1].content == TRUE_FALSE_SOLUTION_CORRECTION
    assert first_request.max_tokens == second_request.max_tokens
    assert first_request.response_schema == second_request.response_schema
    assert result.question.meta[field] == replacement[field]
    assert json.loads(result.responses[0].content) == first
    assert counts(spies) == {"generate": 2, "classify": 1, "embed": 0}
    assert result.question.meta["llm_calls"] == 3
    sleep.assert_awaited_once_with(0.5)
    assert result.retries[0].kind == "executable_solution_required"
    print("TF_CORRECTIVE_RETRY=" + json.dumps({"first": first, "reprompt": second_request.messages[-1].content,
        "replacement": replacement, "events": [asdict(e) for e in result.retries]}))


async def test_true_false_corrective_retry_exhaustion(monkeypatch):
    ctx = context("true_false")
    bad = response(boolean_content(ctx, solution_steps=None, solution_expression=None))
    spies = install_budget(monkeypatch, [bad] * 3, [])
    sleep = AsyncMock()
    with pytest.raises(PipelineValidationError) as caught:
        await GenerationPipeline(provider=object.__new__(GeminiAdapter), similarity_checker=no_similarity,
                                 retry=RetryManager(sleep=sleep)).run(context=ctx, prompts=prompts())
    assert caught.value.check == "executable_solution_required"
    assert counts(spies) == {"generate": 3, "classify": 0, "embed": 0}
    assert [c.args[0] for c in sleep.call_args_list] == [0.5, 1.0]
    requests = [c.args[0] for c in spies[0].call_args_list]
    assert requests[1] == requests[2]  # One note, always appended to original.


async def test_provider_and_validation_failures_share_three_attempt_bound(monkeypatch):
    ctx = context("true_false")
    bad = response(boolean_content(ctx, solution_expression=None))
    spies = install_budget(monkeypatch, [failure(), bad, bad], [])
    sleep = AsyncMock()
    with pytest.raises(PipelineValidationError):
        await GenerationPipeline(provider=object.__new__(GeminiAdapter), similarity_checker=no_similarity,
                                 retry=RetryManager(sleep=sleep)).run(context=ctx, prompts=prompts())
    assert counts(spies) == {"generate": 3, "classify": 0, "embed": 0}
    assert [c.args[0] for c in sleep.call_args_list] == [0.5, 1.0]


async def test_provider_failure_after_correction_preserves_existing_error_path(monkeypatch):
    ctx = context("true_false")
    terminal = failure(retryable=False)
    spies = install_budget(monkeypatch, [response(boolean_content(ctx, solution_expression=None)), terminal], [])
    with pytest.raises(LlmProviderError) as caught:
        await GenerationPipeline(provider=object.__new__(GeminiAdapter), similarity_checker=no_similarity,
                                 retry=RetryManager(sleep=AsyncMock())).run(context=ctx, prompts=prompts())
    assert caught.value is terminal
    assert counts(spies) == {"generate": 2, "classify": 0, "embed": 0}


@pytest.mark.parametrize("overrides,check", [
    ({"solution_steps": "", "solution_expression": "2+3 == 5"}, "executable_solution_required"),
    ({"solution_expression": ""}, "executable_solution_required"),
    ({"cited_snippet_indices": [99], "solution_expression": None}, "snippet_citations"),
    ({"marks": "2", "solution_expression": None}, "request_marks"),
    ({"solution_expression": "2+3 == 6"}, "executed_boolean_answer"),
    ({"solution_expression": "open('secret')"}, "solution_execution"),
])
async def test_true_false_other_validation_failures_are_not_retried(monkeypatch, overrides, check):
    ctx = context("true_false")
    spies = install_budget(monkeypatch, [response(boolean_content(ctx, **overrides))], [])
    with pytest.raises(PipelineValidationError) as caught:
        await GenerationPipeline(provider=object.__new__(GeminiAdapter), similarity_checker=no_similarity).run(
            context=ctx, prompts=prompts())
    assert caught.value.check == check
    assert counts(spies) == {"generate": 1, "classify": 0, "embed": 0}


async def test_corrected_response_is_fully_reverified_and_can_still_fail(monkeypatch):
    ctx = context("true_false")
    # The second response is complete but genuinely computes False, against its
    # claimed True answer. Real execution must reject it, not retry or classify.
    spies = install_budget(monkeypatch, [response(boolean_content(ctx, solution_expression=None)),
        response(boolean_content(ctx, solution_expression="2+3 == 6"))], [])
    with pytest.raises(PipelineValidationError) as caught:
        await GenerationPipeline(provider=object.__new__(GeminiAdapter), similarity_checker=no_similarity,
                                 retry=RetryManager(sleep=AsyncMock())).run(context=ctx, prompts=prompts())
    assert caught.value.check == "executed_boolean_answer"
    assert counts(spies) == {"generate": 2, "classify": 0, "embed": 0}


@pytest.mark.parametrize("qtype", ["numerical", "mcq", "fill_blank"])
async def test_other_types_keep_null_solution_hard_rejection(monkeypatch, qtype):
    ctx = context(qtype)
    data = content(ctx, solution_expression=None)
    if qtype == "mcq":
        data.update(options=[{"text": str(v), "is_correct": i == 0} for i, v in enumerate((5, 4, 6, 7))],
                    answer={"index": 0})
    elif qtype == "fill_blank":
        data.update(answer={"blanks": ["5"]})
    spies = install_budget(monkeypatch, [response(data)], [])
    with pytest.raises(PipelineValidationError) as caught:
        await GenerationPipeline(provider=object.__new__(GeminiAdapter), similarity_checker=no_similarity).run(
            context=ctx, prompts=prompts())
    assert caught.value.check == "executable_solution_required"
    assert counts(spies) == {"generate": 1, "classify": 0, "embed": 0}


async def test_true_false_null_units_alone_do_not_trigger_recovery(monkeypatch):
    monkeypatch.setattr("app.modules.ai.pipeline.validation.compute", AsyncMock(return_value=True))
    ctx = context("true_false")
    spies = install_budget(monkeypatch, [response(boolean_content(ctx, solution_units=None))], [response(classification())])
    result = await GenerationPipeline(provider=object.__new__(GeminiAdapter), similarity_checker=no_similarity).run(
        context=ctx, prompts=prompts())
    assert result.retries == [] and counts(spies)["generate"] == 1


@pytest.mark.parametrize("operation,callback", [("generate", None), ("classify", lambda _: True)])
async def test_validation_recovery_requires_generation_opt_in(operation, callback):
    error = PipelineValidationError("executable_solution_required")
    call = AsyncMock(side_effect=error)
    with pytest.raises(PipelineValidationError) as caught:
        await RetryManager().run(operation, call, [], correct_validation=callback)
    assert caught.value is error and call.await_count == 1
