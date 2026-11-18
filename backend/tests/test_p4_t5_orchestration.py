"""Focused P4-T5 orchestration contracts; live evidence is in the task report."""

from __future__ import annotations

from pathlib import Path

from app.workers.celery_app import celery_app
from app.workers.idempotency import IdempotentTask
from app.workers.tasks import finalize_paper, prepare_paper, process_paper_slot


def test_typed_steps_use_late_ack_idempotent_celery_envelopes() -> None:
    assert [prepare_paper.name, process_paper_slot.name, finalize_paper.name] == [
        "papergen.workers.prepare_paper",
        "papergen.workers.process_paper_slot",
        "papergen.workers.finalize_paper",
    ]
    for task in (prepare_paper, process_paper_slot, finalize_paper):
        assert isinstance(task, IdempotentTask)
        assert task.acks_late is True
        assert task.reject_on_worker_lost is True


def test_keepalive_and_outbox_reconciler_are_periodic() -> None:
    schedule = celery_app.conf.beat_schedule
    assert schedule["generation-queue-keepalive"]["task"] == (
        "papergen.workers.queue_keepalive"
    )
    assert schedule["generation-outbox-reconcile"]["task"] == (
        "papergen.workers.dispatch_outbox"
    )


def test_enqueue_api_is_transactional_outbox_only() -> None:
    source = (
        Path(__file__).resolve().parents[1] / "app" / "workers" / "outbox.py"
    ).read_text(encoding="utf-8")
    function = source.split("async def schedule_paper_generation", 1)[1]
    assert "add_outbox_task" in function
    assert ".apply_async(" not in function
    assert "ForeignKey(\"generated_papers.id\"" in (
        Path(__file__).resolve().parents[1] / "app" / "models" / "assembly.py"
    ).read_text(encoding="utf-8")


def test_residual_scope_is_full_remaining_set_and_final_exact_solve() -> None:
    source = (
        Path(__file__).resolve().parents[1]
        / "app"
        / "modules"
        / "assembly"
        / "resumable.py"
    ).read_text(encoding="utf-8")
    assert '"scope": "full-remaining-unfilled-set"' in source
    assert '"finalExactSelection"' in source
    assert "CpSatFeasibilitySolver" in source
    assert "cp_model" not in source
