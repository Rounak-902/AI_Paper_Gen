"""Pre-spend tenant token reservations and per-call cost telemetry."""
from __future__ import annotations

import asyncio
import inspect
import logging
import uuid
from dataclasses import dataclass
from decimal import Decimal
from typing import Awaitable, Callable, Protocol

from app.modules.ai.providers.base import LlmProvider
from app.modules.ai.providers.errors import LlmProviderError
from app.modules.ai.providers.schemas import LlmRequest, LlmResponse


logger = logging.getLogger(__name__)


class BudgetExhaustedError(RuntimeError):
    """Raised before a provider call when its worst-case tokens do not fit."""

    def __init__(self, *, tenant_id: uuid.UUID, requested: int, remaining: int) -> None:
        self.tenant_id = tenant_id
        self.requested = requested
        self.remaining = remaining
        super().__init__(
            f"tenant token budget exhausted: requested={requested}, remaining={remaining}"
        )


@dataclass(frozen=True, slots=True)
class BudgetReservation:
    reservation_id: uuid.UUID
    tenant_id: uuid.UUID
    tokens: int


class TokenBudgetGate(Protocol):
    async def reserve(self, tenant_id: uuid.UUID, tokens: int) -> BudgetReservation: ...

    async def settle(self, reservation: BudgetReservation, actual_tokens: int) -> None: ...

    async def remaining(self, tenant_id: uuid.UUID) -> int: ...


class InMemoryTokenBudgetGate:
    """Atomic budget gate suitable for workers/tests and replaceable by a ledger.

    A long-lived worker should share one instance.  A future billing-backed
    implementation can satisfy the same protocol without putting a DB import in
    the AI module.
    """

    def __init__(self, per_tenant_limit: int) -> None:
        if type(per_tenant_limit) is not int or per_tenant_limit < 0:
            raise ValueError("per_tenant_limit must be a non-negative integer")
        self._limit = per_tenant_limit
        self._used: dict[uuid.UUID, int] = {}
        self._reserved: dict[uuid.UUID, int] = {}
        self._reservations: dict[uuid.UUID, BudgetReservation] = {}
        self._lock = asyncio.Lock()

    async def reserve(self, tenant_id: uuid.UUID, tokens: int) -> BudgetReservation:
        if type(tokens) is not int or tokens <= 0:
            raise ValueError("reservation tokens must be a positive integer")
        async with self._lock:
            used = self._used.get(tenant_id, 0)
            held = self._reserved.get(tenant_id, 0)
            remaining = max(0, self._limit - used - held)
            if tokens > remaining:
                raise BudgetExhaustedError(
                    tenant_id=tenant_id,
                    requested=tokens,
                    remaining=remaining,
                )
            reservation = BudgetReservation(uuid.uuid4(), tenant_id, tokens)
            self._reservations[reservation.reservation_id] = reservation
            self._reserved[tenant_id] = held + tokens
            return reservation

    async def settle(self, reservation: BudgetReservation, actual_tokens: int) -> None:
        if type(actual_tokens) is not int or not 0 <= actual_tokens <= reservation.tokens:
            raise ValueError("actual_tokens must fit inside the reservation")
        async with self._lock:
            current = self._reservations.pop(reservation.reservation_id, None)
            if current != reservation:
                raise ValueError("unknown or already-settled budget reservation")
            tenant_id = reservation.tenant_id
            self._reserved[tenant_id] -= reservation.tokens
            self._used[tenant_id] = self._used.get(tenant_id, 0) + actual_tokens

    async def remaining(self, tenant_id: uuid.UUID) -> int:
        async with self._lock:
            return max(
                0,
                self._limit
                - self._used.get(tenant_id, 0)
                - self._reserved.get(tenant_id, 0),
            )


@dataclass(frozen=True, slots=True)
class TokenPrice:
    input_per_million_usd: Decimal
    output_per_million_usd: Decimal

    def calculate(self, tokens_in: int, tokens_out: int) -> Decimal:
        million = Decimal(1_000_000)
        return (
            Decimal(tokens_in) * self.input_per_million_usd
            + Decimal(tokens_out) * self.output_per_million_usd
        ) / million


@dataclass(frozen=True, slots=True)
class CostRates:
    flash: TokenPrice
    pro: TokenPrice
    escalation: TokenPrice

    @classmethod
    def from_settings(cls, settings) -> "CostRates":
        def price(prefix: str) -> TokenPrice:
            return TokenPrice(
                Decimal(str(getattr(settings, f"AI_COST_{prefix}_INPUT_PER_MILLION_USD"))),
                Decimal(str(getattr(settings, f"AI_COST_{prefix}_OUTPUT_PER_MILLION_USD"))),
            )

        return cls(price("FLASH"), price("PRO"), price("ESCALATION"))


@dataclass(frozen=True, slots=True)
class AiCallTelemetry:
    tenant_id: uuid.UUID
    cascade_rung: int
    operation: str
    model: str
    tokens_in: int
    tokens_out: int
    reserved_tokens: int
    ai_cost_usd: Decimal
    status: str
    error_kind: str | None = None

    def as_dict(self) -> dict[str, object]:
        return {
            "tenant_id": str(self.tenant_id),
            "cascade_rung": self.cascade_rung,
            "operation": self.operation,
            "model": self.model,
            "tokens_in": self.tokens_in,
            "tokens_out": self.tokens_out,
            "reserved_tokens": self.reserved_tokens,
            "ai_cost_usd": str(self.ai_cost_usd),
            "status": self.status,
            "error_kind": self.error_kind,
        }


TelemetrySink = Callable[[AiCallTelemetry], Awaitable[None] | None]


class BudgetedProvider(LlmProvider):
    """Reserve at every Gemini rung entry, then emit one event per attempt."""

    id = "budgeted"

    def __init__(
        self,
        *,
        delegate: LlmProvider,
        tenant_id: uuid.UUID,
        budget: TokenBudgetGate,
        rates: CostRates,
        flash_model: str,
        pro_model: str,
        escalation_thinking_tokens: int,
        sink: TelemetrySink | None = None,
    ) -> None:
        self._delegate = delegate
        self._tenant_id = tenant_id
        self._budget = budget
        self._rates = rates
        self._flash_model = flash_model
        self._pro_model = pro_model
        self._escalation_thinking_tokens = escalation_thinking_tokens
        self._sink = sink
        self.events: list[AiCallTelemetry] = []

    async def generate(self, request: LlmRequest) -> LlmResponse:
        rung = 6 if request.reasoning_effort == "extended" else 5
        rate = self._rates.escalation if rung == 6 else self._rates.pro
        return await self._call(
            operation="generate",
            rung=rung,
            model=self._pro_model,
            request=request,
            rate=rate,
            call=self._delegate.generate,
        )

    async def classify(self, request: LlmRequest) -> LlmResponse:
        return await self._call(
            operation="classify",
            rung=4,
            model=self._flash_model,
            request=request,
            rate=self._rates.flash,
            call=self._delegate.classify,
        )

    async def embed(self, texts: list[str]) -> list[list[float]]:
        # Embeddings are outside the P3-T6 text cascade and remain inert under
        # ADR-007.  Do not silently invent a budget policy for a disabled path.
        return await self._delegate.embed(texts)

    async def _call(
        self,
        *,
        operation: str,
        rung: int,
        model: str,
        request: LlmRequest,
        rate: TokenPrice,
        call: Callable[[LlmRequest], Awaitable[LlmResponse]],
    ) -> LlmResponse:
        estimated_input = self._estimate_input_tokens(request)
        max_output = request.max_tokens or 1
        reasoning = self._escalation_thinking_tokens if rung == 6 else 0
        reservation_tokens = estimated_input + max_output + reasoning
        # This is the enforcement point: no delegate method is touched unless
        # the atomic reservation succeeds.
        reservation = await self._budget.reserve(self._tenant_id, reservation_tokens)
        try:
            response = await call(request)
        except BaseException as exc:
            # A timeout can still be billed upstream.  Conservatively consume the
            # reservation so retries can never push the tenant over its ceiling.
            await self._budget.settle(reservation, reservation.tokens)
            error_kind = (
                exc.kind.value if isinstance(exc, LlmProviderError) else type(exc).__name__
            )
            event = AiCallTelemetry(
                self._tenant_id,
                rung,
                operation,
                getattr(exc, "model", model),
                0,
                0,
                reservation.tokens,
                Decimal("0"),
                "error",
                error_kind,
            )
            await self._emit(event)
            raise

        actual_tokens = response.tokens_in + response.tokens_out
        if actual_tokens > reservation.tokens:
            # The max-output reservation should make this impossible.  Consume
            # the full reservation and fail closed rather than under-metering.
            await self._budget.settle(reservation, reservation.tokens)
            raise RuntimeError("provider usage exceeded its pre-spend token reservation")
        await self._budget.settle(reservation, actual_tokens)
        cost = (
            Decimal(str(response.cost_estimate))
            if response.cost_estimate is not None
            else rate.calculate(response.tokens_in, response.tokens_out)
        )
        response = response.model_copy(update={"cost_estimate": float(cost)})
        event = AiCallTelemetry(
            self._tenant_id,
            rung,
            operation,
            response.model,
            response.tokens_in,
            response.tokens_out,
            reservation.tokens,
            cost,
            "ok",
        )
        await self._emit(event)
        return response

    async def _emit(self, event: AiCallTelemetry) -> None:
        self.events.append(event)
        logger.info("ai_call_telemetry %s", event.as_dict())
        if self._sink is not None:
            result = self._sink(event)
            if inspect.isawaitable(result):
                await result

    @staticmethod
    def _estimate_input_tokens(request: LlmRequest) -> int:
        # Conservative local upper bound: one token per UTF-8 byte plus role
        # overhead.  Common chars/token heuristics divide by four and can
        # under-reserve for short, symbolic, or multilingual input, which is not
        # acceptable for a hard pre-spend ceiling.
        byte_count = sum(len(message.content.encode("utf-8")) for message in request.messages)
        return max(1, byte_count + 4 * len(request.messages))


__all__ = [
    "AiCallTelemetry",
    "BudgetExhaustedError",
    "BudgetReservation",
    "BudgetedProvider",
    "CostRates",
    "InMemoryTokenBudgetGate",
    "TokenBudgetGate",
    "TokenPrice",
]
