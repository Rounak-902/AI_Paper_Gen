"""Operation-local provider retries and opt-in null-solution recovery."""
from __future__ import annotations

import asyncio
import math
from dataclasses import dataclass
from typing import TYPE_CHECKING, Awaitable, Callable, TypeVar

from app.modules.ai.providers.errors import LlmProviderError

if TYPE_CHECKING:
    from .validation import PipelineValidationError

T = TypeVar("T")


@dataclass(frozen=True)
class RetryEvent:
    operation: str
    failed_attempt: int
    kind: str
    delay_seconds: float | None


class RetryManager:
    """At most two retries (three attempts), 0.5/1s exponential backoff.

    Respect numeric Retry-After up to a 30s latency ceiling. Larger/nonfinite
    advice fails immediately rather than calling earlier than the provider asks.
    Authentication and other nonretryable errors retain their original EH-3 type.
    """

    def __init__(self, retries: int = 2, *, sleep: Callable[[float], Awaitable[None]] = asyncio.sleep):
        if type(retries) is not int or not 0 <= retries <= 2:
            raise ValueError("retries must be between zero and two")
        self.retries = retries
        self.sleep = sleep

    async def run(self, operation: str, call: Callable[[], Awaitable[T]], events: list[RetryEvent], *,
                  correct_validation: Callable[[PipelineValidationError], bool] | None = None) -> T:
        # Runtime import avoids the schemas -> retry -> validation -> schemas cycle.
        from .validation import PipelineValidationError

        for attempt in range(1, self.retries + 2):
            try:
                return await call()
            except LlmProviderError as exc:
                advice = exc.retry_after_seconds or 0.0
                retry = exc.retryable and attempt <= self.retries and math.isfinite(advice) and advice <= 30
                delay = max(0.5 * 2 ** (attempt - 1), advice) if retry else None
                events.append(RetryEvent(operation, attempt, exc.kind.value, delay))
                if not retry:
                    raise
                await self.sleep(delay)
            except PipelineValidationError as exc:
                # Opt-in recovery for one generation failure only. The caller
                # checks the question type/null fields and builds the reprompt.
                if (operation != "generate" or exc.check != "executable_solution_required"
                        or correct_validation is None or not correct_validation(exc)):
                    raise
                retry = attempt <= self.retries
                delay = 0.5 * 2 ** (attempt - 1) if retry else None
                events.append(RetryEvent(operation, attempt, exc.check, delay))
                if not retry:
                    raise
                await self.sleep(delay)
        raise AssertionError("unreachable")
