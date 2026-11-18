"""P3-T2 contract tests for the provider interface and Gemini normalization."""

from __future__ import annotations

import asyncio
import math
from collections import deque
from typing import Any

import httpx
import pytest
from google.genai import errors as genai_errors
from google.genai import types

from app.core.config import Settings
from app.modules.ai.providers import (
    GeminiAdapter,
    LlmMessage,
    LlmProvider,
    LlmProviderError,
    LlmRequest,
    LlmResponse,
    ProviderErrorKind,
    ProviderOperation,
    create_llm_provider,
)


def settings(**overrides: Any) -> Settings:
    values: dict[str, Any] = {
        "DATABASE_URL": "postgresql+asyncpg://user:pass@localhost/papergen_test",
        "JWT_SECRET": "unit_test_access_secret_that_is_long_enough_123",
        "JWT_REFRESH_SECRET": "unit_test_refresh_secret_that_is_long_enough_456",
        "GEMINI_API_KEY": "unit_test_gemini_key_valid_shape_123456789",
        "AI_MODEL_GENERATE": "configured-generate-tier",
        "AI_MODEL_CLASSIFY": "configured-classify-tier",
        "AI_EMBEDDING_MODEL": "configured-embedding-tier",
        "AI_EMBEDDING_DIMENSIONS": 2,
        "ENVIRONMENT": "test",
    }
    values.update(overrides)
    return Settings(_env_file=None, **values)


def text_response(
    content: str = "provider response",
    *,
    model_version: str = "resolved-provider-version",
) -> types.GenerateContentResponse:
    return types.GenerateContentResponse(
        model_version=model_version,
        candidates=[
            types.Candidate(
                content=types.Content(
                    role="model", parts=[types.Part.from_text(text=content)]
                ),
                finish_reason=types.FinishReason.STOP,
            )
        ],
        usage_metadata=types.GenerateContentResponseUsageMetadata(
            prompt_token_count=11,
            candidates_token_count=7,
        ),
    )


class FakeAsyncModels:
    def __init__(
        self,
        *,
        generate: list[Any] | None = None,
        embed: list[Any] | None = None,
    ) -> None:
        self.generate_results = deque(generate or [])
        self.embed_results = deque(embed or [])
        self.generate_calls: list[dict[str, Any]] = []
        self.embed_calls: list[dict[str, Any]] = []

    async def generate_content(self, **kwargs: Any) -> Any:
        self.generate_calls.append(kwargs)
        result = self.generate_results.popleft()
        if isinstance(result, BaseException):
            raise result
        return result

    async def embed_content(self, **kwargs: Any) -> Any:
        self.embed_calls.append(kwargs)
        result = self.embed_results.popleft()
        if isinstance(result, BaseException):
            raise result
        return result


class FakeAio:
    def __init__(self, models: FakeAsyncModels) -> None:
        self.models = models
        self.closed = False

    async def aclose(self) -> None:
        self.closed = True


class FakeClient:
    def __init__(self, models: FakeAsyncModels) -> None:
        self.aio = FakeAio(models)


def request(*, structured: bool = False) -> LlmRequest:
    return LlmRequest(
        messages=[
            LlmMessage(role="system", content="Follow the curriculum."),
            LlmMessage(role="user", content="Classify this question."),
        ],
        response_schema=(
            {
                "type": "object",
                "properties": {"label": {"type": "string"}},
                "required": ["label"],
            }
            if structured
            else None
        ),
    )


@pytest.mark.asyncio
async def test_contract_is_exactly_generate_classify_embed() -> None:
    assert set(LlmProvider.__abstractmethods__) == {"generate", "classify", "embed"}

    class FutureProvider(LlmProvider):
        id = "future"

        async def generate(self, request: LlmRequest) -> LlmResponse:
            raise NotImplementedError

        async def classify(self, request: LlmRequest) -> LlmResponse:
            raise NotImplementedError

        async def embed(self, texts: list[str]) -> list[list[float]]:
            return [[1.0] for _ in texts]

    assert await FutureProvider().embed(["one", "two"]) == [[1.0], [1.0]]


@pytest.mark.asyncio
async def test_generate_routes_to_configured_generation_model() -> None:
    models = FakeAsyncModels(generate=[text_response()])
    adapter = GeminiAdapter(settings=settings(), client=FakeClient(models))

    response = await adapter.generate(request())

    call = models.generate_calls[0]
    assert call["model"] == "configured-generate-tier"
    assert response.model == "configured-generate-tier"
    assert response.model_version == "resolved-provider-version"
    assert response.provider == "gemini"
    assert response.tokens_in == 11
    assert response.tokens_out == 7
    assert response.cost_estimate is None
    assert call["config"].system_instruction == "Follow the curriculum."
    assert call["config"].temperature == 0.3
    assert call["config"].max_output_tokens == 2048
    assert len(call["config"].safety_settings) == 4
    assert call["config"].automatic_function_calling.disable is True
    assert {setting.threshold for setting in call["config"].safety_settings} == {
        types.HarmBlockThreshold.BLOCK_MEDIUM_AND_ABOVE
    }
    assert call["config"].thinking_config is None


@pytest.mark.asyncio
async def test_extended_reasoning_is_explicit_and_uses_configured_budget() -> None:
    upstream = text_response()
    upstream.usage_metadata.thoughts_token_count = 13
    models = FakeAsyncModels(generate=[upstream])
    adapter = GeminiAdapter(
        settings=settings(AI_ESCALATION_THINKING_BUDGET=3210),
        client=FakeClient(models),
    )

    extended = request().model_copy(update={"reasoning_effort": "extended"})
    response = await adapter.generate(extended)

    thinking = models.generate_calls[0]["config"].thinking_config
    assert thinking.thinking_budget == 3210
    assert thinking.include_thoughts is False
    assert response.tokens_out == 20  # 7 visible candidate + 13 hidden thoughts


@pytest.mark.asyncio
async def test_classify_routes_to_configured_flash_model_and_schema() -> None:
    models = FakeAsyncModels(generate=[text_response('{"label":"physics"}')])
    adapter = GeminiAdapter(settings=settings(), client=FakeClient(models))

    response = await adapter.classify(request(structured=True))

    call = models.generate_calls[0]
    assert call["model"] == "configured-classify-tier"
    assert response.model == "configured-classify-tier"
    assert call["config"].response_mime_type == "application/json"
    assert call["config"].response_schema is None
    assert call["config"].response_json_schema["required"] == ["label"]


@pytest.mark.asyncio
async def test_model_switch_is_configuration_only() -> None:
    first_models = FakeAsyncModels(generate=[text_response()])
    second_models = FakeAsyncModels(generate=[text_response()])

    first = GeminiAdapter(settings=settings(), client=FakeClient(first_models))
    second = GeminiAdapter(
        settings=settings(AI_MODEL_GENERATE="switched-generate-tier"),
        client=FakeClient(second_models),
    )

    await first.generate(request())
    switched = await second.generate(request())

    assert first_models.generate_calls[0]["model"] == "configured-generate-tier"
    assert second_models.generate_calls[0]["model"] == "switched-generate-tier"
    assert switched.model == "switched-generate-tier"


@pytest.mark.asyncio
async def test_embed_uses_configured_model_width_and_renormalizes() -> None:
    models = FakeAsyncModels(
        embed=[
            types.EmbedContentResponse(
                embeddings=[types.ContentEmbedding(values=[3.0, 4.0, 99.0])]
            )
        ]
    )
    adapter = GeminiAdapter(settings=settings(), client=FakeClient(models))

    vectors = await adapter.embed(["Newton's second law"])

    call = models.embed_calls[0]
    assert call["model"] == "configured-embedding-tier"
    assert call["config"].output_dimensionality == 2
    assert vectors[0] == pytest.approx([0.6, 0.8])
    assert math.sqrt(sum(value * value for value in vectors[0])) == pytest.approx(1.0)


@pytest.mark.asyncio
async def test_real_sdk_error_shape_is_normalized_to_typed_eh3_error() -> None:
    sdk_error = genai_errors.ClientError(
        429,
        {
            "error": {
                "status": "RESOURCE_EXHAUSTED",
                "message": "The account quota is exhausted.",
                "details": [
                    {
                        "@type": "type.googleapis.com/google.rpc.RetryInfo",
                        "retryDelay": "9s",
                    }
                ],
            }
        },
    )
    models = FakeAsyncModels(generate=[sdk_error])
    adapter = GeminiAdapter(settings=settings(), client=FakeClient(models))

    with pytest.raises(LlmProviderError) as exc_info:
        await adapter.generate(request())

    error = exc_info.value
    assert error.kind == ProviderErrorKind.QUOTA_EXCEEDED
    assert error.operation == ProviderOperation.GENERATE
    assert error.retryable is True
    assert error.upstream_status_code == 429
    assert error.retry_after_seconds == 9.0
    assert error.status_code == 502
    assert error.eh_ref == "EH-3"
    assert isinstance(error.__cause__, genai_errors.ClientError)


@pytest.mark.asyncio
async def test_network_error_is_normalized_and_remains_retryable() -> None:
    models = FakeAsyncModels(generate=[httpx.ConnectError("network unavailable")])
    adapter = GeminiAdapter(settings=settings(), client=FakeClient(models))

    with pytest.raises(LlmProviderError) as exc_info:
        await adapter.classify(request())

    assert exc_info.value.kind == ProviderErrorKind.NETWORK
    assert exc_info.value.operation == ProviderOperation.CLASSIFY
    assert exc_info.value.retryable is True
    assert isinstance(exc_info.value.__cause__, httpx.ConnectError)


@pytest.mark.asyncio
async def test_adapter_timeout_is_normalized_without_retrying() -> None:
    class SlowModels(FakeAsyncModels):
        async def generate_content(self, **kwargs: Any) -> Any:
            self.generate_calls.append(kwargs)
            await asyncio.sleep(0.05)
            return text_response()

    models = SlowModels()
    adapter = GeminiAdapter(
        settings=settings(AI_GENERATION_TIMEOUT_MS=1), client=FakeClient(models)
    )

    with pytest.raises(LlmProviderError) as exc_info:
        await adapter.generate(request())

    assert exc_info.value.kind == ProviderErrorKind.TIMEOUT
    assert exc_info.value.retryable is True
    assert len(models.generate_calls) == 1


@pytest.mark.asyncio
async def test_safety_block_is_a_non_retryable_response_outcome() -> None:
    blocked = types.GenerateContentResponse(
        candidates=[
            types.Candidate(
                finish_reason=types.FinishReason.SAFETY,
                safety_ratings=[
                    types.SafetyRating(
                        category=types.HarmCategory.HARM_CATEGORY_DANGEROUS_CONTENT,
                        blocked=True,
                    )
                ],
            )
        ]
    )
    models = FakeAsyncModels(generate=[blocked])
    adapter = GeminiAdapter(settings=settings(), client=FakeClient(models))

    response = await adapter.generate(request())

    assert response.content == ""
    assert response.safety_blocked is True
    assert response.finish_reason == "SAFETY"
    assert response.safety_categories == ["HARM_CATEGORY_DANGEROUS_CONTENT"]


@pytest.mark.asyncio
async def test_empty_non_safety_response_is_typed_invalid_response() -> None:
    models = FakeAsyncModels(generate=[types.GenerateContentResponse()])
    adapter = GeminiAdapter(settings=settings(), client=FakeClient(models))

    with pytest.raises(LlmProviderError) as exc_info:
        await adapter.generate(request())

    assert exc_info.value.kind == ProviderErrorKind.INVALID_RESPONSE
    assert exc_info.value.retryable is True
    assert exc_info.value.eh_ref == "EH-3"


def test_factory_rejects_the_explicitly_deferred_second_provider() -> None:
    with pytest.raises(ValueError, match="not implemented"):
        create_llm_provider(settings(AI_PROVIDER="future-provider"))


def test_request_rejects_provider_model_and_unknown_fields() -> None:
    with pytest.raises(ValueError):
        LlmRequest.model_validate(
            {
                "messages": [{"role": "user", "content": "hello"}],
                "model": "caller-selected-provider-model",
            }
        )
