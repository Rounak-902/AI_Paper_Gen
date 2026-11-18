"""Application boundary from deterministic templates to the real question bank."""

from __future__ import annotations

import uuid
from dataclasses import dataclass
from typing import Mapping, Optional

from sqlalchemy.ext.asyncio import AsyncSession

from app.common.deps import RequestScope
from app.modules.questions import schemas, service
from packages.prompts.templates import GeneratedQuestion, TemplateEngine


@dataclass(frozen=True, slots=True)
class TemplateQuestionCreateResult:
    generated: GeneratedQuestion
    question: schemas.QuestionCreateOut


def build_template_question_payload(
    generated: GeneratedQuestion,
    *,
    board_id: uuid.UUID | None = None,
    class_id: uuid.UUID | None = None,
    subject_id: uuid.UUID | None = None,
    chapter_id: uuid.UUID | None = None,
    learning_outcome_ids: tuple[uuid.UUID, ...] = (),
    competency_ids: tuple[uuid.UUID, ...] = (),
    scope: str | None = None,
) -> schemas.QuestionCreateIn:
    """Apply the exact P2-T3 structural/schema gate used for manual creation."""
    return schemas.QuestionCreateIn.model_validate(
        {
            **generated.as_question_fields(),
            "scope": scope,
            "board_id": board_id,
            "class_id": class_id,
            "subject_id": subject_id,
            "chapter_id": chapter_id,
            "learning_outcome_ids": list(learning_outcome_ids),
            "competency_ids": list(competency_ids),
            # Deterministic templates never have AI-prompt provenance.
            "prompt_version_id": None,
        }
    )


async def generate_and_create_question(
    *,
    session: AsyncSession,
    request_scope: RequestScope,
    org_id: uuid.UUID,
    template_ref: str,
    seed: int | None = None,
    parameters: Mapping[str, int | str] | None = None,
    board_id: uuid.UUID | None = None,
    class_id: uuid.UUID | None = None,
    subject_id: uuid.UUID | None = None,
    chapter_id: uuid.UUID | None = None,
    learning_outcome_ids: tuple[uuid.UUID, ...] = (),
    competency_ids: tuple[uuid.UUID, ...] = (),
    scope: str | None = None,
    ip: Optional[str] = None,
    engine: TemplateEngine | None = None,
) -> TemplateQuestionCreateResult:
    """Generate, structurally validate, deduplicate, audit, and persist one question."""
    generated = (engine or TemplateEngine()).generate(
        template_ref,
        seed=seed,
        parameters=parameters,
    )
    payload = build_template_question_payload(
        generated,
        board_id=board_id,
        class_id=class_id,
        subject_id=subject_id,
        chapter_id=chapter_id,
        learning_outcome_ids=learning_outcome_ids,
        competency_ids=competency_ids,
        scope=scope,
    )
    created = await service.create_question(
        session=session,
        scope=request_scope,
        org_id=org_id,
        payload=payload,
        ip=ip,
    )
    return TemplateQuestionCreateResult(generated=generated, question=created)
