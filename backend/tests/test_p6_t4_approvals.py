"""P6-T4 approval lifecycle, DB BR-6 backstop, diff, and answer-key auth."""

from __future__ import annotations

import hashlib
import uuid
from datetime import datetime, timezone
from decimal import Decimal

import pytest
from httpx import ASGITransport, AsyncClient
from sqlalchemy import text
from sqlalchemy.exc import IntegrityError

from app.common.deps import OrgContext, RequestScope, get_db, get_org_context, get_scope
from app.common.errors import ConflictError
from app.main import app
from app.models.export import ExportArtifact
from app.models.paper import GeneratedPaper, PaperVersion
from app.models.template import Template
from app.models.user import User
from app.modules.approvals.schemas import (
    ApprovalSubmitIn,
    ApprovalTransitionIn,
)
from app.modules.approvals.service import ApprovalService, _shallow_diff


def _content(stem: str = "Original stem") -> dict:
    return {
        "schemaVersion": 1,
        "sections": [
            {
                "id": "section-a",
                "title": "Section A",
                "questions": [
                    {
                        "id": "question-1",
                        "slotId": "slot-1",
                        "sectionId": "section-a",
                        "stem": stem,
                        "marks": "2",
                    }
                ],
            }
        ],
        "assembly": {
            "solverOutcome": "feasible",
            "coverage": [
                {
                    "dimension": "marks",
                    "target": "paper",
                    "targetValue": "2",
                    "actual": "2",
                    "satisfied": True,
                }
            ],
            "compromises": [],
        },
    }


async def _paper_version_artifacts(
    db_session,
    tenant,
    *,
    title: str,
    label: str = "v1",
    stem: str = "Original stem",
):
    template = Template(
        org_id=tenant.org_id,
        name=f"{title} template {uuid.uuid4()}",
        layout={"schemaVersion": 1},
        created_by=tenant.user_id,
        status="active",
    )
    paper = GeneratedPaper(
        org_id=tenant.org_id,
        created_by=tenant.user_id,
        blueprint={"totalMarks": "2"},
        title=title,
        total_marks=Decimal("2"),
        duration_min=30,
        status="draft",
    )
    db_session.add_all([template, paper])
    await db_session.flush()
    version = PaperVersion(
        paper_id=paper.id,
        label=label,
        content=_content(stem),
        answer_key={"questions": [{"slotId": "slot-1", "answer": "2"}]},
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
        byte_size=128,
        sha256=hashlib.sha256(b"artifact").hexdigest(),
        completed_at=datetime.now(timezone.utc),
    )
    paper_artifact = ExportArtifact(
        **common, artifact_type="paper", object_key=f"{version.id}/paper.docx"
    )
    key_artifact = ExportArtifact(
        **common, artifact_type="answer_key", object_key=f"{version.id}/key.docx"
    )
    db_session.add_all([paper_artifact, key_artifact])
    await db_session.flush()
    return paper, version, paper_artifact, key_artifact


@pytest.mark.db
async def test_real_lifecycle_and_new_row_after_rejection(db_session, tenant) -> None:
    reviewer = User(email=f"reviewer-{uuid.uuid4()}@example.com", full_name="Reviewer")
    db_session.add(reviewer)
    await db_session.flush()
    paper, version, paper_artifact, key_artifact = await _paper_version_artifacts(
        db_session, tenant, title="Approval happy path"
    )
    author_scope = RequestScope(
        org_id=tenant.org_id, user_id=tenant.user_id, role="teacher"
    )
    reviewer_scope = RequestScope(
        org_id=tenant.org_id, user_id=reviewer.id, role="coaching_admin"
    )
    submitted = await ApprovalService(db_session, author_scope).submit(
        paper_id=paper.id,
        version_id=version.id,
        payload=ApprovalSubmitIn(
            paper_export_artifact_id=paper_artifact.id,
            answer_key_export_artifact_id=key_artifact.id,
            accept_coverage_report=True,
        ),
    )
    assert submitted.status == "submitted"
    assert submitted.submitted_at is not None
    submitted_row = await ApprovalService(db_session, author_scope).repo.request(
        submitted.id
    )
    assert submitted_row is not None
    assert submitted_row.coverage_report_snapshot == version.content["assembly"]
    assert len(submitted_row.coverage_report_sha256) == 64
    await db_session.refresh(paper)
    assert paper.status == "submitted"

    reviewing = await ApprovalService(db_session, reviewer_scope).start_review(
        submitted.id, ApprovalTransitionIn(row_version=submitted.row_version)
    )
    assert reviewing.status == "review" and reviewing.review_started_at is not None
    approved = await ApprovalService(db_session, reviewer_scope).decide(
        submitted.id,
        ApprovalTransitionIn(row_version=reviewing.row_version, comment="Reviewed"),
        decision="approved",
    )
    assert approved.status == "approved" and approved.decided_at is not None
    await db_session.refresh(paper)
    assert paper.status == "approved"
    approved_history = await ApprovalService(db_session, author_scope).history(
        submitted.id
    )
    assert [event.action for event in approved_history.events] == [
        "submitted",
        "review_started",
        "approved",
    ]
    assert all(event.occurred_at is not None for event in approved_history.events)
    withdrawn = await ApprovalService(db_session, reviewer_scope).withdraw_approval(
        submitted.id,
        ApprovalTransitionIn(
            row_version=approved.row_version, comment="Administrative withdrawal"
        ),
    )
    assert withdrawn.status == "approved"
    assert withdrawn.row_version == approved.row_version + 1
    await db_session.refresh(paper)
    assert paper.status == "draft"
    paper_version_after_withdrawal = paper.row_version
    withdrawn_history = await ApprovalService(db_session, author_scope).history(
        submitted.id
    )
    assert withdrawn_history.events[-1].action == "approval_withdrawn"
    assert withdrawn_history.events[-1].to_status == "draft"

    async def override_db():
        yield db_session

    app.dependency_overrides[get_scope] = lambda: reviewer_scope
    app.dependency_overrides[get_org_context] = lambda: OrgContext(
        org_id=reviewer_scope.org_id,
        user_id=reviewer_scope.user_id,
        role=reviewer_scope.role,
        roles=(reviewer_scope.role,),
    )
    app.dependency_overrides[get_db] = override_db
    try:
        async with AsyncClient(
            transport=ASGITransport(app=app), base_url="http://test"
        ) as client:
            repeated_response = await client.post(
                f"/api/v1/approval-requests/{submitted.id}/withdraw-approval",
                json={
                    "rowVersion": withdrawn.row_version,
                    "comment": "This second withdrawal must be rejected",
                },
            )
    finally:
        app.dependency_overrides.clear()
    assert repeated_response.status_code == 409, repeated_response.text
    assert repeated_response.json()["error"]["code"] == "APPROVAL_ALREADY_WITHDRAWN"

    with pytest.raises(ConflictError) as repeated:
        await ApprovalService(db_session, reviewer_scope).withdraw_approval(
            submitted.id,
            ApprovalTransitionIn(
                row_version=withdrawn.row_version,
                comment="This second withdrawal must be rejected",
            ),
        )
    assert repeated.value.code == "APPROVAL_ALREADY_WITHDRAWN"
    repeated_history = await ApprovalService(db_session, author_scope).history(
        submitted.id
    )
    assert [event.action for event in repeated_history.events].count(
        "approval_withdrawn"
    ) == 1
    await db_session.refresh(paper)
    assert paper.row_version == paper_version_after_withdrawal

    # Separate paper for the rejection loop: its old request remains terminal, and
    # resubmission uses a new immutable version and a new request row.
    rejected_paper, rejected_v1, p1, k1 = await _paper_version_artifacts(
        db_session, tenant, title="Approval rejection path"
    )
    first = await ApprovalService(db_session, author_scope).submit(
        paper_id=rejected_paper.id,
        version_id=rejected_v1.id,
        payload=ApprovalSubmitIn(
            paper_export_artifact_id=p1.id,
            answer_key_export_artifact_id=k1.id,
            accept_coverage_report=True,
        ),
    )
    first_review = await ApprovalService(db_session, reviewer_scope).start_review(
        first.id, ApprovalTransitionIn(row_version=first.row_version)
    )
    rejected = await ApprovalService(db_session, reviewer_scope).decide(
        first.id,
        ApprovalTransitionIn(
            row_version=first_review.row_version, comment="Regenerate slot 1"
        ),
        decision="rejected",
    )
    assert rejected.status == "rejected"
    await db_session.refresh(rejected_paper)
    assert rejected_paper.status == "rejected"

    revised = PaperVersion(
        paper_id=rejected_paper.id,
        label="v2",
        content=_content("Deliberately regenerated stem"),
        answer_key=rejected_v1.answer_key,
    )
    db_session.add(revised)
    await db_session.flush()
    common = dict(
        org_id=tenant.org_id,
        paper_id=rejected_paper.id,
        paper_version_id=revised.id,
        template_id=p1.template_id,
        requested_by=tenant.user_id,
        format="docx",
        status="done",
        media_type=p1.media_type,
        byte_size=128,
        sha256=p1.sha256,
        completed_at=datetime.now(timezone.utc),
    )
    p2 = ExportArtifact(**common, artifact_type="paper", object_key=f"{revised.id}/paper.docx")
    k2 = ExportArtifact(**common, artifact_type="answer_key", object_key=f"{revised.id}/key.docx")
    db_session.add_all([p2, k2])
    await db_session.flush()
    second = await ApprovalService(db_session, author_scope).submit(
        paper_id=rejected_paper.id,
        version_id=revised.id,
        payload=ApprovalSubmitIn(
            paper_export_artifact_id=p2.id,
            answer_key_export_artifact_id=k2.id,
            accept_coverage_report=True,
        ),
    )
    assert second.id != first.id
    assert second.paper_version_id != first.paper_version_id


@pytest.mark.db
async def test_database_rejects_submitter_as_approver(db_session, tenant) -> None:
    reviewer = User(email=f"db-reviewer-{uuid.uuid4()}@example.com", full_name="DB Reviewer")
    db_session.add(reviewer)
    await db_session.flush()
    paper, version, paper_artifact, key_artifact = await _paper_version_artifacts(
        db_session, tenant, title="DB BR-6 attack"
    )
    author_scope = RequestScope(org_id=tenant.org_id, user_id=tenant.user_id, role="teacher")
    reviewer_scope = RequestScope(org_id=tenant.org_id, user_id=reviewer.id, role="coaching_admin")
    submitted = await ApprovalService(db_session, author_scope).submit(
        paper_id=paper.id,
        version_id=version.id,
        payload=ApprovalSubmitIn(
            paper_export_artifact_id=paper_artifact.id,
            answer_key_export_artifact_id=key_artifact.id,
            accept_coverage_report=True,
        ),
    )
    reviewing = await ApprovalService(db_session, reviewer_scope).start_review(
        submitted.id, ApprovalTransitionIn(row_version=submitted.row_version)
    )

    with pytest.raises(IntegrityError) as rejected:
        async with db_session.begin_nested():
            await db_session.execute(
                text(
                    "UPDATE approval_requests SET status='approved', "
                    "approver_id=:submitter, decided_at=now(), row_version=row_version+1 "
                    "WHERE id=:request_id"
                ),
                {"submitter": tenant.user_id, "request_id": submitted.id},
            )
    assert "approver_differs_from_submitter" in str(rejected.value.orig)
    await db_session.refresh(await ApprovalService(db_session, reviewer_scope).repo.request(submitted.id))
    assert reviewing.status == "review"


def test_shallow_diff_surfaces_deliberate_slot_change() -> None:
    paper_id = uuid.uuid4()
    base = PaperVersion(id=uuid.uuid4(), paper_id=paper_id, label="v1", content=_content())
    target = PaperVersion(
        id=uuid.uuid4(),
        paper_id=paper_id,
        label="v2",
        content=_content("Deliberately regenerated stem"),
    )
    result = _shallow_diff(paper_id, base, target, "explicit")
    assert result.changed_slots[0].slot_id == "slot-1"
    assert result.changed_slots[0].changed_fields == ["stem"]


@pytest.mark.db
async def test_version_diff_endpoint_surfaces_deliberate_slot_change(
    db_session, tenant
) -> None:
    paper, base, _paper_artifact, _key_artifact = await _paper_version_artifacts(
        db_session, tenant, title="Version diff endpoint"
    )
    target = PaperVersion(
        paper_id=paper.id,
        label="v2",
        content=_content("Deliberately regenerated stem"),
        answer_key=base.answer_key,
    )
    db_session.add(target)
    await db_session.flush()
    scope = RequestScope(org_id=tenant.org_id, user_id=tenant.user_id, role="teacher")

    async def override_db():
        yield db_session

    app.dependency_overrides[get_scope] = lambda: scope
    app.dependency_overrides[get_db] = override_db
    try:
        async with AsyncClient(
            transport=ASGITransport(app=app), base_url="http://test"
        ) as client:
            response = await client.get(
                f"/api/v1/papers/{paper.id}/versions/{target.id}/diff",
                params={"baseVersionId": str(base.id)},
            )
    finally:
        app.dependency_overrides.clear()

    assert response.status_code == 200, response.text
    diff = response.json()["data"]
    assert diff["basis"] == "explicit"
    assert diff["changedSlots"] == [
        {
            "slotId": "slot-1",
            "sectionId": "section-a",
            "changedFields": ["stem"],
        }
    ]


@pytest.mark.db
async def test_teacher_gets_real_403_for_answer_key_but_can_get_paper(
    db_session, tenant
) -> None:
    _paper, _version, paper_artifact, key_artifact = await _paper_version_artifacts(
        db_session, tenant, title="Answer key authorization"
    )
    scope = RequestScope(org_id=tenant.org_id, user_id=tenant.user_id, role="teacher")

    async def override_db():
        yield db_session

    app.dependency_overrides[get_scope] = lambda: scope
    app.dependency_overrides[get_db] = override_db
    try:
        async with AsyncClient(
            transport=ASGITransport(app=app), base_url="http://test"
        ) as client:
            paper_response = await client.get(
                f"/api/v1/papers/exports/{paper_artifact.id}"
            )
            key_response = await client.get(
                f"/api/v1/papers/exports/{key_artifact.id}"
            )
            key_download_response = await client.post(
                f"/api/v1/papers/exports/{key_artifact.id}/download"
            )
    finally:
        app.dependency_overrides.clear()

    assert paper_response.status_code == 200, paper_response.text
    assert key_response.status_code == 403, key_response.text
    assert key_response.json()["error"]["code"] == "FORBIDDEN"
    assert key_download_response.status_code == 403, key_download_response.text
    assert key_download_response.json()["error"]["code"] == "FORBIDDEN"
