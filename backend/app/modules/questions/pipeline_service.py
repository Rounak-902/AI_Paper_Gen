"""Caller-owned persistence/dedup bridge for the DB-free AI pipeline."""
from __future__ import annotations

from app.modules.questions import schemas, service
from app.modules.ai.pipeline import SimilarityResult
from app.common.errors import NotFoundError


def similarity_checker(*, session, request_scope, org_id):
    """Reuse the entire P2-T4 path in a rollback-only insertion probe.

    No hash/trigram SQL is copied here. Exact collisions retain P2's conflict;
    >=0.80 near matches retain its review warnings. The probe never commits.
    Final insertion must repeat the real check: probe results are not a lock or
    an authorization token. Caller must provide a session with no pending writes
    (begin_nested flushes pending state before its savepoint).
    """
    async def check(payload: schemas.QuestionCreateIn) -> SimilarityResult:
        if session.new or session.dirty or session.deleted:
            raise ValueError("similarity probe requires a session without pending writes")
        savepoint = await session.begin_nested()
        try:
            created = await service.create_question(session=session, scope=request_scope, org_id=org_id, payload=payload)
            return SimilarityResult(tuple(created.warnings))
        finally:
            await savepoint.rollback()
    return check


async def create_pipeline_question(*, result, session, request_scope, org_id):
    """Persist only an accepted result; transaction commit remains caller-owned."""
    return await service.create_question(session=session, scope=request_scope, org_id=org_id, payload=result.question)


def bank_question_resolver(*, session, request_scope, org_id):
    """Resolve P3-T1 candidate IDs through P2's authorized read path.

    The cascade neither re-runs retrieval nor imports a repository.  A candidate
    that disappeared between retrieval and resolution is an ordinary miss.
    """

    async def resolve(candidate) -> schemas.QuestionCreateIn | None:
        try:
            question = await service.get_question(
                session=session,
                scope=request_scope,
                org_id=org_id,
                question_id=candidate.question_id,
            )
        except NotFoundError:
            return None
        return schemas.QuestionCreateIn(
            qtype=question.qtype,
            marks=question.marks,
            difficulty=question.difficulty,
            bloom=question.bloom,
            language=question.language,
            source=question.source,
            scope=question.scope,
            board_id=question.board_id,
            class_id=question.class_id,
            subject_id=question.subject_id,
            chapter_id=question.chapter_id,
            stem=question.stem,
            options=question.options,
            answer=question.answer,
            rubric=question.rubric,
            learning_outcome_ids=list(question.learning_outcome_ids),
            competency_ids=list(question.competency_ids),
            meta={**question.meta, "reused_question_id": str(question.id)},
            prompt_version_id=question.prompt_version_id,
        )

    return resolve
