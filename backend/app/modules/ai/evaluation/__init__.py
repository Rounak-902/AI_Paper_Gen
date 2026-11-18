"""Offline AI evaluation and P3-T3 prompt/model comparison contracts."""

from .benchmark import GoldenCase, load_curriculum_bank_golden_set
from .harness import (
    ComparisonReport,
    EvalAttempt,
    EvaluationReport,
    EvaluationVariant,
    OfflineEvaluator,
)
from .replay import ReplayCascadeRunner, resolved_artifact_prompt

__all__ = [
    "ComparisonReport",
    "EvalAttempt",
    "EvaluationReport",
    "EvaluationVariant",
    "GoldenCase",
    "OfflineEvaluator",
    "ReplayCascadeRunner",
    "load_curriculum_bank_golden_set",
    "resolved_artifact_prompt",
]
