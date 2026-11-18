"""Celery adapters for P6-T5's durable notification inbox and channel fan-out."""

from __future__ import annotations

import asyncio
import uuid
from datetime import datetime, timedelta, timezone
from typing import Any

from sqlalchemy import select, text
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from app.core.config import get_settings
from app.db.engine import create_engine
from app.db.session import get_tenant_session, get_unscoped_session
from app.models.notification import NotificationEventReceipt
from app.modules.notifications import service
from app.workers.celery_app import celery_app


def _factory(engine) -> async_sessionmaker[AsyncSession]:
    return async_sessionmaker(engine, class_=AsyncSession, expire_on_commit=False)


async def _claim_work() -> tuple[list[tuple[str, str]], list[tuple[str, str, str]]]:
    settings = get_settings()
    engine = create_engine(settings)
    factory = _factory(engine)
    try:
        async with get_unscoped_session(session_factory=factory) as session:
            approval_rows = (
                await session.execute(
                    text(
                        "SELECT receipt_id,org_id FROM "
                        "public.claim_approval_notification_events(:limit,:max_attempts)"
                    ),
                    {
                        "limit": 100,
                        "max_attempts": settings.NOTIFICATION_EVENT_MAX_RETRIES + 1,
                    },
                )
            ).all()
            work_rows = (
                await session.execute(
                    text(
                        "SELECT work_kind,work_id,org_id FROM "
                        "public.pending_notification_work(:limit,:threshold)"
                    ),
                    {
                        "limit": 100,
                        "threshold": settings.NOTIFICATION_QUESTION_REVIEW_THRESHOLD,
                    },
                )
            ).all()
        return (
            [(str(row.receipt_id), str(row.org_id)) for row in approval_rows],
            [
                (str(row.work_kind), str(row.work_id), str(row.org_id))
                for row in work_rows
            ],
        )
    finally:
        await engine.dispose()


async def _process_receipt(*, org_id: uuid.UUID, receipt_id: uuid.UUID) -> list[str]:
    settings = get_settings()
    engine = create_engine(settings)
    factory = _factory(engine)
    try:
        async with get_tenant_session(org_id, session_factory=factory) as session:
            email_ids = await service.process_receipt(
                session,
                org_id=org_id,
                receipt_id=receipt_id,
                cadence_seconds=settings.NOTIFICATION_DIGEST_CADENCE_SECONDS,
            )
        return [str(value) for value in email_ids]
    finally:
        await engine.dispose()


async def _fail_receipt(
    *, org_id: uuid.UUID, receipt_id: uuid.UUID, error: str
) -> None:
    settings = get_settings()
    engine = create_engine(settings)
    factory = _factory(engine)
    try:
        async with get_tenant_session(org_id, session_factory=factory) as session:
            row = (
                await session.execute(
                    select(NotificationEventReceipt)
                    .where(
                        NotificationEventReceipt.id == receipt_id,
                        NotificationEventReceipt.org_id == org_id,
                    )
                    .with_for_update()
                )
            ).scalar_one_or_none()
            if row is not None and row.status not in {"completed", "ignored"}:
                row.status = "failed"
                row.attempt_count += 1
                row.available_at = datetime.now(timezone.utc) + timedelta(seconds=2)
                row.last_error = error[:2000]
    finally:
        await engine.dispose()


def _enqueue_emails(org_id: str, email_ids: list[str]) -> None:
    for email_id in email_ids:
        send_notification_email.apply_async(
            kwargs={"org_id": org_id, "notification_id": email_id},
            queue="notifications",
        )


@celery_app.task(
    bind=True,
    name="papergen.workers.notification.consume_receipt",
    acks_late=True,
    reject_on_worker_lost=True,
)
def consume_notification_receipt(
    self,
    *,
    org_id: str,
    receipt_id: str,
) -> dict[str, Any]:
    parsed_org = uuid.UUID(org_id)
    parsed_receipt = uuid.UUID(receipt_id)
    try:
        email_ids = asyncio.run(
            _process_receipt(org_id=parsed_org, receipt_id=parsed_receipt)
        )
    except Exception as exc:
        asyncio.run(
            _fail_receipt(
                org_id=parsed_org,
                receipt_id=parsed_receipt,
                error=f"{type(exc).__name__}: {exc}",
            )
        )
        raise self.retry(
            exc=exc,
            countdown=2,
            max_retries=get_settings().NOTIFICATION_EVENT_MAX_RETRIES,
        )
    _enqueue_emails(org_id, email_ids)
    return {"receipt_id": receipt_id, "email_ids": email_ids}


async def _accept_and_process_domain_event(
    *,
    org_id: uuid.UUID,
    event_id: uuid.UUID,
    event_type: str,
    payload: dict[str, Any],
) -> list[str]:
    settings = get_settings()
    engine = create_engine(settings)
    factory = _factory(engine)
    try:
        async with get_tenant_session(org_id, session_factory=factory) as session:
            receipt_id = await service.accept_domain_event(
                session,
                org_id=org_id,
                event_id=event_id,
                event_type=event_type,
                payload=payload,
            )
            email_ids = await service.process_receipt(
                session,
                org_id=org_id,
                receipt_id=receipt_id,
                cadence_seconds=settings.NOTIFICATION_DIGEST_CADENCE_SECONDS,
            )
        return [str(value) for value in email_ids]
    finally:
        await engine.dispose()


@celery_app.task(
    bind=True,
    name="papergen.workers.notification.consume_domain_event",
    acks_late=True,
    reject_on_worker_lost=True,
)
def consume_domain_event(
    self,
    *,
    org_id: str,
    event_id: str,
    event_type: str,
    payload: dict[str, Any],
) -> dict[str, Any]:
    try:
        email_ids = asyncio.run(
            _accept_and_process_domain_event(
                org_id=uuid.UUID(org_id),
                event_id=uuid.UUID(event_id),
                event_type=event_type,
                payload=payload,
            )
        )
    except Exception as exc:
        raise self.retry(
            exc=exc,
            countdown=2,
            max_retries=get_settings().NOTIFICATION_EVENT_MAX_RETRIES,
        )
    _enqueue_emails(org_id, email_ids)
    return {"event_id": event_id, "email_ids": email_ids}


async def _flush_digest(*, org_id: uuid.UUID, digest_id: uuid.UUID) -> list[str]:
    settings = get_settings()
    engine = create_engine(settings)
    factory = _factory(engine)
    try:
        async with get_tenant_session(org_id, session_factory=factory) as session:
            ids = await service.flush_digest(
                session,
                org_id=org_id,
                digest_id=digest_id,
                threshold=settings.NOTIFICATION_QUESTION_REVIEW_THRESHOLD,
            )
        return [str(value) for value in ids]
    finally:
        await engine.dispose()


@celery_app.task(
    bind=True,
    name="papergen.workers.notification.flush_digest",
    acks_late=True,
    reject_on_worker_lost=True,
)
def flush_notification_digest(
    self, *, org_id: str, digest_id: str
) -> dict[str, Any]:
    try:
        email_ids = asyncio.run(
            _flush_digest(org_id=uuid.UUID(org_id), digest_id=uuid.UUID(digest_id))
        )
    except Exception as exc:
        raise self.retry(
            exc=exc,
            countdown=2,
            max_retries=get_settings().NOTIFICATION_EVENT_MAX_RETRIES,
        )
    _enqueue_emails(org_id, email_ids)
    return {"digest_id": digest_id, "email_ids": email_ids}


async def _send_email(
    *, org_id: uuid.UUID, notification_id: uuid.UUID
) -> tuple[bool, str | None]:
    settings = get_settings()
    engine = create_engine(settings)
    factory = _factory(engine)
    try:
        async with get_tenant_session(org_id, session_factory=factory) as session:
            return await service.send_email(
                session,
                org_id=org_id,
                notification_id=notification_id,
                settings=settings,
            )
    finally:
        await engine.dispose()


@celery_app.task(
    bind=True,
    name="papergen.workers.notification.send_email",
    acks_late=True,
    reject_on_worker_lost=True,
)
def send_notification_email(
    self, *, org_id: str, notification_id: str
) -> dict[str, Any]:
    sent, error = asyncio.run(
        _send_email(
            org_id=uuid.UUID(org_id), notification_id=uuid.UUID(notification_id)
        )
    )
    if not sent:
        raise self.retry(
            exc=RuntimeError(error or "SMTP delivery failed"),
            countdown=2,
            max_retries=get_settings().NOTIFICATION_EMAIL_MAX_RETRIES,
        )
    return {"notification_id": notification_id, "status": "sent"}


@celery_app.task(
    name="papergen.workers.notification.dispatch",
    ignore_result=True,
)
def dispatch_notifications() -> dict[str, int]:
    approvals, work = asyncio.run(_claim_work())
    for receipt_id, org_id in approvals:
        consume_notification_receipt.apply_async(
            kwargs={"org_id": org_id, "receipt_id": receipt_id},
            queue="notifications",
        )
    for work_kind, work_id, org_id in work:
        task = (
            flush_notification_digest
            if work_kind == "digest"
            else send_notification_email
        )
        key = "digest_id" if work_kind == "digest" else "notification_id"
        task.apply_async(
            kwargs={"org_id": org_id, key: work_id}, queue="notifications"
        )
    return {"approval_events": len(approvals), "pending_work": len(work)}


__all__ = [
    "consume_domain_event",
    "consume_notification_receipt",
    "dispatch_notifications",
    "flush_notification_digest",
    "send_notification_email",
]
