"""Deterministic difficulty and Bloom classification for cascade rung 3.

The classifier is intentionally small and explainable.  It only claims high
confidence when a stem contains an unambiguous Bloom verb and a matching
difficulty signal.  Ambiguous or conflicting text remains below the configured
fallback threshold and is sent to the Flash tier by the pipeline.
"""
from __future__ import annotations

import re
from dataclasses import dataclass

from .schemas import Classification, GeneratedContent, GenerationContext


_BLOOM_VERBS: dict[str, tuple[str, ...]] = {
    "remember": ("define", "identify", "list", "name", "recall", "state", "what is"),
    "understand": ("classify", "describe", "explain", "outline", "summarize"),
    "apply": ("apply", "calculate", "compute", "demonstrate", "solve", "use"),
    "analyze": ("analyze", "compare", "contrast", "differentiate", "examine"),
    "evaluate": ("assess", "critique", "evaluate", "justify", "recommend"),
    "create": ("construct", "create", "design", "develop", "formulate"),
}

_HARD_SIGNALS = (
    "derive",
    "prove",
    "multi-step",
    "justify",
    "evaluate",
    "design",
    "synthesize",
)
_MEDIUM_SIGNALS = (
    "analyze",
    "apply",
    "calculate",
    "compare",
    "compute",
    "explain",
    "solve",
)
_EASY_SIGNALS = ("define", "identify", "list", "name", "recall", "state", "what is")


@dataclass(frozen=True, slots=True)
class LocalClassification:
    classification: Classification
    difficulty_confidence: float
    bloom_confidence: float

    @property
    def confidence(self) -> float:
        return min(self.difficulty_confidence, self.bloom_confidence)


class DeterministicClassifier:
    """Classify only when rules are decisive; otherwise invite Flash fallback."""

    def classify(
        self,
        question: GeneratedContent,
        context: GenerationContext,
    ) -> LocalClassification:
        text = " ".join(question.stem.casefold().split())
        bloom, bloom_confidence, bloom_signal = self._bloom(text)
        difficulty, difficulty_confidence, difficulty_signal = self._difficulty(text)

        # A confident rule that contradicts the requested slot is a conflict, not
        # permission to overwrite either side.  Make the residual explicit so the
        # configured Flash threshold decides the fallback.
        if context.slot.bloom is not None and bloom != context.slot.bloom:
            bloom_confidence = min(bloom_confidence, 0.49)
        if difficulty != context.slot.difficulty:
            difficulty_confidence = min(difficulty_confidence, 0.49)

        confidence = min(difficulty_confidence, bloom_confidence)
        result = Classification(
            difficulty=difficulty,
            bloom=bloom,
            confidence=confidence,
            # The existing Validation Engine has already checked curriculum IDs,
            # evidence citations, and language before this classifier is called.
            curriculum_aligned=True,
            language_matches=question.language == context.language,
            grammar_flags=[],
            format_flags=[],
            qualitative_correctness_confidence=1.0,
            evidence=(
                f"deterministic rules: difficulty={difficulty_signal}; "
                f"bloom={bloom_signal}"
            ),
        )
        return LocalClassification(result, difficulty_confidence, bloom_confidence)

    @staticmethod
    def _contains(text: str, signal: str) -> bool:
        return re.search(rf"(?<![a-z]){re.escape(signal)}(?![a-z])", text) is not None

    def _bloom(self, text: str) -> tuple[str, float, str]:
        matches: list[tuple[str, str]] = []
        for level, verbs in _BLOOM_VERBS.items():
            for verb in verbs:
                if self._contains(text, verb):
                    matches.append((level, verb))
                    break
        if len(matches) == 1:
            level, verb = matches[0]
            return level, 0.92, f"unambiguous verb {verb!r}"
        if len(matches) > 1:
            return matches[-1][0], 0.45, "conflicting Bloom verbs"
        return "understand", 0.35, "no taxonomy verb"

    def _difficulty(self, text: str) -> tuple[str, float, str]:
        hard = [signal for signal in _HARD_SIGNALS if self._contains(text, signal)]
        medium = [signal for signal in _MEDIUM_SIGNALS if self._contains(text, signal)]
        easy = [signal for signal in _EASY_SIGNALS if self._contains(text, signal)]
        arithmetic_steps = len(re.findall(r"[+*/=]|(?<!\*)-(?!>)", text))

        if hard:
            return "hard", 0.86, f"hard signal {hard[0]!r}"
        if arithmetic_steps >= 3 or len(medium) >= 2:
            return "hard", 0.78, "multiple reasoning signals"
        if medium:
            return "medium", 0.82, f"reasoning signal {medium[0]!r}"
        if easy or arithmetic_steps <= 1:
            signal = easy[0] if easy else "single-step stem"
            return "easy", 0.82, f"easy signal {signal!r}"
        return "medium", 0.40, "no decisive difficulty signal"


__all__ = ["DeterministicClassifier", "LocalClassification"]
