"""Celery delivery probes, resumable paper assembly, and progress publication.

P4-T6 observes P4-T5's durable slot state after transaction boundaries and publishes
best-effort Redis events. It does not own or alter assembly/checkpoint correctness.
"""

from __future__ import annotations

import asyncio
import hashlib
import logging
import time
import uuid
from datetime import datetime, timezone
from typing import Any

from redis import Redis
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from app.common.deps import RequestScope
from app.core.config import get_settings
from app.db.engine import create_engine
from app.db.session import get_tenant_session, get_unscoped_session
from app.models.assembly import PaperAssemblyRun, PaperAssemblySlot, TaskOutbox
from app.models.question_import import QuestionImportJob
from app.modules.ai.providers.factory import create_llm_provider
from app.modules.assembly import PaperAssemblyService
from app.modules.assembly.resumable import ResumablePaperAssemblyService
from app.modules.exports.service import ExportProcessor
from app.modules.questions import import_service as question_import_service
from app.modules.questions.import_schemas import ColumnMappingEntry, ImportRowResultOut
from app.modules.realtime.events import (
    PAPER_GENERATED,
    PAPER_GENERATION_FAILED,
    PAPER_GENERATION_PROGRESS,
    PAPER_QUESTION_READY,
    publish_paper_event,
    utc_timestamp,
)
from app.workers.celery_app import celery_app
from app.workers.idempotency import IdempotentTask

logger = logging.getLogger("papergen.workers")


def _queue_redis() -> Redis:
    return Redis.from_url(get_settings().QUEUE_REDIS_URL, decode_responses=True)


def _paper_events_redis() -> Redis:
    """Use the application Redis DB, keeping Celery's broker DB independent."""

    return Redis.from_url(get_settings().REDIS_URL, decode_responses=True)


def _enqueue_notification_event(
    *,
    event_id: uuid.UUID,
    event_type: str,
    org_id: uuid.UUID | str,
    resource_id: uuid.UUID | str,
    occurred_at: str,
    payload: dict[str, Any],
) -> None:
    """Hand an already-committed domain event to P6-T5's generic consumer."""

    try:
        celery_app.send_task(
            "papergen.workers.notification.consume_domain_event",
            kwargs={
                "org_id": str(org_id),
                "event_id": str(event_id),
                "event_type": event_type,
                "payload": {
                    **payload,
                    "resourceId": str(resource_id),
                    "occurredAt": occurred_at,
                },
            },
            queue="notifications",
        )
    except Exception as exc:  # noqa: BLE001 - assembly state is already committed
        logger.warning(
            "notification_event_enqueue_failed event_type=%s resource_id=%s error=%s",
            event_type,
            resource_id,
            type(exc).__name__,
        )


async def _load_slot_observation(
    *, org_id: uuid.UUID, run_id: uuid.UUID, slot_id: str
) -> dict[str, Any] | None:
    """Read the committed P4-T5 checkpoint state without mutating orchestration."""

    settings = get_settings()
    engine = create_engine(settings)
    factory = async_sessionmaker(engine, class_=AsyncSession, expire_on_commit=False)
    try:
        async with get_tenant_session(org_id, session_factory=factory) as session:
            slots = (
                await session.execute(
                    select(PaperAssemblySlot)
                    .where(
                        PaperAssemblySlot.org_id == org_id,
                        PaperAssemblySlot.run_id == run_id,
                    )
                    .order_by(PaperAssemblySlot.section_id, PaperAssemblySlot.position)
                )
            ).scalars().all()
            checkpoint = next((item for item in slots if item.slot_id == slot_id), None)
            if checkpoint is None:
                return None
            completed = sum(item.status == "completed" for item in slots)
            total = len(slots)
            return {
                "org_id": checkpoint.org_id,
                "paper_id": checkpoint.paper_id,
                "run_id": checkpoint.run_id,
                "slot_id": checkpoint.slot_id,
                "section_id": checkpoint.section_id,
                "position": checkpoint.position,
                "status": checkpoint.status,
                "attempt_count": checkpoint.attempt_count,
                "completed_slots": completed,
                "total_slots": total,
                "percent": round((completed / total) * 100) if total else 0,
                "question_id": checkpoint.question_id,
                "snapshot": checkpoint.snapshot,
                "last_error": checkpoint.last_error,
            }
    finally:
        await engine.dispose()


def _observe_slot_safely(
    *, org_id: uuid.UUID, run_id: uuid.UUID, slot_id: str
) -> dict[str, Any] | None:
    """Keep the non-authoritative observation path from controlling assembly."""

    try:
        return asyncio.run(
            _load_slot_observation(
                org_id=org_id,
                run_id=run_id,
                slot_id=slot_id,
            )
        )
    except Exception as exc:  # noqa: BLE001 - the assembly task must still run
        logger.warning(
            "paper_progress_observation_failed run_id=%s slot_id=%s error=%s",
            run_id,
            slot_id,
            type(exc).__name__,
        )
        return None


def _publish_slot_observation(
    observation: dict[str, Any] | None,
    *,
    status: str | None = None,
    retry_scheduled: bool = False,
    terminal_failure: bool = False,
) -> None:
    """Best-effort observation of durable slot state; never controls the job."""

    if observation is None:
        return
    event_status = status or observation["status"]
    timestamp = utc_timestamp()
    payload = {
        "jobId": str(observation["run_id"]),
        "paperId": str(observation["paper_id"]),
        "runId": str(observation["run_id"]),
        "slotId": observation["slot_id"],
        "sectionId": observation["section_id"],
        "position": observation["position"],
        "status": event_status,
        "timestamp": timestamp,
        "stage": "assembly",
        "percent": observation["percent"],
        "filledSlots": observation["completed_slots"],
        "totalSlots": observation["total_slots"],
        "attemptCount": observation["attempt_count"] + (event_status == "running"),
    }
    if observation.get("last_error"):
        payload["lastError"] = observation["last_error"]
    if retry_scheduled:
        payload["retryScheduled"] = True
    if terminal_failure:
        payload.update(
            {
                "message": "Question generation stopped after all retry attempts. Retry generation.",
                "retryable": True,
            }
        )

    event_id = uuid.uuid5(
        uuid.NAMESPACE_URL,
        f"papergen:{PAPER_GENERATION_FAILED}:{observation['run_id']}",
    )
    client = _paper_events_redis()
    try:
        publish_paper_event(
            client,
            event_type=(
                PAPER_GENERATION_FAILED
                if terminal_failure
                else PAPER_GENERATION_PROGRESS
            ),
            org_id=observation["org_id"],
            paper_id=observation["paper_id"],
            payload=payload,
            occurred_at=timestamp,
            event_id=event_id if terminal_failure else None,
        )
        if (
            not terminal_failure
            and event_status == "completed"
            and observation.get("snapshot") is not None
        ):
            publish_paper_event(
                client,
                event_type=PAPER_QUESTION_READY,
                org_id=observation["org_id"],
                paper_id=observation["paper_id"],
                payload={
                    "jobId": str(observation["run_id"]),
                    "paperId": str(observation["paper_id"]),
                    "runId": str(observation["run_id"]),
                    "sectionId": observation["section_id"],
                    "slotId": observation["slot_id"],
                    "status": "completed",
                    "timestamp": timestamp,
                    "question": observation["snapshot"],
                },
                occurred_at=timestamp,
            )
    except Exception as exc:  # noqa: BLE001 - progress transport is non-authoritative
        logger.warning(
            "paper_progress_publish_failed paper_id=%s slot_id=%s error=%s",
            observation["paper_id"],
            observation["slot_id"],
            type(exc).__name__,
        )
    finally:
        client.close()
    if terminal_failure:
        _enqueue_notification_event(
            event_id=event_id,
            event_type=PAPER_GENERATION_FAILED,
            org_id=observation["org_id"],
            resource_id=observation["paper_id"],
            occurred_at=timestamp,
            payload=payload,
        )


@celery_app.task(
    bind=True,
    base=IdempotentTask,
    name="papergen.workers.delivery_probe",
    acks_late=True,
    reject_on_worker_lost=True,
)
def delivery_probe(
    self: IdempotentTask,
    *,
    idempotency_key: str,
    marker: str,
    delay_seconds: float = 0.0,
    result_value: str = "completed",
) -> dict[str, Any]:
    """Record observable starts/completions around an interruptible delay."""

    redis_client = _queue_redis()
    marker_key = f"papergen:worker-probe:{marker}"
    attempt = int(redis_client.hincrby(marker_key, "starts", 1))
    key_digest = hashlib.sha256(idempotency_key.encode("utf-8")).hexdigest()[:12]
    redelivered = bool(self.request.delivery_info.get("redelivered", False))
    logger.warning(
        "worker_probe_started task_id=%s marker=%s attempt=%s redelivered=%s key_sha256=%s",
        self.request.id,
        marker,
        attempt,
        redelivered,
        key_digest,
    )

    if delay_seconds:
        time.sleep(delay_seconds)

    completions = int(redis_client.hincrby(marker_key, "completions", 1))
    redis_client.expire(marker_key, get_settings().TASK_IDEMPOTENCY_TTL_SECONDS)
    logger.warning(
        "worker_probe_completed task_id=%s marker=%s attempt=%s completions=%s",
        self.request.id,
        marker,
        attempt,
        completions,
    )
    return {
        "task_id": self.request.id,
        "marker": marker,
        "attempt": attempt,
        "completions": completions,
        "redelivered": redelivered,
        "value": result_value,
    }


@celery_app.task(
    name="papergen.workers.queue_keepalive",
    ignore_result=True,
)
def queue_keepalive() -> None:
    """Intentional queue traffic that advances Redis visibility restoration.

    Celery beat publishes this even when no user jobs exist.  Do not rely on
    Kombu's poll-loop callback: in Celery 5.6/Kombu 5.6 it may run only during
    consumer initialization.  The pulse explicitly invokes the Redis transport's
    visibility restoration check, bounding recovery by visibility timeout plus
    one keepalive interval and ordinary scheduling slack.
    """

    with celery_app.connection_for_read() as connection:
        channel = connection.channel()
        try:
            restore_visible = getattr(channel.qos, "restore_visible", None)
            if restore_visible is not None:
                restore_visible(interval=1)
        finally:
            channel.close()


async def _run_resumable_step(*, org_id: uuid.UUID, method: str, **kwargs: Any) -> Any:
    settings = get_settings()
    engine = create_engine(settings)
    factory = async_sessionmaker(engine, class_=AsyncSession, expire_on_commit=False)
    provider = create_llm_provider(settings)
    try:
        async with provider:
            async with get_tenant_session(org_id, session_factory=factory) as session:
                service = ResumablePaperAssemblyService(
                    session=session,
                    settings=settings,
                    org_id=org_id,
                    provider=provider,
                )
                return await getattr(service, method)(**kwargs)
    finally:
        await engine.dispose()


async def _record_slot_failure(
    *,
    org_id: uuid.UUID,
    run_id: uuid.UUID,
    slot_id: str,
    exc: Exception,
    terminal: bool = False,
) -> None:
    """Commit failed-attempt state after the slot transaction has rolled back."""

    settings = get_settings()
    engine = create_engine(settings)
    factory = async_sessionmaker(engine, class_=AsyncSession, expire_on_commit=False)
    try:
        async with get_tenant_session(org_id, session_factory=factory) as session:
            checkpoint = (
                await session.execute(
                    select(PaperAssemblySlot)
                    .where(
                        PaperAssemblySlot.org_id == org_id,
                        PaperAssemblySlot.run_id == run_id,
                        PaperAssemblySlot.slot_id == slot_id,
                    )
                    .with_for_update()
                )
            ).scalar_one_or_none()
            if checkpoint is not None and checkpoint.status != "completed":
                checkpoint.status = "failed" if terminal else "pending"
                checkpoint.attempt_count += 1
                checkpoint.last_error = f"{type(exc).__name__}: {exc}"[:2000]
                if terminal:
                    run = (
                        await session.execute(
                            select(PaperAssemblyRun)
                            .where(
                                PaperAssemblyRun.org_id == org_id,
                                PaperAssemblyRun.id == run_id,
                            )
                            .with_for_update()
                        )
                    ).scalar_one_or_none()
                    if run is not None and run.status != "completed":
                        run.status = "failed"
                        run.last_error = checkpoint.last_error
    finally:
        await engine.dispose()


@celery_app.task(
    bind=True,
    base=IdempotentTask,
    name="papergen.workers.prepare_paper",
    acks_late=True,
    reject_on_worker_lost=True,
)
def prepare_paper(
    self: IdempotentTask,
    *,
    idempotency_key: str,
    org_id: str,
    paper_id: str,
    label: str = "Generated Set A",
) -> dict[str, Any]:
    del idempotency_key
    try:
        run = asyncio.run(
            _run_resumable_step(
                org_id=uuid.UUID(org_id),
                method="prepare",
                paper_id=uuid.UUID(paper_id),
                label=label,
            )
        )
    except Exception as exc:
        raise self.retry(exc=exc, countdown=1, max_retries=get_settings().CELERY_SLOT_MAX_RETRIES)
    return {"run_id": str(run.id), "status": run.status}


@celery_app.task(
    bind=True,
    base=IdempotentTask,
    name="papergen.workers.process_paper_slot",
    acks_late=True,
    reject_on_worker_lost=True,
)
def process_paper_slot(
    self: IdempotentTask,
    *,
    idempotency_key: str,
    org_id: str,
    run_id: str,
    slot_id: str,
) -> dict[str, Any]:
    """Process exactly one slot; Celery retry re-enters only this checkpoint."""

    del idempotency_key
    parsed_org_id = uuid.UUID(org_id)
    parsed_run_id = uuid.UUID(run_id)
    observation = _observe_slot_safely(
        org_id=parsed_org_id,
        run_id=parsed_run_id,
        slot_id=slot_id,
    )
    _publish_slot_observation(observation, status="running")
    try:
        result = asyncio.run(
            _run_resumable_step(
                org_id=parsed_org_id,
                method="process_slot",
                run_id=parsed_run_id,
                slot_id=slot_id,
            )
        )
    except Exception as exc:
        logger.exception("paper_slot_failed run_id=%s slot_id=%s", run_id, slot_id)
        max_retries = get_settings().CELERY_SLOT_MAX_RETRIES
        retries_exhausted = self.request.retries >= max_retries
        asyncio.run(
            _record_slot_failure(
                org_id=parsed_org_id,
                run_id=parsed_run_id,
                slot_id=slot_id,
                exc=exc,
                terminal=retries_exhausted,
            )
        )
        observation = _observe_slot_safely(
            org_id=parsed_org_id,
            run_id=parsed_run_id,
            slot_id=slot_id,
        )
        _publish_slot_observation(
            observation,
            status="failed" if retries_exhausted else None,
            retry_scheduled=not retries_exhausted,
            terminal_failure=retries_exhausted,
        )
        raise self.retry(exc=exc, countdown=1, max_retries=max_retries)
    observation = _observe_slot_safely(
        org_id=parsed_org_id,
        run_id=parsed_run_id,
        slot_id=slot_id,
    )
    _publish_slot_observation(observation)
    return result


@celery_app.task(
    bind=True,
    base=IdempotentTask,
    name="papergen.workers.finalize_paper",
    acks_late=True,
    reject_on_worker_lost=True,
)
def finalize_paper(
    self: IdempotentTask,
    *,
    idempotency_key: str,
    org_id: str,
    run_id: str,
) -> dict[str, Any]:
    del idempotency_key
    try:
        result = asyncio.run(
            _run_resumable_step(
                org_id=uuid.UUID(org_id),
                method="finalize",
                run_id=uuid.UUID(run_id),
            )
        )
    except Exception as exc:
        raise self.retry(exc=exc, countdown=1, max_retries=get_settings().CELERY_SLOT_MAX_RETRIES)
    paper_id = result.get("paper_id")
    if paper_id is not None and result.get("state") == "completed":
        client = _paper_events_redis()
        timestamp = utc_timestamp()
        event_id = uuid.UUID(run_id)
        payload = {
            "jobId": run_id,
            "paperId": paper_id,
            "runId": run_id,
            "status": "completed",
            "timestamp": timestamp,
            "version": result["paper_version_id"],
            "counts": {"questions": result["selected_question_count"]},
            "warnings": [],
        }
        try:
            publish_paper_event(
                client,
                event_type=PAPER_GENERATED,
                org_id=org_id,
                paper_id=paper_id,
                payload=payload,
                occurred_at=timestamp,
                event_id=event_id,
            )
        except Exception as publish_exc:  # noqa: BLE001 - observation is best effort
            logger.warning(
                "paper_generated_publish_failed paper_id=%s error=%s",
                paper_id,
                type(publish_exc).__name__,
            )
        finally:
            client.close()
        _enqueue_notification_event(
            event_id=event_id,
            event_type=PAPER_GENERATED,
            org_id=org_id,
            resource_id=paper_id,
            occurred_at=timestamp,
            payload=payload,
        )
    return result


async def _dispatch_pending_outbox() -> dict[str, int]:
    settings = get_settings()
    engine = create_engine(settings)
    factory = async_sessionmaker(engine, class_=AsyncSession, expire_on_commit=False)
    published = 0
    failed = 0
    try:
        async with get_unscoped_session(session_factory=factory) as session:
            rows = (
                await session.execute(
                    select(TaskOutbox)
                    .where(
                        TaskOutbox.status == "pending",
                        TaskOutbox.available_at <= datetime.now(timezone.utc),
                    )
                    .order_by(TaskOutbox.created_at, TaskOutbox.id)
                    .limit(100)
                    .with_for_update(skip_locked=True)
                )
            ).scalars().all()
            for row in rows:
                row.publish_attempts += 1
                try:
                    task = celery_app.tasks[row.task_name]
                    task.apply_async(
                        task_id=str(row.id),
                        kwargs={**row.payload, "idempotency_key": row.idempotency_key},
                        queue="generation",
                    )
                    row.status = "published"
                    row.published_at = datetime.now(timezone.utc)
                    row.last_error = None
                    published += 1
                except Exception as exc:
                    row.last_error = f"{type(exc).__name__}: {exc}"[:2000]
                    failed += 1
        return {"published": published, "failed": failed}
    finally:
        await engine.dispose()


@celery_app.task(name="papergen.workers.dispatch_outbox", ignore_result=True)
def dispatch_outbox() -> dict[str, int]:
    """Reconcile committed outbox rows; safe after producer crashes (R-20)."""

    return asyncio.run(_dispatch_pending_outbox())


async def _assemble_paper(*, org_id: uuid.UUID, paper_id: uuid.UUID) -> dict[str, Any]:
    settings = get_settings()
    engine = create_engine(settings)
    factory = async_sessionmaker(
        engine,
        class_=AsyncSession,
        expire_on_commit=False,
    )
    provider = create_llm_provider(settings)
    try:
        async with provider:
            async with get_tenant_session(org_id, session_factory=factory) as session:
                result = await PaperAssemblyService(
                    session=session,
                    settings=settings,
                    org_id=org_id,
                    provider=provider,
                ).assemble(paper_id=paper_id)
                return {
                    "paper_id": str(result.paper_id),
                    "paper_version_id": str(result.paper_version_id),
                    "outcome": result.outcome,
                    "selected_question_count": result.selected_question_count,
                    "selected_marks": str(result.selected_marks),
                    "compromise_count": result.compromise_count,
                    "reused_existing_version": result.reused_existing_version,
                }
    finally:
        await engine.dispose()


@celery_app.task(
    bind=True,
    base=IdempotentTask,
    name="papergen.workers.assemble_paper",
    acks_late=True,
    reject_on_worker_lost=True,
)
def assemble_paper(
    self: IdempotentTask,
    *,
    idempotency_key: str,
    org_id: str,
    paper_id: str,
) -> dict[str, Any]:
    """Run P4-T4 atomically through P4-T1's idempotent Celery envelope."""

    del idempotency_key  # enforced by IdempotentTask; not business data
    return asyncio.run(
        _assemble_paper(org_id=uuid.UUID(org_id), paper_id=uuid.UUID(paper_id))
    )


def _question_import_counts(
    rows: list[ImportRowResultOut],
) -> tuple[int, int, int, int]:
    return (
        sum(row.status == "created" for row in rows),
        sum(row.status == "created_with_warnings" for row in rows),
        sum(row.status == "error" for row in rows),
        sum(row.status == "skipped_duplicate" for row in rows),
    )


async def _process_question_import(
    *, org_id: uuid.UUID, import_id: uuid.UUID
) -> dict[str, Any]:
    """Resume a durable question import in atomic, checkpointed bulk batches."""

    settings = get_settings()
    engine = create_engine(settings)
    factory = async_sessionmaker(engine, class_=AsyncSession, expire_on_commit=False)
    durable_rows: list[ImportRowResultOut] = []
    try:
        async with get_tenant_session(org_id, session_factory=factory) as session:
            job = (
                await session.execute(
                    select(QuestionImportJob)
                    .where(
                        QuestionImportJob.id == import_id,
                        QuestionImportJob.org_id == org_id,
                    )
                    .with_for_update()
                )
            ).scalar_one_or_none()
            if job is None:
                raise LookupError("question import job not found")
            if job.status == "completed":
                return {
                    "import_id": str(import_id),
                    "status": "completed",
                    "processed_rows": job.processed_rows,
                    "idempotent": True,
                }
            if job.file_bytes is None:
                raise RuntimeError("question import source file is no longer available")
            raw_bytes = bytes(job.file_bytes)
            content_type = job.content_type
            filename = job.filename
            requester_id = job.requested_by
            requester_role = job.requester_role
            request_ip = job.request_ip
            mapping = [
                ColumnMappingEntry.model_validate(entry)
                for entry in job.column_mapping
            ]
            durable_rows = [
                ImportRowResultOut.model_validate(row) for row in (job.row_results or [])
            ]
            job.status = "processing"
            job.started_at = job.started_at or datetime.now(timezone.utc)
            job.error = None

        async def checkpoint(
            session: AsyncSession,
            rows: list[ImportRowResultOut],
            total_rows: int,
        ) -> None:
            """Stage job progress in the same transaction as the batch inserts."""

            current = (
                await session.execute(
                    select(QuestionImportJob)
                    .where(
                        QuestionImportJob.id == import_id,
                        QuestionImportJob.org_id == org_id,
                    )
                    .with_for_update()
                )
            ).scalar_one()
            committed_rows = [
                ImportRowResultOut.model_validate(item)
                for item in (current.row_results or [])
            ]
            committed_rows.extend(rows)
            created, warned, errors, duplicates = _question_import_counts(
                committed_rows
            )
            current.total_rows = total_rows
            current.processed_rows = len(committed_rows)
            current.created_count = created
            current.created_with_warnings_count = warned
            current.error_count = errors
            current.duplicate_count = duplicates
            current.row_results = [
                item.model_dump(mode="json") for item in committed_rows
            ]

        scope = RequestScope(
            org_id=org_id,
            user_id=requester_id,
            role=requester_role,
        )
        new_rows = await question_import_service.process_import_bulk(
            scope=scope,
            org_id=org_id,
            raw_bytes=raw_bytes,
            content_type=content_type,
            filename=filename,
            column_mapping=mapping,
            ip=request_ip,
            start_index=len(durable_rows),
            session_factory=factory,
            progress_callback=checkpoint,
            batch_size=settings.QUESTION_IMPORT_BATCH_SIZE,
        )
        durable_rows.extend(new_rows)

        created, warned, errors, duplicates = _question_import_counts(durable_rows)
        async with get_tenant_session(org_id, session_factory=factory) as session:
            job = (
                await session.execute(
                    select(QuestionImportJob)
                    .where(
                        QuestionImportJob.id == import_id,
                        QuestionImportJob.org_id == org_id,
                    )
                    .with_for_update()
                )
            ).scalar_one()
            job.status = "completed"
            job.processed_rows = len(durable_rows)
            job.created_count = created
            job.created_with_warnings_count = warned
            job.error_count = errors
            job.duplicate_count = duplicates
            job.row_results = [row.model_dump(mode="json") for row in durable_rows]
            job.error_report_available = bool(warned or errors or duplicates)
            job.completed_at = datetime.now(timezone.utc)
            job.file_bytes = None
            job.error = None
        return {
            "import_id": str(import_id),
            "status": "completed",
            "processed_rows": len(durable_rows),
            "created_count": created,
            "created_with_warnings_count": warned,
            "error_count": errors,
            "duplicate_count": duplicates,
        }
    except Exception as exc:
        try:
            async with get_tenant_session(org_id, session_factory=factory) as session:
                job = (
                    await session.execute(
                        select(QuestionImportJob)
                        .where(
                            QuestionImportJob.id == import_id,
                            QuestionImportJob.org_id == org_id,
                        )
                        .with_for_update()
                    )
                ).scalar_one_or_none()
                if job is not None and job.status != "completed":
                    job.status = "failed"
                    job.error = f"{type(exc).__name__}: {exc}"[:2000]
                    job.completed_at = datetime.now(timezone.utc)
                    job.file_bytes = None
        except Exception:  # noqa: BLE001 - preserve the original worker failure
            logger.exception("question_import_failure_checkpoint_failed import_id=%s", import_id)
        raise
    finally:
        await engine.dispose()


@celery_app.task(
    bind=True,
    base=IdempotentTask,
    name="papergen.workers.process_question_import",
    acks_late=True,
    reject_on_worker_lost=True,
)
def process_question_import(
    self: IdempotentTask,
    *,
    idempotency_key: str,
    org_id: str,
    import_id: str,
) -> dict[str, Any]:
    del idempotency_key
    return asyncio.run(
        _process_question_import(
            org_id=uuid.UUID(org_id), import_id=uuid.UUID(import_id)
        )
    )


async def _process_export(*, org_id: uuid.UUID, export_id: uuid.UUID) -> dict[str, Any]:
    """Advance queued -> processing -> done without holding a DB transaction while rendering."""

    settings = get_settings()
    engine = create_engine(settings)
    factory = async_sessionmaker(engine, class_=AsyncSession, expire_on_commit=False)
    processor = ExportProcessor(settings=settings)
    try:
        async with get_tenant_session(org_id, session_factory=factory) as session:
            claimed = await processor.claim(
                session=session, org_id=org_id, export_id=export_id
            )
        if claimed is None:
            return {"export_id": str(export_id), "status": "done", "idempotent": True}
        job, request = claimed
        try:
            key, content, media_type = await asyncio.to_thread(
                processor.render_and_store, job=job, request=request
            )
            async with get_tenant_session(org_id, session_factory=factory) as session:
                completed = await processor.complete(
                    session=session,
                    org_id=org_id,
                    export_id=export_id,
                    key=key,
                    content=content,
                    media_type=media_type,
                )
            timestamp = utc_timestamp()
            _enqueue_notification_event(
                event_id=uuid.uuid5(
                    uuid.NAMESPACE_URL, f"papergen:export.ready:{export_id}"
                ),
                event_type="export.ready",
                org_id=org_id,
                resource_id=export_id,
                occurred_at=timestamp,
                payload={
                    "exportId": str(export_id),
                    "paperId": str(completed.paper_id),
                    "status": completed.status,
                },
            )
            return {
                "export_id": str(export_id),
                "status": completed.status,
                "object_key": completed.object_key,
                "byte_size": completed.byte_size,
                "sha256": completed.sha256,
            }
        except Exception as exc:
            async with get_tenant_session(org_id, session_factory=factory) as session:
                await processor.fail(
                    session=session,
                    org_id=org_id,
                    export_id=export_id,
                    error=f"{type(exc).__name__}: {exc}",
                )
            _enqueue_notification_event(
                event_id=uuid.uuid5(
                    uuid.NAMESPACE_URL, f"papergen:export.failed:{export_id}"
                ),
                event_type="export.failed",
                org_id=org_id,
                resource_id=export_id,
                occurred_at=utc_timestamp(),
                payload={
                    "exportId": str(export_id),
                    "status": "failed",
                },
            )
            raise
    finally:
        await engine.dispose()


@celery_app.task(
    bind=True,
    base=IdempotentTask,
    name="papergen.workers.render_export",
    acks_late=True,
    reject_on_worker_lost=True,
)
def render_export(
    self: IdempotentTask,
    *,
    idempotency_key: str,
    org_id: str,
    export_id: str,
) -> dict[str, Any]:
    del idempotency_key
    return asyncio.run(
        _process_export(org_id=uuid.UUID(org_id), export_id=uuid.UUID(export_id))
    )


__all__ = [
    "assemble_paper",
    "delivery_probe",
    "dispatch_outbox",
    "finalize_paper",
    "prepare_paper",
    "process_question_import",
    "process_paper_slot",
    "queue_keepalive",
    "render_export",
]
