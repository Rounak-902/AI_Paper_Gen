"""Eight-stage AI orchestration without database imports or connections."""
from __future__ import annotations

import json
import re
from typing import Awaitable, Callable

from app.core.config import get_settings
from app.modules.ai.providers.base import LlmProvider
from app.modules.ai.providers.errors import ProviderOperation
from app.modules.ai.providers.schemas import LlmMessage, LlmRequest
from app.modules.prompts.service import ResolvedPrompt
from app.modules.questions.schemas import QuestionCreateIn
from .classification import DeterministicClassifier
from .context import ModelSelector
from .parser import ResponseParser
from .retry import RetryManager
from .schemas import (Classification, GeneratedContent, GenerationContext, PipelinePrompts,
                      PipelineResult, SimilarityResult)
from .validation import ValidationEngine

STAGES = ["context_builder", "model_selector", "adapter", "response_parser",
          "validation_engine", "similarity_checker", "classifiers", "retry_manager"]

TRUE_FALSE_SOLUTION_CORRECTION = (
    "Corrective retry: your previous true/false response omitted or set to null solution_steps or solution_expression. "
    "Return a complete replacement JSON object for the original request. "
    "Provide non-null solution_steps explaining the statement and a non-null solution_expression containing one pure "
    "executable numeric comparison that evaluates to answer.value. "
    "Use only the supplied curriculum evidence, preserve all original request constraints and valid citations, "
    "and follow the original allowed expression language. "
    "Do not substitute an assertion of correctness for executable evidence."
)


def render_request(
    prompt: ResolvedPrompt,
    values: dict,
    shape: type,
    response_schema: dict | None = None,
    *,
    reasoning_effort: str = "standard",
) -> LlmRequest:
    """Render once, never recursively substitute placeholder-like runtime data.

    Developer is a logical prompt layer, folded into system because P3-T2's
    neutral role contract has no developer role. Runtime data stays in user.
    """
    pattern = re.compile(r"\{\{([a-z_]+)\}\}")
    schema = response_schema or shape.model_json_schema()
    values = {"teacher_instructions": {}, **values, "output_schema": schema}
    def replace(match):
        name = match.group(1)
        if name not in values:
            raise ValueError(f"missing prompt binding {name}")
        # Escape angle brackets in data to prevent forged closing delimiters.
        return json.dumps(values[name], ensure_ascii=False, default=str).replace("<", "\\u003c").replace(">", "\\u003e")
    user = pattern.sub(replace, prompt.layers.user)
    return LlmRequest(messages=[LlmMessage(role="system", content=prompt.layers.system + "\n\n" + prompt.layers.developer),
                                LlmMessage(role="user", content=user)], response_schema=schema,
                      max_tokens=get_settings().AI_MAX_TOKENS,
                      reasoning_effort=reasoning_effort)


def generation_schema(qtype: str) -> dict:
    """Narrow the generic bank envelope to the requested answer/options shape.

    The existing bank schema validates again on parsing; JSON-schema guidance
    prevents the provider from confusing true/false with an MCQ index envelope.
    """
    schema = GeneratedContent.model_json_schema()
    def obj(properties, required):
        return {"type": "object", "properties": properties, "required": required, "additionalProperties": False}
    strings = {"type": "array", "items": {"type": "string"}}
    props = schema["properties"]
    props["qtype"] = {"type": "string", "enum": [qtype]}
    if qtype == "mcq":
        props["options"] = {"type": "array", "minItems": 4, "maxItems": 4,
                            "items": obj({"text": {"type": "string"}, "is_correct": {"type": "boolean"}}, ["text", "is_correct"])}
        props["answer"] = obj({"index": {"type": "integer", "minimum": 0, "maximum": 3}}, ["index"])
    elif qtype == "true_false":
        props["options"] = {"type": "null"}
        props["answer"] = obj({"value": {"type": "boolean"}}, ["value"])
    elif qtype == "numerical":
        props["answer"] = obj({"value": {"type": "number"}, "units": {"type": "string"},
                               "tolerance": {"type": "number", "minimum": 0, "maximum": 0.01}}, ["value", "units", "tolerance"])
        props["rubric"] = obj({"steps": strings}, ["steps"])
    elif qtype == "fill_blank":
        props["answer"] = obj({"blanks": strings}, ["blanks"])
    elif qtype == "one_word":
        props["answer"] = obj({"value": {"type": "string"}, "synonyms": strings}, ["value"])
    elif qtype in ("short_answer", "long_answer", "very_short_answer", "diagram_based"):
        props["answer"] = obj({"text": {"type": "string"}}, ["text"])
        if qtype == "short_answer": props["rubric"] = obj({"key_points": strings}, ["key_points"])
        if qtype == "long_answer":
            props["rubric"] = obj({"marking_scheme": {"type": "array", "items": obj(
                {"point": {"type": "string"}, "marks": {"type": "number", "exclusiveMinimum": 0}}, ["point", "marks"])}}, ["marking_scheme"])
    schema["required"] = list(dict.fromkeys(schema["required"] + ["answer"]))
    return schema


class GenerationPipeline:
    def __init__(self, *, provider: LlmProvider,
                 similarity_checker: Callable[[QuestionCreateIn], Awaitable[SimilarityResult]],
                 retry: RetryManager | None = None, validator: ValidationEngine | None = None,
                 local_classifier: DeterministicClassifier | None = None,
                 classification_threshold: float | None = None):
        self.provider = provider
        self.similarity_checker = similarity_checker
        self.retry = retry or RetryManager()
        self.validator = validator or ValidationEngine()
        self.local_classifier = local_classifier or DeterministicClassifier()
        self.classification_threshold = classification_threshold

    async def run(
        self,
        *,
        context: GenerationContext,
        prompts: PipelinePrompts | None = None,
        reasoning_effort: str = "standard",
    ) -> PipelineResult:
        branch = ModelSelector.select(context)
        slot = context.slot
        if branch == "template":
            data = context.template.as_question_fields()
            data.update(board_id=slot.board_id, class_id=slot.class_id, subject_id=slot.subject_id,
                        chapter_id=slot.chapter_ids[0], learning_outcome_ids=slot.learning_outcome_ids,
                        prompt_version_id=None)
            question = QuestionCreateIn.model_validate(data)
            self.validator.request(question, context)
            similarity = await self.similarity_checker(question)
            return PipelineResult("template", question, similarity=similarity,
                                  stages=["context_builder", "model_selector", "validation_engine", "similarity_checker"])
        if prompts is None or prompts.generation.task != "generate_question" or prompts.classification.task != "classify_question":
            raise ValueError("LLM branch requires caller-resolved pipeline prompts")
        generation_tier = ModelSelector.select_llm_tier(
            prompts.generation.task,
            reasoning_effort=reasoning_effort,
        )
        if generation_tier not in ("pro", "pro_extended"):
            raise AssertionError("generation must use a Pro tier")
        values = {"curriculum_context": context.curriculum.model_dump(mode="json", exclude={"template_refs", "textbook_snippets"}),
                  "learning_outcomes": slot.learning_outcome_ids,
                  "retrieved_exemplars": [c.model_dump(mode="json") for c in context.retrieval.candidates],
                  "textbook_snippets": context.curriculum.textbook_snippets,
                  "teacher_instructions": {"topic": slot.topic, "keywords": slot.keywords},
                  "question_constraints": {**slot.model_dump(mode="json", exclude={"topic", "keywords"}),
                                           "language": context.language}}
        generation_values = values
        if slot.qtype == "mcq":
            generation_values = {**values, "question_constraints": {**values["question_constraints"],
                "citation_context": {
                    "valid_textbook_snippet_indices": list(range(len(context.curriculum.textbook_snippets))),
                    "valid_retrieved_question_ids": [str(c.question_id) for c in context.retrieval.candidates],
                }}}
        request = render_request(
            prompts.generation,
            generation_values,
            GeneratedContent,
            generation_schema(slot.qtype),
            reasoning_effort=reasoning_effort,
        )
        responses, retries = [], []
        async def generate():
            response = await self.provider.generate(request)
            responses.append(response)
            if response.model != prompts.generation.model:
                raise ValueError("generation response does not match resolved prompt model")
            return ResponseParser.parse(response, GeneratedContent, ProviderOperation.GENERATE)
        if slot.qtype == "true_false":
            original_request = request
            candidate = None

            async def generate_verified():
                nonlocal candidate
                candidate = await generate()
                return candidate, await self.validator.generated(candidate, context)

            def correct_validation(error):
                nonlocal request
                if candidate is None or not (candidate.solution_steps is None or candidate.solution_expression is None):
                    return False
                request = original_request.model_copy(update={"messages": [*original_request.messages,
                    LlmMessage(role="user", content=TRUE_FALSE_SOLUTION_CORRECTION)]})
                return True

            generated, flags = await self.retry.run("generate", generate_verified, retries,
                                                    correct_validation=correct_validation)
        else:
            generated = await self.retry.run("generate", generate, retries)
            flags = await self.validator.generated(generated, context)
        question_data = generated.model_dump(exclude={"cited_question_ids", "cited_snippet_indices", "solution_steps",
                                                     "solution_expression", "solution_units"})
        # Source and provenance are pipeline-owned, never trusted model claims.
        question_data.update(source="ai_generated", scope=None, competency_ids=[],
                             prompt_version_id=prompts.generation.prompt_version_id,
                             meta={"curriculum_version_id": str(slot.curriculum_version_id),
                                   "cited_question_ids": [str(i) for i in generated.cited_question_ids],
                                   "cited_snippet_indices": generated.cited_snippet_indices,
                                   "solution_steps": generated.solution_steps,
                                   "solution_expression": generated.solution_expression,
                                   "solution_units": generated.solution_units,
                                   "generation_prompt_sha256": prompts.generation.content_sha256})
        question = QuestionCreateIn.model_validate(question_data)
        similarity = await self.similarity_checker(question)
        local = self.local_classifier.classify(generated, context)
        threshold = (
            self.classification_threshold
            if self.classification_threshold is not None
            else getattr(get_settings(), "CLASSIFICATION_FALLBACK_THRESHOLD", 0.7)
        )
        classification_source = "local"
        if local.confidence >= threshold:
            classification = local.classification
        else:
            if ModelSelector.select_llm_tier(prompts.classification.task) != "flash":
                raise AssertionError("classification residuals must use the Flash tier")
            classify_request = render_request(
                prompts.classification,
                {"question": generated.model_dump(mode="json"), **values},
                Classification,
            )

            async def classify():
                response = await self.provider.classify(classify_request)
                responses.append(response)
                if response.model != prompts.classification.model:
                    raise ValueError("classification response does not match resolved prompt model")
                return ResponseParser.parse(response, Classification, ProviderOperation.CLASSIFY)

            classification = await self.retry.run("classify", classify, retries)
            classification_source = "gemini_flash"
        flags += self.validator.classified(classification, context)
        question.meta.update(classification=classification.model_dump(), validation_flags=flags,
                             classification_source=classification_source,
                             classification_rule_confidence=local.confidence)
        if classification_source == "gemini_flash":
            question.meta.update(
                classification_prompt_version_id=str(prompts.classification.prompt_version_id),
                classification_prompt_sha256=prompts.classification.content_sha256,
            )
        question.meta.update(provider_responses=[r.model_dump(exclude={"content"}) for r in responses],
                             retry_events=[{"operation": e.operation, "failed_attempt": e.failed_attempt,
                                            "kind": e.kind, "delay_seconds": e.delay_seconds} for e in retries],
                             llm_calls=(len(responses)
                                        + sum(e.kind != "executable_solution_required" for e in retries)))
        return PipelineResult("llm", question, flags, similarity, classification, responses, retries, list(STAGES))
