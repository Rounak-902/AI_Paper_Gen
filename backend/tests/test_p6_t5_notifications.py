"""P6-T5 durable event consumption, deduplication, digest, and feed contract."""

from __future__ import annotations

import hashlib
import uuid
from datetime import datetime, timedelta, timezone
from decimal import Decimal

import pytest
from sqlalchemy import func, select, text

from app.common.deps import RequestScope
from app.core.config import get_settings
from app.models.export import ExportArtifact
from app.models.membership import Membership
from app.models.notification import Notification, NotificationDigest
from app.models.paper import GeneratedPaper, PaperVersion
from app.models.template import Template
from app.models.user import User
from app.modules.approvals.schemas import ApprovalSubmitIn
from app.modules.approvals.service import ApprovalService
from app.modules.notifications import service

pytestmark = pytest.mark.db


def _paper_content() -> dict:
    return {
        "schemaVersion": 1,
        "sections": [],
        "assembly": {
            "solverOutcome": "feasible",
            "coverage": [],
            "compromises": [],
        },
    }


async def _approval_fixture(db_session, tenant):
    admin = User(
        email=f"notifications-admin-{uuid.uuid4()}@example.com",
        full_name="Notification Reviewer",
    )
    template = Template(
        org_id=tenant.org_id,
        name=f"Notification template {uuid.uuid4()}",
        layout={"schemaVersion": 1},
        created_by=tenant.user_id,
        status="active",
    )
    paper = GeneratedPaper(
        org_id=tenant.org_id,
        created_by=tenant.user_id,
        blueprint={"totalMarks": "2"},
        title="P6-T5 approval event proof",
        total_marks=Decimal("2"),
        status="draft",
    )
    db_session.add_all([admin, template, paper])
    await db_session.flush()
    db_session.add(
        Membership(
            user_id=admin.id,
            org_id=tenant.org_id,
            role="coaching_admin",
            status="active",
        )
    )
    version = PaperVersion(
        paper_id=paper.id,
        label="v1",
        content=_paper_content(),
        answer_key={"questions": []},
    )
    db_session.add(version)
    await db_session.flush()
    common = dict(
        org_id=tenant.org_id,
        paper_id=paper.id,
        paper_version_id=version.id,
        template_id=template.id,
        requested_by=tenant.user_id,
        format="docx",
        status="done",
        media_type="application/vnd.openxmlformats-officedocument.wordprocessingml.document",
        byte_size=32,
        sha256=hashlib.sha256(b"p6-t5").hexdigest(),
        completed_at=datetime.now(timezone.utc),
    )
    paper_export = ExportArtifact(
        **common, artifact_type="paper", object_key=f"{version.id}/paper.docx"
    )
    answer_export = ExportArtifact(
        **common,
        artifact_type="answer_key",
        object_key=f"{version.id}/answer-key.docx",
    )
    db_session.add_all([paper_export, answer_export])
    await db_session.flush()
    return admin, paper, version, paper_export, answer_export


async def test_real_approval_transition_creates_one_delivery_per_channel_and_dedups(
    db_session, tenant
) -> None:
    admin, paper, version, paper_export, answer_export = await _approval_fixture(
        db_session, tenant
    )
    submitted = await ApprovalService(
        db_session,
        RequestScope(
            org_id=tenant.org_id, user_id=tenant.user_id, role="teacher"
        ),
    ).submit(
        paper_id=paper.id,
        version_id=version.id,
        payload=ApprovalSubmitIn(
            paper_export_artifact_id=paper_export.id,
            answer_key_export_artifact_id=answer_export.id,
            accept_coverage_report=True,
        ),
    )
    await db_session.commit()

    claimed = (
        await db_session.execute(
            text(
                "SELECT receipt_id,org_id FROM "
                "public.claim_approval_notification_events(100)"
            )
        )
    ).one()
    assert claimed.org_id == tenant.org_id
    email_ids = await service.process_receipt(
        db_session,
        org_id=tenant.org_id,
        receipt_id=claimed.receipt_id,
        cadence_seconds=300,
    )
    assert len(email_ids) == 1
    await db_session.flush()

    # Redelivering the exact event is a no-op after taking the same receipt lock.
    assert (
        await service.process_receipt(
            db_session,
            org_id=tenant.org_id,
            receipt_id=claimed.receipt_id,
            cadence_seconds=300,
        )
        == []
    )
    rows = (
        await db_session.execute(
            select(Notification)
            .where(Notification.user_id == admin.id)
            .order_by(Notification.channel)
        )
    ).scalars().all()
    assert [(row.code, row.channel) for row in rows] == [
        ("NT-4", "email"),
        ("NT-4", "in_app"),
    ]
    assert rows[0].dedup_key.endswith(":email")
    assert rows[1].dedup_key.endswith(":in_app")
    assert submitted.status == "submitted"


async def test_email_failure_does_not_roll_back_in_app(db_session, tenant) -> None:
    user = await db_session.get(User, tenant.user_id)
    assert user is not None
    event_id = uuid.uuid4()
    paper = GeneratedPaper(
        org_id=tenant.org_id,
        created_by=tenant.user_id,
        blueprint={},
        title="Email isolation",
        status="draft",
    )
    db_session.add(paper)
    await db_session.flush()
    receipt_id = await service.accept_domain_event(
        db_session,
        org_id=tenant.org_id,
        event_id=event_id,
        event_type="paper.generation.failed",
        payload={"resourceId": str(paper.id)},
    )
    email_ids = await service.process_receipt(
        db_session,
        org_id=tenant.org_id,
        receipt_id=receipt_id,
        cadence_seconds=300,
    )
    assert len(email_ids) == 1
    in_app = (
        await db_session.execute(
            select(Notification).where(
                Notification.user_id == tenant.user_id,
                Notification.channel == "in_app",
                Notification.code == "NT-8",
            )
        )
    ).scalar_one()
    assert in_app.status == "sent"
    sent, error = await service.send_email(
        db_session,
        org_id=tenant.org_id,
        notification_id=email_ids[0],
        settings=get_settings().model_copy(
            update={"SMTP_PORT": 1, "SMTP_TIMEOUT_SECONDS": 0.1}
        ),
    )
    assert sent is False
    assert error and "Error" in error
    assert in_app.status == "sent"


async def test_export_ready_uses_catalogue_in_app_only_channel(
    db_session, tenant
) -> None:
    _admin, _paper, _version, paper_export, _answer_export = await _approval_fixture(
        db_session, tenant
    )
    receipt_id = await service.accept_domain_event(
        db_session,
        org_id=tenant.org_id,
        event_id=uuid.uuid4(),
        event_type="export.ready",
        payload={"resourceId": str(paper_export.id)},
    )
    assert (
        await service.process_receipt(
            db_session,
            org_id=tenant.org_id,
            receipt_id=receipt_id,
            cadence_seconds=300,
        )
        == []
    )
    rows = (
        await db_session.execute(
            select(Notification).where(
                Notification.user_id == tenant.user_id,
                Notification.code == "NT-9",
            )
        )
    ).scalars().all()
    assert len(rows) == 1
    assert rows[0].channel == "in_app"
    assert rows[0].link == f"/papers/{paper_export.paper_id}?export={paper_export.id}"


async def test_question_events_collapse_into_one_due_digest(db_session, tenant) -> None:
    admin = User(
        email=f"digest-admin-{uuid.uuid4()}@example.com", full_name="Digest Admin"
    )
    db_session.add(admin)
    await db_session.flush()
    db_session.add(
        Membership(
            user_id=admin.id,
            org_id=tenant.org_id,
            role="coaching_admin",
            status="active",
        )
    )
    occurred = datetime.now(timezone.utc) - timedelta(minutes=10)
    for _ in range(12):
        receipt_id = await service.accept_domain_event(
            db_session,
            org_id=tenant.org_id,
            event_id=uuid.uuid4(),
            event_type="question.extracted",
            payload={"occurredAt": occurred.isoformat()},
        )
        await service.process_receipt(
            db_session,
            org_id=tenant.org_id,
            receipt_id=receipt_id,
            cadence_seconds=300,
        )
    digest = (
        await db_session.execute(
            select(NotificationDigest).where(NotificationDigest.user_id == admin.id)
        )
    ).scalar_one()
    assert digest.event_count == 12
    email_ids = await service.flush_digest(
        db_session,
        org_id=tenant.org_id,
        digest_id=digest.id,
        threshold=10,
    )
    assert email_ids == []
    assert (
        await service.flush_digest(
            db_session,
            org_id=tenant.org_id,
            digest_id=digest.id,
            threshold=10,
        )
        == []
    )
    count = (
        await db_session.execute(
            select(func.count(Notification.id)).where(
                Notification.user_id == admin.id, Notification.code == "NT-12"
            )
        )
    ).scalar_one()
    assert count == 1  # NT-12 is in-app only in the catalogue, not twelve records


def test_catalogue_mapping_is_closed_to_documented_current_sources() -> None:
    assert service.EVENT_TO_CODE == {
        "paper.generated": "NT-3",
        "paper.submitted": "NT-4",
        "paper.approved": "NT-5",
        "paper.rejected": "NT-6",
        "paper.generation.failed": "NT-8",
        "export.ready": "NT-9",
        "export.failed": "NT-10",
        "question.extracted": "NT-12",
    }
    assert "paper.review_started" not in service.EVENT_TO_CODE
    assert "paper.approval_withdrawn" not in service.EVENT_TO_CODE
