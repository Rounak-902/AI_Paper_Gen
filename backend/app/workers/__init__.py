"""Celery worker infrastructure for the backend workspace.

Workers are another process boundary over the existing ``app.modules`` package;
they are not a second business-logic implementation.
"""

from app.workers.celery_app import celery_app
from app.workers.idempotency import IdempotentTask, enqueue_idempotent
from app.workers.outbox import schedule_paper_generation

__all__ = [
    "IdempotentTask",
    "celery_app",
    "enqueue_idempotent",
    "schedule_paper_generation",
]
