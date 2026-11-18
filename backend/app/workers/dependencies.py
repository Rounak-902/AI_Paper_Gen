"""Existing application ports the future generation worker will compose.

These imports are intentionally explicit evidence of the P4 boundary: Celery jobs
will orchestrate the existing retrieval, generation, persistence, and tenant-session
implementations. No worker-local copy of any of that logic exists.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from app.db.session import get_tenant_session
from app.modules.ai.pipeline import GenerationPipeline
from app.modules.questions.repository import QuestionRepository
from app.modules.retrieval.service import RetrievalService


@dataclass(frozen=True)
class GenerationWorkerDependencies:
    """Concrete existing types/functions reserved for P4-T4 composition."""

    tenant_session: Any = get_tenant_session
    retrieval_service: type[RetrievalService] = RetrievalService
    generation_pipeline: type[GenerationPipeline] = GenerationPipeline
    question_repository: type[QuestionRepository] = QuestionRepository


__all__ = ["GenerationWorkerDependencies"]
