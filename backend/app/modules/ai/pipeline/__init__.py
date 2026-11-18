"""Pure generation orchestration; caller owns retrieval, prompts and persistence."""
from .budget import (
    AiCallTelemetry,
    BudgetExhaustedError,
    CostRates,
    InMemoryTokenBudgetGate,
    TokenPrice,
)
from .cascade import (
    CostCascade,
    ESCALATION_VERIFICATION_FAILURES,
    InMemoryExactResultCache,
)
from .classification import DeterministicClassifier
from .context import ContextBuilder, ModelSelector
from .retry import RetryManager
from .schemas import CurriculumContext, PipelinePrompts, PipelineResult, SimilarityResult
from .service import GenerationPipeline
from .validation import PipelineValidationError, ValidationEngine

__all__ = [
    "AiCallTelemetry",
    "BudgetExhaustedError",
    "ContextBuilder",
    "CostCascade",
    "CostRates",
    "DeterministicClassifier",
    "ESCALATION_VERIFICATION_FAILURES",
    "GenerationPipeline",
    "InMemoryExactResultCache",
    "InMemoryTokenBudgetGate",
    "ModelSelector",
    "PipelinePrompts",
    "PipelineResult",
    "PipelineValidationError",
    "RetryManager",
    "SimilarityResult",
    "TokenPrice",
    "ValidationEngine",
    "CurriculumContext",
]
