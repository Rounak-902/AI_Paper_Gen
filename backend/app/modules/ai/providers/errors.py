"""Typed provider failures normalized for the P3-T4 retry manager."""

from __future__ import annotations

from enum import StrEnum

from app.common.errors import UpstreamProviderError


class ProviderOperation(StrEnum):
    GENERATE = "generate"
    CLASSIFY = "classify"
    EMBED = "embed"


class ProviderErrorKind(StrEnum):
    INVALID_REQUEST = "invalid_request"
    AUTHENTICATION = "authentication"
    PERMISSION_DENIED = "permission_denied"
    MODEL_NOT_FOUND = "model_not_found"
    RATE_LIMITED = "rate_limited"
    QUOTA_EXCEEDED = "quota_exceeded"
    TIMEOUT = "timeout"
    UNAVAILABLE = "unavailable"
    NETWORK = "network"
    INVALID_RESPONSE = "invalid_response"
    UNKNOWN = "unknown"


class LlmProviderError(UpstreamProviderError):
    """A catchable EH-3 failure with normalized retry facts.

    The inherited HTTP mapping is 502/``UPSTREAM_PROVIDER_FAILED``/EH-3, so if a
    future route lets this reach FastAPI it is never turned into an opaque 500.
    Provider details stay in ``log_detail`` and out of the client envelope.
    """

    def __init__(
        self,
        *,
        provider: str,
        operation: ProviderOperation,
        model: str,
        kind: ProviderErrorKind,
        retryable: bool,
        upstream_status_code: int | None = None,
        retry_after_seconds: float | None = None,
        cause_type: str | None = None,
    ) -> None:
        self.provider = provider
        self.operation = operation
        self.model = model
        self.kind = kind
        self.retryable = retryable
        self.upstream_status_code = upstream_status_code
        self.retry_after_seconds = retry_after_seconds
        self.cause_type = cause_type

        facts = [
            f"provider={provider}",
            f"operation={operation.value}",
            f"model={model}",
            f"kind={kind.value}",
            f"retryable={str(retryable).lower()}",
        ]
        if upstream_status_code is not None:
            facts.append(f"upstream_status={upstream_status_code}")
        if retry_after_seconds is not None:
            facts.append(f"retry_after_seconds={retry_after_seconds}")
        if cause_type:
            facts.append(f"cause_type={cause_type}")

        super().__init__(
            "The AI provider could not complete the request.",
            log_detail=" ".join(facts),
        )


__all__ = ["LlmProviderError", "ProviderErrorKind", "ProviderOperation"]
