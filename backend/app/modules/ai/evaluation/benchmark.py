"""Golden cases sourced from P2's importable curriculum/question-bank data.

The benchmark deliberately does not duplicate question fixtures.  It validates and
adapts the repository's real ``sample_import.json`` bank plus the matching curriculum
seed into stable evaluation cases.  Only the S-4 query paraphrases live separately,
because P2-T4 contains surface edits rather than semantic paraphrase pairs.
"""

from __future__ import annotations

import json
import uuid
from dataclasses import dataclass
from decimal import Decimal
from pathlib import Path
from typing import Any

from app.modules.ai.pipeline import ContextBuilder, CurriculumContext
from app.modules.ai.pipeline.schemas import GenerationContext
from app.modules.questions.schemas import QuestionCreateIn
from app.modules.retrieval.schemas import BlueprintSlot, ScoredCandidate, SlotRetrievalResult


_NAMESPACE = uuid.UUID("a076e5c1-0631-4d2f-a310-7ad087c62a61")


def _repo_root() -> Path:
    return Path(__file__).resolve().parents[5]


def _stable_id(kind: str, value: str) -> uuid.UUID:
    return uuid.uuid5(_NAMESPACE, f"{kind}:{value}")


def _json_cell(value: Any) -> Any:
    if value is None or value == "":
        return None
    if not isinstance(value, str):
        return value
    return json.loads(value)


@dataclass(frozen=True, slots=True)
class GoldenCase:
    """One expected output bound to its real P2 chapter/type bank cell."""

    case_id: str
    chapter_name: str
    curriculum_description: str
    reference_question_id: uuid.UUID
    question: QuestionCreateIn
    curriculum_version_id: uuid.UUID

    @property
    def benchmark_cell(self) -> tuple[str, str]:
        return self.chapter_name, self.question.qtype

    def generation_context(self) -> GenerationContext:
        question = self.question
        if question.board_id is None or question.class_id is None or question.subject_id is None:
            raise ValueError(f"golden case {self.case_id} has incomplete curriculum placement")
        if question.chapter_id is None:
            raise ValueError(f"golden case {self.case_id} has no chapter")

        slot = BlueprintSlot(
            slot_id=self.case_id,
            board_id=question.board_id,
            class_id=question.class_id,
            subject_id=question.subject_id,
            curriculum_version_id=self.curriculum_version_id,
            chapter_ids=[question.chapter_id],
            qtype=question.qtype,
            marks=question.marks,
            difficulty=question.difficulty,
            bloom=question.bloom,
            generation_mode="mixed",
            topic=self.chapter_name,
            learning_outcome_ids=list(question.learning_outcome_ids),
        )
        candidate = ScoredCandidate(
            question_id=self.reference_question_id,
            rank=1,
            pre_coverage_rank=1,
            score=1.0,
            metadata_score=1.0,
            fts_score=1.0,
            trigram_score=1.0,
            vector_score=0.0,
            coverage_priority=0.0,
            chapter_id=question.chapter_id,
            matched_chapter_ids=[question.chapter_id],
            learning_outcome_ids=list(question.learning_outcome_ids),
            qtype=question.qtype,
            marks=question.marks,
            difficulty=question.difficulty,
            bloom=question.bloom,
            source=question.source,
            scope=question.scope or "org",
            stem=question.stem,
        )
        retrieval = SlotRetrievalResult(
            slot_id=self.case_id,
            candidates=[candidate],
            metadata_candidate_count=1,
            duplicate_candidates_removed=0,
            required_candidates=1,
            gap=False,
        )
        curriculum = CurriculumContext(
            board_id=question.board_id,
            class_id=question.class_id,
            subject_id=question.subject_id,
            curriculum_version_id=self.curriculum_version_id,
            chapter_ids=[question.chapter_id],
            learning_outcome_ids=list(question.learning_outcome_ids),
            description=self.curriculum_description,
            textbook_snippets=[],
        )
        return ContextBuilder().build(
            slot=slot,
            retrieval=retrieval,
            curriculum=curriculum,
            language=question.language,
        )


def load_curriculum_bank_golden_set(
    *,
    bank_path: Path | str | None = None,
    curriculum_path: Path | str | None = None,
) -> tuple[GoldenCase, ...]:
    """Load every valid P2 bank row; never maintain a second question fixture set."""

    root = _repo_root()
    if bank_path is None:
        raise ValueError("bank_path is required; sample datasets are not shipped")
    if curriculum_path is None:
        raise ValueError("curriculum_path is required; curriculum samples are not shipped")
    bank_file = Path(bank_path)
    curriculum_file = Path(curriculum_path)
    bank_rows = json.loads(bank_file.read_text(encoding="utf-8"))
    curriculum_seed = json.loads(curriculum_file.read_text(encoding="utf-8"))
    if not isinstance(bank_rows, list) or not bank_rows:
        raise ValueError("P2 curriculum bank must be a non-empty JSON array")

    board = curriculum_seed["board"]
    class_row = board["classes"][0]
    subject = class_row["subjects"][0]
    version = board["curriculumVersion"]
    chapters = {chapter["title"]: chapter for chapter in subject["chapters"]}

    board_id = _stable_id("board", board["code"])
    class_id = _stable_id("class", f"{board['code']}:{class_row['name']}")
    subject_id = _stable_id(
        "subject", f"{board['code']}:{class_row['name']}:{subject['name']}"
    )
    curriculum_version_id = _stable_id(
        "curriculum-version", f"{board['code']}:{version['academicYear']}"
    )

    cases: list[GoldenCase] = []
    seen_ids: set[str] = set()
    for row_number, row in enumerate(bank_rows, start=1):
        chapter_name = row["chapter_name"]
        if chapter_name not in chapters:
            raise ValueError(
                f"bank row {row_number} names chapter {chapter_name!r} absent from the P2 curriculum seed"
            )
        chapter = chapters[chapter_name]
        chapter_id = _stable_id(
            "chapter",
            f"{board['code']}:{class_row['name']}:{subject['name']}:{chapter_name}",
        )
        reference_id = _stable_id(
            "question", f"{chapter_name}:{row['qtype']}:{row['stem']}"
        )
        case_id = f"bank-{row_number:03d}-{str(reference_id)[:8]}"
        if case_id in seen_ids:
            raise ValueError(f"duplicate golden case id {case_id}")
        seen_ids.add(case_id)

        question = QuestionCreateIn(
            qtype=row["qtype"],
            marks=Decimal(str(row["marks"])),
            difficulty=row["difficulty"],
            bloom=row.get("bloom"),
            language=row.get("language", "en"),
            source=row.get("source", "textbook"),
            scope=row.get("scope") or "org",
            board_id=board_id,
            class_id=class_id,
            subject_id=subject_id,
            chapter_id=chapter_id,
            stem=row["stem"],
            options=_json_cell(row.get("options_json")),
            answer=_json_cell(row.get("answer_json")),
            rubric=_json_cell(row.get("rubric_json")),
        )
        outcomes = chapter.get("learningOutcomes", [])
        descriptions = [item["description"] for item in outcomes]
        description = f"{chapter_name}. " + " ".join(descriptions)
        cases.append(
            GoldenCase(
                case_id=case_id,
                chapter_name=chapter_name,
                curriculum_description=description,
                reference_question_id=reference_id,
                question=question,
                curriculum_version_id=curriculum_version_id,
            )
        )
    return tuple(cases)


__all__ = ["GoldenCase", "load_curriculum_bank_golden_set"]
