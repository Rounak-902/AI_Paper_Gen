"""Focused unit proofs for the P4-T1 Celery scaffold."""

from __future__ import annotations

from typing import Any

import pytest
from celery import Task

from app.modules.ai.pipeline import GenerationPipeline
from app.modules.questions.repository import QuestionRepository
from app.modules.retrieval.service import RetrievalService
from app.workers.celery_app import celery_app
from app.workers.dependencies import GenerationWorkerDependencies
from app.workers.idempotency import Reservation
from app.workers.tasks import delivery_probe


class FakeStore:
    def __init__(self) -> None:
        self.owners: dict[str, str] = {}

    def reserve(self, idempotency_key: str, task_id: str) -> Reservation:
        owner = self.owners.setdefault(idempotency_key, task_id)
        return Reservation(owner_task_id=owner, acquired=owner == task_id)

    def release(self, idempotency_key: str, task_id: str) -> bool:
        if self.owners.get(idempotency_key) != task_id:
            return False
        del self.owners[idempotency_key]
        return True

    def confirm(self, idempotency_key: str, task_id: str) -> bool:
        return self.owners.get(idempotency_key) == task_id


def test_celery_uses_redis_and_late_acknowledgement() -> None:
    assert str(celery_app.conf.broker_url).startswith("redis://")
    assert str(celery_app.conf.result_backend).startswith("redis://")
    assert celery_app.conf.task_acks_late is True
    assert celery_app.conf.task_reject_on_worker_lost is True
    assert celery_app.conf.worker_prefetch_multiplier == 1
    assert delivery_probe.acks_late is True
    assert delivery_probe.reject_on_worker_lost is True


def test_worker_boundary_reuses_existing_application_types() -> None:
    ports = GenerationWorkerDependencies()
    assert ports.retrieval_service is RetrievalService
    assert ports.generation_pipeline is GenerationPipeline
    assert ports.question_repository is QuestionRepository
    assert ports.tenant_session.__module__ == "app.db.session"


def test_missing_caller_idempotency_key_is_rejected() -> None:
    with pytest.raises(ValueError, match="caller-supplied idempotency_key"):
        delivery_probe.apply_async(kwargs={"marker": "missing-key"})


def test_duplicate_enqueue_publishes_only_once(monkeypatch: pytest.MonkeyPatch) -> None:
    store = FakeStore()
    published: list[dict[str, Any]] = []

    monkeypatch.setattr(
        "app.workers.idempotency.get_idempotency_store", lambda: store
    )

    def fake_apply_async(
        self: Task,
        args: Any = None,
        kwargs: Any = None,
        task_id: str | None = None,
        **options: Any,
    ) -> Any:
        published.append({"task_id": task_id, "kwargs": kwargs})
        return self.AsyncResult(task_id)

    monkeypatch.setattr(Task, "apply_async", fake_apply_async)

    first = delivery_probe.apply_async(
        kwargs={"idempotency_key": "paper:42:generation", "marker": "one"}
    )
    duplicate = delivery_probe.apply_async(
        kwargs={"idempotency_key": "paper:42:generation", "marker": "two"}
    )

    assert len(published) == 1
    assert first.id == duplicate.id == published[0]["task_id"]


def test_publish_failure_releases_reservation(monkeypatch: pytest.MonkeyPatch) -> None:
    store = FakeStore()
    monkeypatch.setattr(
        "app.workers.idempotency.get_idempotency_store", lambda: store
    )

    def fail_publish(*args: Any, **kwargs: Any) -> Any:
        raise RuntimeError("broker publish failed")

    monkeypatch.setattr(Task, "apply_async", fail_publish)

    with pytest.raises(RuntimeError, match="broker publish failed"):
        delivery_probe.apply_async(
            kwargs={"idempotency_key": "paper:43:generation", "marker": "failure"}
        )

    assert store.owners == {}
