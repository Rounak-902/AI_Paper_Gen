"""Catalogue-driven notification fan-out, aggregation, and read-state queries."""

from __future__ import annotations

import base64
import datetime as dt
import smtplib
import uuid
from dataclasses import dataclass
from email.message import EmailMessage
from typing import Any

from sqlalchemy import and_, func, or_, select, tuple_, update
from sqlalchemy.dialects.postgresql import insert
from sqlalchemy.ext.asyncio import AsyncSession

from app.common.errors import NotFoundError
from app.core.config import Settings
from app.models.approval import ApprovalRequestEvent
from app.models.export import ExportArtifact
from app.models.membership import Membership
from app.models.notification import (
    Notification,
    NotificationDigest,
    NotificationEventReceipt,
)
from app.models.paper import GeneratedPaper
from app.models.user import User
from app.modules.notifications import schemas

UTC = dt.timezone.utc

# Exact notification triggers currently supported from 01_Product_Requirements.md
# "Notifications" and 02_System_Architecture.md "Event Architecture".
EVENT_TO_CODE: dict[str, str] = {
    "paper.generated": "NT-3",
    "paper.submitted": "NT-4",
    "paper.approved": "NT-5",
    "paper.rejected": "NT-6",
    "paper.generation.failed": "NT-8",
    "export.ready": "NT-9",
    "export.failed": "NT-10",
    "question.extracted": "NT-12",
}

CHANNELS_BY_CODE: dict[str, tuple[str, ...]] = {
    "NT-3": ("in_app", "email"),
    "NT-4": ("in_app", "email"),
    "NT-5": ("in_app", "email"),
    "NT-6": ("in_app", "email"),
    "NT-8": ("in_app", "email"),
    "NT-9": ("in_app",),
    "NT-10": ("in_app",),
    "NT-12": ("in_app",),
}

CODE_TO_CLIENT_TYPE: dict[str, str] = {
    "NT-3": "job.completed",
    "NT-4": "approval.requested",
    "NT-5": "approval.decided",
    "NT-6": "approval.decided",
    "NT-8": "job.failed",
    "NT-9": "job.completed",
    "NT-10": "job.failed",
    "NT-12": "approval.requested",
}


@dataclass(frozen=True, slots=True)
class RenderedNotification:
    code: str
    title: str
    body: str
    link: str
    entity: str
    entity_id: uuid.UUID
    recipients: tuple[uuid.UUID, ...]


def _utcnow() -> dt.datetime:
    return dt.datetime.now(UTC)


def _dedup_key(code: str, event_id: uuid.UUID, channel: str) -> str:
    """One delivery per catalogue event, recipient, and channel; no time window."""

    return f"{code}:event:{event_id}:{channel}"


def _decode_cursor(value: str) -> tuple[dt.datetime, uuid.UUID]:
    try:
        decoded = base64.urlsafe_b64decode(value.encode("ascii")).decode("utf-8")
        raw_time, raw_id = decoded.rsplit("|", 1)
        return dt.datetime.fromisoformat(raw_time), uuid.UUID(raw_id)
    except (ValueError, UnicodeError) as exc:
        raise NotFoundError("Notification cursor is invalid.") from exc


def _encode_cursor(created_at: dt.datetime, row_id: uuid.UUID) -> str:
    raw = f"{created_at.isoformat()}|{row_id}".encode("utf-8")
    return base64.urlsafe_b64encode(raw).decode("ascii")


def _out(row: Notification) -> schemas.NotificationOut:
    return schemas.NotificationOut(
        id=row.id,
        code=row.code,
        type=CODE_TO_CLIENT_TYPE.get(row.code, "system.announcement"),
        title=row.title,
        body=row.body,
        link=row.link,
        entity=row.entity,
        entity_id=row.entity_id,
        read_at=row.read_at,
        created_at=row.created_at,
    )


async def list_notifications(
    session: AsyncSession,
    *,
    org_id: uuid.UUID,
    user_id: uuid.UUID,
    limit: int,
    cursor: str | None,
    unread_only: bool,
) -> schemas.NotificationPage:
    conditions = [
        Notification.org_id == org_id,
        Notification.user_id == user_id,
        Notification.channel == "in_app",
        Notification.status == "sent",
    ]
    if unread_only:
        conditions.append(Notification.read_at.is_(None))
    if cursor:
        cursor_time, cursor_id = _decode_cursor(cursor)
        conditions.append(
            tuple_(Notification.created_at, Notification.id)
            < tuple_(cursor_time, cursor_id)
        )
    rows = (
        await session.execute(
            select(Notification)
            .where(*conditions)
            .order_by(Notification.created_at.desc(), Notification.id.desc())
            .limit(limit + 1)
        )
    ).scalars().all()
    has_next = len(rows) > limit
    visible = rows[:limit]
    next_cursor = (
        _encode_cursor(visible[-1].created_at, visible[-1].id)
        if has_next and visible
        else None
    )
    return schemas.NotificationPage(
        items=[_out(row) for row in visible],
        next_cursor=next_cursor,
        has_next=has_next,
    )


async def unread_count(
    session: AsyncSession, *, org_id: uuid.UUID, user_id: uuid.UUID
) -> int:
    return int(
        (
            await session.execute(
                select(func.count(Notification.id)).where(
                    Notification.org_id == org_id,
                    Notification.user_id == user_id,
                    Notification.channel == "in_app",
                    Notification.status == "sent",
                    Notification.read_at.is_(None),
                )
            )
        ).scalar_one()
    )


async def mark_read(
    session: AsyncSession,
    *,
    org_id: uuid.UUID,
    user_id: uuid.UUID,
    notification_id: uuid.UUID,
) -> Notification:
    row = (
        await session.execute(
            select(Notification)
            .where(
                Notification.id == notification_id,
                Notification.org_id == org_id,
                Notification.user_id == user_id,
                Notification.channel == "in_app",
            )
            .with_for_update()
        )
    ).scalar_one_or_none()
    if row is None:
        raise NotFoundError("Notification not found.")
    if row.read_at is None:
        row.read_at = _utcnow()
    await session.flush()
    return row


async def mark_all_read(
    session: AsyncSession, *, org_id: uuid.UUID, user_id: uuid.UUID
) -> int:
    result = await session.execute(
        update(Notification)
        .where(
            Notification.org_id == org_id,
            Notification.user_id == user_id,
            Notification.channel == "in_app",
            Notification.read_at.is_(None),
        )
        .values(read_at=_utcnow())
    )
    return int(result.rowcount or 0)


async def _admin_recipient_ids(
    session: AsyncSession, org_id: uuid.UUID
) -> tuple[uuid.UUID, ...]:
    # NT-4/NT-12 say Coaching Admins. There is no designated-reviewer assignment
    # dimension yet, so no broader role is inferred here.
    rows = (
        await session.execute(
            select(Membership.user_id)
            .join(User, User.id == Membership.user_id)
            .where(
                Membership.org_id == org_id,
                Membership.role == "coaching_admin",
                Membership.status == "active",
                Membership.deleted_at.is_(None),
                User.status == "active",
                User.deleted_at.is_(None),
            )
        )
    ).scalars().all()
    return tuple(dict.fromkeys(rows))


async def _render_paper_event(
    session: AsyncSession,
    *,
    org_id: uuid.UUID,
    event_id: uuid.UUID,
    event_type: str,
    paper_id: uuid.UUID,
) -> RenderedNotification:
    paper = (
        await session.execute(
            select(GeneratedPaper).where(
                GeneratedPaper.id == paper_id,
                GeneratedPaper.org_id == org_id,
                GeneratedPaper.deleted_at.is_(None),
            )
        )
    ).scalar_one_or_none()
    if paper is None:
        raise NotFoundError("Notification event paper not found.")
    code = EVENT_TO_CODE[event_type]
    if event_type == "paper.submitted":
        return RenderedNotification(
            code=code,
            title=f"Approval pending: {paper.title}",
            body="A paper is ready for review.",
            link=f"/papers/{paper.id}?mode=review",
            entity="generated_papers",
            entity_id=paper.id,
            recipients=await _admin_recipient_ids(session, org_id),
        )
    copy = {
        "paper.generated": (
            f"Paper ready: {paper.title}",
            "Your generated paper is ready to preview.",
            f"/papers/{paper.id}",
        ),
        "paper.approved": (
            f"Paper approved: {paper.title}",
            "Your paper was approved and is ready to export.",
            f"/papers/{paper.id}?approval=approved",
        ),
        "paper.rejected": (
            f"Changes requested: {paper.title}",
            "Your paper was rejected. Open it to review the decision comments.",
            f"/papers/{paper.id}?approval=rejected",
        ),
        "paper.generation.failed": (
            f"Generation failed: {paper.title}",
            "Paper generation stopped after retries. Your blueprint is preserved.",
            f"/papers/{paper.id}/edit",
        ),
    }[event_type]
    return RenderedNotification(
        code=code,
        title=copy[0],
        body=copy[1],
        link=copy[2],
        entity="generated_papers",
        entity_id=paper.id,
        recipients=(paper.created_by,),
    )


async def _insert_deliveries(
    session: AsyncSession,
    *,
    org_id: uuid.UUID,
    event_id: uuid.UUID,
    rendered: RenderedNotification,
) -> list[uuid.UUID]:
    values: list[dict[str, Any]] = []
    for recipient_id in rendered.recipients:
        for channel in CHANNELS_BY_CODE[rendered.code]:
            values.append(
                {
                    "org_id": org_id,
                    "user_id": recipient_id,
                    "code": rendered.code,
                    "channel": channel,
                    "title": rendered.title,
                    "body": rendered.body,
                    "link": rendered.link,
                    "entity": rendered.entity,
                    "entity_id": rendered.entity_id,
                    "dedup_key": _dedup_key(rendered.code, event_id, channel),
                    "status": "sent" if channel == "in_app" else "pending",
                    "sent_at": _utcnow() if channel == "in_app" else None,
                }
            )
    if not values:
        return []
    await session.execute(
        insert(Notification)
        .values(values)
        .on_conflict_do_nothing(
            index_elements=[Notification.user_id, Notification.dedup_key]
        )
    )
    keys = [value["dedup_key"] for value in values if value["channel"] == "email"]
    return list(
        (
            await session.execute(
                select(Notification.id).where(
                    Notification.org_id == org_id,
                    Notification.channel == "email",
                    Notification.dedup_key.in_(keys),
                    Notification.status.in_(("pending", "failed")),
                )
            )
        ).scalars()
    )


async def _render_export_event(
    session: AsyncSession,
    *,
    org_id: uuid.UUID,
    event_type: str,
    export_id: uuid.UUID,
) -> RenderedNotification:
    row = (
        await session.execute(
            select(ExportArtifact, GeneratedPaper.title)
            .join(GeneratedPaper, GeneratedPaper.id == ExportArtifact.paper_id)
            .where(
                ExportArtifact.id == export_id,
                ExportArtifact.org_id == org_id,
            )
        )
    ).one_or_none()
    if row is None:
        raise NotFoundError("Notification event export not found.")
    artifact, paper_title = row
    ready = event_type == "export.ready"
    return RenderedNotification(
        code=EVENT_TO_CODE[event_type],
        title=(
            f"Export ready: {paper_title}"
            if ready
            else f"Export failed: {paper_title}"
        ),
        body=(
            "Your export is ready to download."
            if ready
            else "The export could not be rendered. Open the paper to retry."
        ),
        link=(
            f"/papers/{artifact.paper_id}?export={artifact.id}"
            if ready
            else f"/papers/{artifact.paper_id}"
        ),
        entity="export_artifacts",
        entity_id=artifact.id,
        recipients=(artifact.requested_by,),
    )


def _event_time(payload: dict[str, Any]) -> dt.datetime:
    value = payload.get("occurredAt")
    if isinstance(value, str):
        parsed = dt.datetime.fromisoformat(value.replace("Z", "+00:00"))
        return parsed.astimezone(UTC)
    return _utcnow()


def _window(value: dt.datetime, cadence_seconds: int) -> tuple[dt.datetime, dt.datetime]:
    stamp = int(value.timestamp())
    start_stamp = stamp - (stamp % cadence_seconds)
    start = dt.datetime.fromtimestamp(start_stamp, tz=UTC)
    return start, start + dt.timedelta(seconds=cadence_seconds)


async def _aggregate_question_review(
    session: AsyncSession,
    *,
    org_id: uuid.UUID,
    payload: dict[str, Any],
    cadence_seconds: int,
) -> None:
    start, end = _window(_event_time(payload), cadence_seconds)
    for recipient_id in await _admin_recipient_ids(session, org_id):
        await session.execute(
            insert(NotificationDigest)
            .values(
                org_id=org_id,
                user_id=recipient_id,
                code="NT-12",
                aggregation_key=f"question-review:{org_id}",
                window_start=start,
                window_end=end,
                event_count=1,
                status="open",
            )
            .on_conflict_do_update(
                index_elements=[
                    NotificationDigest.user_id,
                    NotificationDigest.code,
                    NotificationDigest.aggregation_key,
                    NotificationDigest.window_start,
                ],
                set_={
                    "event_count": NotificationDigest.event_count + 1,
                    "updated_at": _utcnow(),
                },
            )
        )


async def process_receipt(
    session: AsyncSession,
    *,
    org_id: uuid.UUID,
    receipt_id: uuid.UUID,
    cadence_seconds: int,
) -> list[uuid.UUID]:
    """Lock one receipt through fan-out; concurrent redelivery waits then no-ops."""

    receipt = (
        await session.execute(
            select(NotificationEventReceipt)
            .where(
                NotificationEventReceipt.id == receipt_id,
                NotificationEventReceipt.org_id == org_id,
            )
            .with_for_update()
        )
    ).scalar_one_or_none()
    if receipt is None:
        raise NotFoundError("Notification event receipt not found.")
    if receipt.status in {"completed", "ignored"}:
        return []
    if receipt.event_type not in EVENT_TO_CODE:
        receipt.status = "ignored"
        receipt.completed_at = _utcnow()
        return []
    if receipt.event_type == "question.extracted":
        await _aggregate_question_review(
            session,
            org_id=org_id,
            payload=receipt.payload,
            cadence_seconds=cadence_seconds,
        )
        receipt.status = "completed"
        receipt.completed_at = _utcnow()
        receipt.last_error = None
        return []

    if receipt.source == "approval_request_events":
        event = (
            await session.execute(
                select(ApprovalRequestEvent).where(
                    ApprovalRequestEvent.id == receipt.source_event_id,
                    ApprovalRequestEvent.org_id == org_id,
                )
            )
        ).scalar_one_or_none()
        if event is None:
            raise NotFoundError("Approval notification source event not found.")
        paper_id = event.paper_id
    else:
        resource_id = receipt.payload.get("resourceId")
        resource_uuid = uuid.UUID(str(resource_id))
        paper_id = resource_uuid
    if receipt.event_type.startswith("export."):
        rendered = await _render_export_event(
            session,
            org_id=org_id,
            event_type=receipt.event_type,
            export_id=resource_uuid,
        )
    else:
        rendered = await _render_paper_event(
            session,
            org_id=org_id,
            event_id=receipt.source_event_id,
            event_type=receipt.event_type,
            paper_id=paper_id,
        )
    email_ids = await _insert_deliveries(
        session,
        org_id=org_id,
        event_id=receipt.source_event_id,
        rendered=rendered,
    )
    receipt.status = "completed"
    receipt.completed_at = _utcnow()
    receipt.last_error = None
    return email_ids


async def accept_domain_event(
    session: AsyncSession,
    *,
    org_id: uuid.UUID,
    event_id: uuid.UUID,
    event_type: str,
    payload: dict[str, Any],
) -> uuid.UUID:
    """Persist the generic post-commit subscriber envelope idempotently."""

    await session.execute(
        insert(NotificationEventReceipt)
        .values(
            org_id=org_id,
            source="domain_event",
            source_event_id=event_id,
            event_type=event_type,
            payload=payload,
            status="pending",
        )
        .on_conflict_do_nothing(
            index_elements=[
                NotificationEventReceipt.source,
                NotificationEventReceipt.source_event_id,
            ]
        )
    )
    return (
        await session.execute(
            select(NotificationEventReceipt.id).where(
                NotificationEventReceipt.source == "domain_event",
                NotificationEventReceipt.source_event_id == event_id,
                NotificationEventReceipt.org_id == org_id,
            )
        )
    ).scalar_one()


async def flush_digest(
    session: AsyncSession,
    *,
    org_id: uuid.UUID,
    digest_id: uuid.UUID,
    threshold: int,
) -> list[uuid.UUID]:
    """Lock one aggregate bucket through durable notification creation (R-18)."""

    digest = (
        await session.execute(
            select(NotificationDigest)
            .where(
                NotificationDigest.id == digest_id,
                NotificationDigest.org_id == org_id,
            )
            .with_for_update()
        )
    ).scalar_one_or_none()
    if digest is None or digest.status == "delivered":
        return []
    if digest.window_end > _utcnow() or digest.event_count < threshold:
        return []
    rendered = RenderedNotification(
        code="NT-12",
        title="Questions awaiting review",
        body=f"{digest.event_count} new questions are awaiting review.",
        link="/questions?status=draft",
        entity="question_bank",
        entity_id=digest.id,
        recipients=(digest.user_id,),
    )
    email_ids = await _insert_deliveries(
        session,
        org_id=org_id,
        event_id=digest.id,
        rendered=rendered,
    )
    digest.status = "delivered"
    digest.delivered_at = _utcnow()
    return email_ids


def _smtp_send(settings: Settings, *, recipient: str, subject: str, body: str) -> None:
    message = EmailMessage()
    message["From"] = settings.SMTP_FROM_EMAIL
    message["To"] = recipient
    message["Subject"] = subject
    message.set_content(body)
    with smtplib.SMTP(
        settings.SMTP_HOST, settings.SMTP_PORT, timeout=settings.SMTP_TIMEOUT_SECONDS
    ) as client:
        if settings.SMTP_STARTTLS:
            client.starttls()
        if settings.SMTP_USERNAME:
            client.login(settings.SMTP_USERNAME, settings.SMTP_PASSWORD or "")
        client.send_message(message)


async def send_email(
    session: AsyncSession,
    *,
    org_id: uuid.UUID,
    notification_id: uuid.UUID,
    settings: Settings,
) -> tuple[bool, str | None]:
    """Hold a database row lock across SMTP so concurrent workers cannot double-send."""

    row = (
        await session.execute(
            select(Notification, User.email)
            .join(User, User.id == Notification.user_id)
            .where(
                Notification.id == notification_id,
                Notification.org_id == org_id,
                Notification.channel == "email",
            )
            .with_for_update(of=Notification)
        )
    ).one_or_none()
    if row is None:
        return True, None
    notification, address = row
    if notification.status == "sent":
        return True, None
    notification.attempt_count += 1
    try:
        import asyncio

        await asyncio.to_thread(
            _smtp_send,
            settings,
            recipient=str(address),
            subject=notification.title,
            body=notification.body or notification.title,
        )
    except Exception as exc:  # noqa: BLE001 - persisted and retried by Celery
        error = f"{type(exc).__name__}: {exc}"[:2000]
        notification.status = "failed"
        notification.last_error = error
        return False, error
    notification.status = "sent"
    notification.sent_at = _utcnow()
    notification.last_error = None
    return True, None


__all__ = [
    "CODE_TO_CLIENT_TYPE",
    "CHANNELS_BY_CODE",
    "EVENT_TO_CODE",
    "accept_domain_event",
    "flush_digest",
    "list_notifications",
    "mark_all_read",
    "mark_read",
    "process_receipt",
    "send_email",
    "unread_count",
]
