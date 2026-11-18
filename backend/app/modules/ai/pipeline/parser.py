"""Malformed/truncated/safety-blocked model output retains the EH-3 mapping."""
from __future__ import annotations

import json
from typing import TypeVar
from pydantic import BaseModel
from app.modules.ai.providers.errors import LlmProviderError, ProviderErrorKind, ProviderOperation
from app.modules.ai.providers.schemas import LlmResponse

T = TypeVar("T", bound=BaseModel)


def _unique(pairs):
    result = {}
    for key, value in pairs:
        if key in result:
            raise ValueError("duplicate JSON key")
        result[key] = value
    return result


class ResponseParser:
    @staticmethod
    def parse(response: LlmResponse, shape: type[T], operation: ProviderOperation) -> T:
        try:
            if response.safety_blocked or response.finish_reason.upper() != "STOP":
                raise ValueError("completion was blocked or incomplete")
            if len(response.content) > 256 * 1024:
                raise ValueError("response exceeds parsing size limit")
            data = json.loads(response.content, object_pairs_hook=_unique,
                              parse_constant=lambda _: (_ for _ in ()).throw(ValueError("nonfinite JSON")))
            return shape.model_validate(data)
        except (ValueError, TypeError, ArithmeticError, RecursionError) as exc:
            raise LlmProviderError(provider=response.provider, operation=operation,
                                   model=response.model, kind=ProviderErrorKind.INVALID_RESPONSE,
                                   retryable=not response.safety_blocked, cause_type=type(exc).__name__) from exc
