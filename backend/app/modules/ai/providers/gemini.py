"""Gemini implementation of the provider-neutral P3-T2 contract."""

from __future__ import annotations

import asyncio
import math
import time
from collections.abc import Iterable
from typing import Any, ClassVar

import httpx
from google import genai
from google.genai import errors as genai_errors
from google.genai import types

from app.core.config import Settings
from app.modules.ai.providers.base import LlmProvider
from app.modules.ai.providers.errors import (
    LlmProviderError,
    ProviderErrorKind,
    ProviderOperation,
)
from app.modules.ai.providers.schemas import LlmRequest, LlmResponse


_SAFETY_CATEGORIES: tuple[types.HarmCategory, ...] = (
    types.HarmCategory.HARM_CATEGORY_HARASSMENT,
    types.HarmCategory.HARM_CATEGORY_HATE_SPEECH,
    types.HarmCategory.HARM_CATEGORY_SEXUALLY_EXPLICIT,
    types.HarmCategory.HARM_CATEGORY_DANGEROUS_CONTENT,
)

_SAFETY_THRESHOLDS: dict[str, types.HarmBlockThreshold] = {
    "block_low_and_above": types.HarmBlockThreshold.BLOCK_LOW_AND_ABOVE,
    "block_medium_and_above": types.HarmBlockThreshold.BLOCK_MEDIUM_AND_ABOVE,
    "block_only_high": types.HarmBlockThreshold.BLOCK_ONLY_HIGH,
    "block_none": types.HarmBlockThreshold.BLOCK_NONE,
    "off": types.HarmBlockThreshold.OFF,
}

_SAFETY_FINISH_REASONS = {
    "SAFETY",
    "BLOCKLIST",
    "PROHIBITED_CONTENT",
    "SPII",
    "IMAGE_SAFETY",
    "IMAGE_PROHIBITED_CONTENT",
}


def _enum_value(value: Any, *, fallback: str) -> str:
    if value is None:
        return fallback
    return str(getattr(value, "value", value))


def _retry_after_seconds(exc: genai_errors.APIError) -> float | None:
    response = getattr(exc, "response", None)
    headers = getattr(response, "headers", None)
    if headers:
        raw = headers.get("retry-after")
        if raw is not None:
            try:
                return max(0.0, float(raw))
            except (TypeError, ValueError):
                # An HTTP-date can be scheduled by P3-T4; do not guess here.
                pass

    # Gemini commonly sends google.rpc.RetryInfo in the JSON body instead of an
    # HTTP Retry-After header.  Preserve the numeric delay for the later scheduler.
    payload = getattr(exc, "details", None)
    if isinstance(payload, dict):
        error = payload.get("error", payload)
        if isinstance(error, dict):
            for detail in error.get("details", []):
                if not isinstance(detail, dict):
                    continue
                delay = detail.get("retryDelay")
                if isinstance(delay, str) and delay.endswith("s"):
                    try:
                        return max(0.0, float(delay[:-1]))
                    except ValueError:
                        pass
    return None


def _api_error_kind(exc: genai_errors.APIError) -> tuple[ProviderErrorKind, bool]:
    code = int(getattr(exc, "code", 0) or 0)
    status_name = str(getattr(exc, "status", "") or "").upper()
    message = str(getattr(exc, "message", "") or "").casefold()

    if code in (408, 504) or status_name in {"DEADLINE_EXCEEDED", "TIMEOUT"}:
        return ProviderErrorKind.TIMEOUT, True
    if code == 401 or status_name == "UNAUTHENTICATED":
        return ProviderErrorKind.AUTHENTICATION, False
    if code == 403 or status_name == "PERMISSION_DENIED":
        return ProviderErrorKind.PERMISSION_DENIED, False
    if code == 404 or status_name == "NOT_FOUND":
        return ProviderErrorKind.MODEL_NOT_FOUND, False
    if code == 429 or status_name == "RESOURCE_EXHAUSTED":
        if "quota" in message:
            return ProviderErrorKind.QUOTA_EXCEEDED, True
        return ProviderErrorKind.RATE_LIMITED, True
    if code in (400, 409, 422) or status_name in {
        "INVALID_ARGUMENT",
        "FAILED_PRECONDITION",
    }:
        return ProviderErrorKind.INVALID_REQUEST, False
    if code >= 500 or status_name in {"UNAVAILABLE", "INTERNAL"}:
        return ProviderErrorKind.UNAVAILABLE, True
    return ProviderErrorKind.UNKNOWN, False


class GeminiAdapter(LlmProvider):
    """Real async Gemini adapter with configuration-owned tier routing."""

    id: ClassVar[str] = "gemini"

    def __init__(self, *, settings: Settings, client: Any | None = None) -> None:
        self._settings = settings
        self._owns_client = client is None
        self._client = client or genai.Client(
            api_key=settings.GEMINI_API_KEY,
            http_options=types.HttpOptions(
                timeout=settings.AI_GENERATION_TIMEOUT_MS,
                # P3-T4 owns retry policy.  Disable the SDK's otherwise-hidden
                # five-attempt default so one adapter invocation is one attempt.
                retry_options=types.HttpRetryOptions(attempts=1),
            ),
        )

        try:
            threshold = _SAFETY_THRESHOLDS[
                settings.AI_SAFETY_THRESHOLD.strip().casefold()
            ]
        except KeyError as exc:
            accepted = ", ".join(sorted(_SAFETY_THRESHOLDS))
            raise ValueError(
                f"Unsupported AI_SAFETY_THRESHOLD; expected one of: {accepted}"
            ) from exc

        self._safety_settings = [
            types.SafetySetting(category=category, threshold=threshold)
            for category in _SAFETY_CATEGORIES
        ]

    async def __aenter__(self) -> "GeminiAdapter":
        return self

    async def __aexit__(self, *_: object) -> None:
        if self._owns_client:
            await self._client.aio.aclose()

    async def generate(self, request: LlmRequest) -> LlmResponse:
        return await self._complete(
            request=request,
            model=self._settings.AI_MODEL_GENERATE,
            operation=ProviderOperation.GENERATE,
        )

    async def classify(self, request: LlmRequest) -> LlmResponse:
        return await self._complete(
            request=request,
            model=self._settings.AI_MODEL_CLASSIFY,
            operation=ProviderOperation.CLASSIFY,
        )

    async def embed(self, texts: list[str]) -> list[list[float]]:
        model = self._settings.AI_EMBEDDING_MODEL
        operation = ProviderOperation.EMBED
        if not texts:
            raise ValueError("texts must contain at least one item")
        if len(texts) > self._settings.AI_BATCH_EMBED_SIZE:
            raise ValueError(
                "texts exceeds configured AI_BATCH_EMBED_SIZE="
                f"{self._settings.AI_BATCH_EMBED_SIZE}"
            )
        if any(not isinstance(text, str) or not text.strip() for text in texts):
            raise ValueError("every embedding input must be a non-blank string")

        try:
            async with asyncio.timeout(
                self._settings.AI_GENERATION_TIMEOUT_MS / 1000
            ):
                response = await self._client.aio.models.embed_content(
                    model=model,
                    contents=texts,
                    config=types.EmbedContentConfig(
                        output_dimensionality=self._settings.AI_EMBEDDING_DIMENSIONS,
                    ),
                )
        except TimeoutError as exc:
            raise self._transport_error(
                operation=operation,
                model=model,
                kind=ProviderErrorKind.TIMEOUT,
                cause=exc,
            ) from exc
        except genai_errors.APIError as exc:
            raise self._from_api_error(exc, operation=operation, model=model) from exc
        except httpx.TimeoutException as exc:
            raise self._transport_error(
                operation=operation,
                model=model,
                kind=ProviderErrorKind.TIMEOUT,
                cause=exc,
            ) from exc
        except httpx.HTTPError as exc:
            raise self._transport_error(
                operation=operation,
                model=model,
                kind=ProviderErrorKind.NETWORK,
                cause=exc,
            ) from exc
        except genai_errors.UnknownApiResponseError as exc:
            raise self._transport_error(
                operation=operation,
                model=model,
                kind=ProviderErrorKind.INVALID_RESPONSE,
                cause=exc,
            ) from exc

        embeddings = list(response.embeddings or [])
        if len(embeddings) != len(texts):
            raise self._invalid_response(
                operation=operation,
                model=model,
                cause_type="EmbeddingCountMismatch",
            )

        normalized: list[list[float]] = []
        for item in embeddings:
            values = list(item.values or [])
            normalized.append(self._normalize_embedding(values, model=model))
        return normalized

    async def _complete(
        self,
        *,
        request: LlmRequest,
        model: str,
        operation: ProviderOperation,
    ) -> LlmResponse:
        contents, system_instruction = self._to_gemini_contents(request)
        config = types.GenerateContentConfig(
            system_instruction=system_instruction,
            temperature=(
                request.temperature
                if request.temperature is not None
                else self._settings.AI_TEMPERATURE
            ),
            max_output_tokens=(
                request.max_tokens
                if request.max_tokens is not None
                else self._settings.AI_MAX_TOKENS
            ),
            response_mime_type=(
                "application/json" if request.response_schema is not None else None
            ),
            # The neutral contract accepts standard JSON Schema.  Gemini's
            # ``response_schema`` is a narrower provider dialect, while
            # ``response_json_schema`` preserves supported JSON-Schema keywords
            # such as additionalProperties and $defs.
            response_json_schema=request.response_schema,
            safety_settings=self._safety_settings,
            # This P3-T2 contract has no tool surface.  The SDK otherwise enables
            # automatic function calling by default even when no tools are supplied.
            automatic_function_calling=types.AutomaticFunctionCallingConfig(
                disable=True
            ),
            thinking_config=(
                types.ThinkingConfig(
                    thinking_budget=self._settings.AI_ESCALATION_THINKING_BUDGET,
                    include_thoughts=False,
                )
                if request.reasoning_effort == "extended"
                else None
            ),
        )

        started = time.perf_counter()
        try:
            async with asyncio.timeout(
                self._settings.AI_GENERATION_TIMEOUT_MS / 1000
            ):
                response = await self._client.aio.models.generate_content(
                    model=model,
                    contents=contents,
                    config=config,
                )
        except TimeoutError as exc:
            raise self._transport_error(
                operation=operation,
                model=model,
                kind=ProviderErrorKind.TIMEOUT,
                cause=exc,
            ) from exc
        except genai_errors.APIError as exc:
            raise self._from_api_error(exc, operation=operation, model=model) from exc
        except httpx.TimeoutException as exc:
            raise self._transport_error(
                operation=operation,
                model=model,
                kind=ProviderErrorKind.TIMEOUT,
                cause=exc,
            ) from exc
        except httpx.HTTPError as exc:
            raise self._transport_error(
                operation=operation,
                model=model,
                kind=ProviderErrorKind.NETWORK,
                cause=exc,
            ) from exc
        except genai_errors.UnknownApiResponseError as exc:
            raise self._transport_error(
                operation=operation,
                model=model,
                kind=ProviderErrorKind.INVALID_RESPONSE,
                cause=exc,
            ) from exc
        latency_ms = max(0, round((time.perf_counter() - started) * 1000))

        candidates = list(response.candidates or [])
        candidate = candidates[0] if candidates else None
        finish_reason = _enum_value(
            getattr(candidate, "finish_reason", None), fallback="UNKNOWN"
        )

        prompt_feedback = getattr(response, "prompt_feedback", None)
        prompt_block_reason = _enum_value(
            getattr(prompt_feedback, "block_reason", None), fallback=""
        )
        if prompt_block_reason and prompt_block_reason != "BLOCKED_REASON_UNSPECIFIED":
            finish_reason = prompt_block_reason

        safety_categories = sorted(
            set(
                self._blocked_categories(
                    getattr(prompt_feedback, "safety_ratings", None)
                )
                | self._blocked_categories(
                    getattr(candidate, "safety_ratings", None)
                )
            )
        )
        safety_blocked = bool(
            safety_categories
            or finish_reason in _SAFETY_FINISH_REASONS
            or (
                prompt_block_reason
                and prompt_block_reason != "BLOCKED_REASON_UNSPECIFIED"
            )
        )
        content = response.text or ""
        if not content and not safety_blocked:
            raise self._invalid_response(
                operation=operation,
                model=model,
                cause_type="EmptyGeminiResponse",
            )

        usage = getattr(response, "usage_metadata", None)
        return LlmResponse(
            content=content,
            model=model,
            model_version=getattr(response, "model_version", None),
            provider=self.id,
            tokens_in=int(getattr(usage, "prompt_token_count", 0) or 0),
            # Thinking tokens are billable generation usage even though thoughts
            # are never returned to the application.
            tokens_out=(
                int(getattr(usage, "candidates_token_count", 0) or 0)
                + int(getattr(usage, "thoughts_token_count", 0) or 0)
            ),
            latency_ms=latency_ms,
            finish_reason=finish_reason,
            safety_blocked=safety_blocked,
            safety_categories=safety_categories,
        )

    @staticmethod
    def _to_gemini_contents(
        request: LlmRequest,
    ) -> tuple[list[types.Content], str | None]:
        system_parts = [
            message.content for message in request.messages if message.role == "system"
        ]
        contents = [
            types.Content(
                role="model" if message.role == "assistant" else "user",
                parts=[types.Part.from_text(text=message.content)],
            )
            for message in request.messages
            if message.role != "system"
        ]
        return contents, "\n\n".join(system_parts) or None

    @staticmethod
    def _blocked_categories(ratings: Iterable[Any] | None) -> set[str]:
        if not ratings:
            return set()
        return {
            _enum_value(getattr(rating, "category", None), fallback="UNKNOWN")
            for rating in ratings
            if bool(getattr(rating, "blocked", False))
        }

    def _normalize_embedding(self, values: list[float], *, model: str) -> list[float]:
        dimensions = self._settings.AI_EMBEDDING_DIMENSIONS
        if len(values) < dimensions:
            raise self._invalid_response(
                operation=ProviderOperation.EMBED,
                model=model,
                cause_type=f"EmbeddingWidth{len(values)}",
            )

        truncated = [float(value) for value in values[:dimensions]]
        magnitude = math.sqrt(sum(value * value for value in truncated))
        if not math.isfinite(magnitude) or magnitude == 0.0:
            raise self._invalid_response(
                operation=ProviderOperation.EMBED,
                model=model,
                cause_type="InvalidEmbeddingMagnitude",
            )
        return [value / magnitude for value in truncated]

    def _from_api_error(
        self,
        exc: genai_errors.APIError,
        *,
        operation: ProviderOperation,
        model: str,
    ) -> LlmProviderError:
        kind, retryable = _api_error_kind(exc)
        return LlmProviderError(
            provider=self.id,
            operation=operation,
            model=model,
            kind=kind,
            retryable=retryable,
            upstream_status_code=int(getattr(exc, "code", 0) or 0) or None,
            retry_after_seconds=_retry_after_seconds(exc),
            cause_type=type(exc).__name__,
        )

    def _transport_error(
        self,
        *,
        operation: ProviderOperation,
        model: str,
        kind: ProviderErrorKind,
        cause: BaseException,
    ) -> LlmProviderError:
        return LlmProviderError(
            provider=self.id,
            operation=operation,
            model=model,
            kind=kind,
            retryable=True,
            cause_type=type(cause).__name__,
        )

    def _invalid_response(
        self,
        *,
        operation: ProviderOperation,
        model: str,
        cause_type: str,
    ) -> LlmProviderError:
        return LlmProviderError(
            provider=self.id,
            operation=operation,
            model=model,
            kind=ProviderErrorKind.INVALID_RESPONSE,
            retryable=True,
            cause_type=cause_type,
        )


__all__ = ["GeminiAdapter"]
