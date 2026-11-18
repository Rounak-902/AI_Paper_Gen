"""Pure assembly from P3-T1 output and caller-loaded curriculum/blueprint."""
from __future__ import annotations

from decimal import Decimal
from packages.prompts.templates import TemplateEngine
from app.modules.retrieval.schemas import BlueprintSlot, SlotRetrievalResult
from .schemas import CurriculumContext, GenerationContext


class ContextBuilder:
    def __init__(self, engine: TemplateEngine | None = None):
        self.engine = engine or TemplateEngine()

    def build(self, *, slot: BlueprintSlot, retrieval: SlotRetrievalResult,
              curriculum: CurriculumContext, language: str = "en", seed: int | None = None) -> GenerationContext:
        if retrieval.slot_id != slot.slot_id:
            raise ValueError("retrieval belongs to another slot")
        for name in ("board_id", "class_id", "subject_id", "curriculum_version_id"):
            if getattr(slot, name) != getattr(curriculum, name):
                raise ValueError(f"curriculum {name} mismatch")
        if not set(slot.chapter_ids) <= set(curriculum.chapter_ids):
            raise ValueError("curriculum does not cover requested chapters")
        if not set(slot.learning_outcome_ids) <= set(curriculum.learning_outcome_ids):
            raise ValueError("curriculum does not cover requested outcomes")
        if not language.strip():
            raise ValueError("language must be nonblank")
        # Reject out-of-slot evidence, rather than putting unrelated retrieval data
        # into a grounded generation prompt. Retrieval remains entirely unchanged.
        for candidate in retrieval.candidates:
            if not set(candidate.matched_chapter_ids) & set(slot.chapter_ids):
                raise ValueError("retrieved evidence has no requested chapter")
            if candidate.question_id in slot.excluded_question_ids:
                raise ValueError("retrieved evidence is excluded")
        template = None
        for ref in curriculum.template_refs:
            # A bad reviewed mapping is a caller error; do not hide it with an LLM.
            generated = self.engine.generate(ref, seed=seed)
            if (generated.qtype == slot.qtype and Decimal(generated.marks) == slot.marks
                    and generated.difficulty == slot.difficulty
                    and (slot.bloom is None or generated.bloom == slot.bloom)
                    and generated.language == language):
                template = generated
                break
        return GenerationContext(slot.model_copy(deep=True), retrieval.model_copy(deep=True),
                                 curriculum.model_copy(deep=True), language, template)


class ModelSelector:
    @staticmethod
    def select(context: GenerationContext) -> str:
        # MCQ/TF/numerical are fully verifiable on the reviewed template branch;
        # one-word/fill-blank catalog entries are numeric only. Qualitative types
        # always fall back, and their correctness judge can only advise (ADR-010).
        return "template" if context.template is not None else "llm"

    @staticmethod
    def select_llm_tier(task: str, *, reasoning_effort: str = "standard") -> str:
        """Resolve logical tasks to ADR-008 tiers without naming a model ID."""
        if reasoning_effort not in ("standard", "extended"):
            raise ValueError("reasoning_effort must be 'standard' or 'extended'")
        if task == "classify_question":
            if reasoning_effort != "standard":
                raise ValueError("classification never uses extended Pro reasoning")
            return "flash"
        if task == "generate_question":
            return "pro_extended" if reasoning_effort == "extended" else "pro"
        raise ValueError(f"unsupported pipeline task {task!r}")
