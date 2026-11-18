"""CP-SAT paper-feasibility domain service (ADR-009 / P4-T2)."""

from packages.domain.assembler.solver import (
    Assignment,
    Blueprint,
    ChapterTarget,
    Compromise,
    CoverageFact,
    CpSatFeasibilitySolver,
    FeasibilityOutcome,
    InfeasibilityReason,
    LearningOutcomeTarget,
    MixTarget,
    QuestionCandidate,
    Section,
    SolveResult,
)

__all__ = [
    "Assignment",
    "Blueprint",
    "ChapterTarget",
    "Compromise",
    "CoverageFact",
    "CpSatFeasibilitySolver",
    "FeasibilityOutcome",
    "InfeasibilityReason",
    "LearningOutcomeTarget",
    "MixTarget",
    "QuestionCandidate",
    "Section",
    "SolveResult",
]
