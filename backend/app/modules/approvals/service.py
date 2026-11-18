"""Approval state machine over immutable paper versions and export artifacts."""

from __future__ import annotations

import hashlib
import json
import uuid
from datetime import datetime, timezone
from typing import Any

from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from app.common.audit_log import record_audit
from app.common.deps import RequestScope
from app.common.errors import BusinessRuleError, ConflictError, NotFoundError
from app.models.approval import ApprovalComment, ApprovalRequest, ApprovalRequestEvent
from app.models.paper import PaperVersion
from app.modules.approvals import schemas
from app.modules.approvals.repository import ApprovalRepository


def _request_out(row: ApprovalRequest) -> schemas.ApprovalRequestOut:
    return schemas.ApprovalRequestOut.model_validate(row)


def _coverage_hash(snapshot: dict[str, Any]) -> str:
    encoded = json.dumps(
        snapshot, sort_keys=True, separators=(",", ":"), ensure_ascii=False
    ).encode("utf-8")
    return hashlib.sha256(encoded).hexdigest()


def _event(
    session: AsyncSession,
    *,
    row: ApprovalRequest,
    actor_id: uuid.UUID,
    action: str,
    from_status: str | None,
    to_status: str,
    comment: str | None = None,
) -> ApprovalRequestEvent:
    event = ApprovalRequestEvent(
        org_id=row.org_id,
        approval_request_id=row.id,
        paper_id=row.paper_id,
        paper_version_id=row.paper_version_id,
        actor_id=actor_id,
        action=action,
        from_status=from_status,
        to_status=to_status,
        comment=comment,
        # PostgreSQL now() is transaction-stable; explicit wall-clock timestamps
        # preserve transition order when submit/review/decide share one transaction.
        occurred_at=datetime.now(timezone.utc),
    )
    session.add(event)
    return event


class ApprovalService:
    def __init__(self, session: AsyncSession, scope: RequestScope) -> None:
        self.session = session
        self.scope = scope
        self.repo = ApprovalRepository(session, scope.org_id)

    async def submit(
        self,
        *,
        paper_id: uuid.UUID,
        version_id: uuid.UUID,
        payload: schemas.ApprovalSubmitIn,
    ) -> schemas.ApprovalRequestOut:
        await self.repo.lock_paper(paper_id)
        paper = await self.repo.paper(paper_id)
        version = await self.repo.version(paper_id, version_id)
        if paper is None or version is None:
            raise NotFoundError("Paper version not found.")
        if paper.created_by != self.scope.user_id:
            raise BusinessRuleError(
                "Only the paper author can submit this version for approval.",
                code="APPROVAL_SUBMITTER_NOT_AUTHOR",
            )
        if paper.status not in {"draft", "rejected"}:
            raise BusinessRuleError(
                f"A paper in {paper.status} state cannot be submitted.",
                code="INVALID_APPROVAL_TRANSITION",
            )

        assembly = version.content.get("assembly")
        if not isinstance(assembly, dict):
            raise BusinessRuleError(
                "This version has no persisted coverage report.",
                code="COVERAGE_REPORT_REQUIRED",
            )
        if not isinstance(assembly.get("coverage"), list) or not isinstance(
            assembly.get("compromises"), list
        ):
            raise BusinessRuleError(
                "This version has no complete persisted coverage report.",
                code="COVERAGE_REPORT_REQUIRED",
            )
        if str(assembly.get("solverOutcome", "")).lower() in {
            "infeasible",
            "failed",
            "unknown",
            "",
        }:
            raise BusinessRuleError(
                "The persisted coverage report does not pass the submission gate.",
                code="BLUEPRINT_RECONCILIATION_FAILED",
            )

        paper_artifact = await self.repo.artifact(payload.paper_export_artifact_id)
        answer_artifact = await self.repo.artifact(
            payload.answer_key_export_artifact_id
        )
        self._validate_artifact(
            paper_artifact, paper_id, version_id, expected_type="paper"
        )
        self._validate_artifact(
            answer_artifact, paper_id, version_id, expected_type="answer_key"
        )

        row = ApprovalRequest(
            org_id=self.scope.org_id,
            paper_id=paper_id,
            paper_version_id=version_id,
            paper_export_artifact_id=payload.paper_export_artifact_id,
            answer_key_export_artifact_id=payload.answer_key_export_artifact_id,
            submitted_by=self.scope.user_id,
            status="submitted",
            separation_of_duties_required=await self.repo.separation_required(),
            coverage_report_snapshot=assembly,
            coverage_report_sha256=_coverage_hash(assembly),
        )
        self.session.add(row)
        try:
            await self.session.flush()
        except IntegrityError as exc:
            raise ConflictError(
                "This paper version was already submitted, or the paper already has a pending review.",
                code="APPROVAL_REQUEST_CONFLICT",
            ) from exc
        _event(
            self.session,
            row=row,
            actor_id=self.scope.user_id,
            action="submitted",
            from_status="draft",
            to_status="submitted",
        )
        record_audit(
            self.session,
            org_id=self.scope.org_id,
            actor_id=self.scope.user_id,
            action="approval.submitted",
            entity="approval_requests",
            entity_id=row.id,
            meta={
                "paperId": str(paper_id),
                "paperVersionId": str(version_id),
                "coverageReportSha256": row.coverage_report_sha256,
            },
        )
        return _request_out(row)

    @staticmethod
    def _validate_artifact(
        artifact,
        paper_id: uuid.UUID,
        version_id: uuid.UUID,
        *,
        expected_type: str,
    ) -> None:
        if (
            artifact is None
            or artifact.paper_id != paper_id
            or artifact.paper_version_id != version_id
            or artifact.artifact_type != expected_type
            or artifact.status != "done"
        ):
            raise BusinessRuleError(
                f"A completed {expected_type} export for this exact version is required.",
                code="APPROVAL_ARTIFACT_REQUIRED",
            )

    async def start_review(
        self, request_id: uuid.UUID, payload: schemas.ApprovalTransitionIn
    ) -> schemas.ApprovalRequestOut:
        row = await self._transition_source(request_id, payload.row_version)
        if row.status != "submitted":
            self._invalid(row, "review")
        self._enforce_separation(row)
        old = row.status
        row.status = "review"
        row.review_started_by = self.scope.user_id
        row.review_started_at = datetime.now(timezone.utc)
        row.row_version += 1
        await self.session.flush()
        _event(
            self.session,
            row=row,
            actor_id=self.scope.user_id,
            action="review_started",
            from_status=old,
            to_status=row.status,
            comment=payload.comment,
        )
        self._audit(row, "approval.review_started")
        return _request_out(row)

    async def decide(
        self,
        request_id: uuid.UUID,
        payload: schemas.ApprovalTransitionIn,
        *,
        decision: str,
    ) -> schemas.ApprovalRequestOut:
        row = await self._transition_source(request_id, payload.row_version)
        if row.status != "review":
            self._invalid(row, decision)
        self._enforce_separation(row)
        if decision == "rejected" and payload.comment is None:
            raise BusinessRuleError(
                "A rejection comment is required.", code="REJECTION_COMMENT_REQUIRED"
            )
        old = row.status
        row.status = decision
        row.approver_id = self.scope.user_id
        row.decided_at = datetime.now(timezone.utc)
        row.decision_comment = payload.comment
        row.row_version += 1
        await self.session.flush()
        _event(
            self.session,
            row=row,
            actor_id=self.scope.user_id,
            action=decision,
            from_status=old,
            to_status=decision,
            comment=payload.comment,
        )
        self._audit(row, f"approval.{decision}")
        return _request_out(row)

    async def withdraw_approval(
        self, request_id: uuid.UUID, payload: schemas.ApprovalTransitionIn
    ) -> schemas.ApprovalRequestOut:
        row = await self.repo.request(request_id)
        if row is None:
            raise NotFoundError("Approval request not found.")
        await self.repo.lock_paper(row.paper_id)
        if await self.repo.has_event(request_id, "approval_withdrawn"):
            raise ConflictError(
                "This approval has already been withdrawn.",
                code="APPROVAL_ALREADY_WITHDRAWN",
            )
        if row.row_version != payload.row_version:
            raise ConflictError("The approval request changed; reload and retry.")
        if row.status != "approved":
            raise BusinessRuleError(
                "Only an approved decision can be withdrawn.",
                code="INVALID_APPROVAL_TRANSITION",
            )
        paper = await self.repo.paper(row.paper_id)
        if paper is None:
            raise NotFoundError("Paper not found.")
        # The request remains approved and terminal. Withdrawal is a new append-only
        # event; only the mutable aggregate returns to draft, preserving the decision.
        paper.status = "draft"
        paper.row_version += 1
        row.row_version += 1
        _event(
            self.session,
            row=row,
            actor_id=self.scope.user_id,
            action="approval_withdrawn",
            from_status="approved",
            to_status="draft",
            comment=payload.comment,
        )
        self._audit(row, "approval.withdrawn")
        await self.session.flush()
        return _request_out(row)

    async def add_comment(
        self, request_id: uuid.UUID, payload: schemas.ApprovalCommentIn
    ) -> schemas.ApprovalCommentOut:
        row = await self.repo.request(request_id)
        if row is None:
            raise NotFoundError("Approval request not found.")
        if row.status not in {"submitted", "review"}:
            raise BusinessRuleError(
                "Comments cannot be added after the review round is decided.",
                code="APPROVAL_REQUEST_TERMINAL",
            )
        if payload.slot_id is not None:
            version = await self.repo.version(row.paper_id, row.paper_version_id)
            if version is None or payload.slot_id not in _slot_map(version.content):
                raise BusinessRuleError(
                    "The comment slot does not exist in the reviewed version.",
                    code="APPROVAL_SLOT_NOT_FOUND",
                )
        comment = ApprovalComment(
            org_id=self.scope.org_id,
            approval_request_id=row.id,
            paper_version_id=row.paper_version_id,
            created_by=self.scope.user_id,
            slot_id=payload.slot_id,
            body=payload.body,
        )
        self.session.add(comment)
        await self.session.flush()
        _event(
            self.session,
            row=row,
            actor_id=self.scope.user_id,
            action="question_commented",
            from_status=row.status,
            to_status=row.status,
            comment=payload.body,
        )
        self._audit(row, "approval.question_commented")
        return schemas.ApprovalCommentOut.model_validate(comment)

    async def history(self, request_id: uuid.UUID) -> schemas.ApprovalHistoryOut:
        row = await self.repo.request(request_id)
        if row is None:
            raise NotFoundError("Approval request not found.")
        return schemas.ApprovalHistoryOut(
            request=_request_out(row),
            events=[
                schemas.ApprovalEventOut.model_validate(item)
                for item in await self.repo.events(row.id)
            ],
            comments=[
                schemas.ApprovalCommentOut.model_validate(item)
                for item in await self.repo.comments(row.id)
            ],
        )

    async def list_for_paper(
        self, paper_id: uuid.UUID
    ) -> list[schemas.ApprovalRequestOut]:
        if await self.repo.paper(paper_id) is None:
            raise NotFoundError("Paper not found.")
        return [_request_out(row) for row in await self.repo.requests_for_paper(paper_id)]

    async def queue(self) -> list[schemas.ApprovalRequestOut]:
        return [_request_out(row) for row in await self.repo.queue()]

    async def diff(
        self,
        *,
        paper_id: uuid.UUID,
        target_version_id: uuid.UUID,
        base_version_id: uuid.UUID | None,
    ) -> schemas.PaperVersionDiffOut:
        target = await self.repo.version(paper_id, target_version_id)
        if target is None or await self.repo.paper(paper_id) is None:
            raise NotFoundError("Paper version not found.")
        basis = "explicit"
        if base_version_id is not None:
            base = await self.repo.version(paper_id, base_version_id)
            if base is None:
                raise NotFoundError("Base paper version not found.")
        else:
            base = await self.repo.approved_baseline(paper_id, target)
            basis = "last-approved"
            if base is None:
                base = await self.repo.preceding_version(paper_id, target)
                basis = "immediately-preceding" if base is not None else "none"
        return _shallow_diff(paper_id, base, target, basis)

    async def _transition_source(
        self, request_id: uuid.UUID, expected_row_version: int
    ) -> ApprovalRequest:
        row = await self.repo.request(request_id)
        if row is None:
            raise NotFoundError("Approval request not found.")
        await self.repo.lock_paper(row.paper_id)
        if row.row_version != expected_row_version:
            raise ConflictError("The approval request changed; reload and retry.")
        return row

    def _enforce_separation(self, row: ApprovalRequest) -> None:
        if (
            row.separation_of_duties_required
            and row.submitted_by == self.scope.user_id
        ):
            raise BusinessRuleError(
                "BR-6 requires the submitter and reviewer/approver to be different identities.",
                code="SEPARATION_OF_DUTIES",
                status_code=403,
            )

    @staticmethod
    def _invalid(row: ApprovalRequest, target: str) -> None:
        raise BusinessRuleError(
            f"Approval request cannot transition from {row.status} to {target}.",
            code="INVALID_APPROVAL_TRANSITION",
        )

    def _audit(self, row: ApprovalRequest, action: str) -> None:
        record_audit(
            self.session,
            org_id=self.scope.org_id,
            actor_id=self.scope.user_id,
            action=action,
            entity="approval_requests",
            entity_id=row.id,
            meta={
                "paperId": str(row.paper_id),
                "paperVersionId": str(row.paper_version_id),
                "status": row.status,
            },
        )


def _slot_map(content: dict[str, Any]) -> dict[str, tuple[str | None, dict[str, Any]]]:
    slots: dict[str, tuple[str | None, dict[str, Any]]] = {}
    sections = content.get("sections", [])
    if not isinstance(sections, list):
        return slots
    for section in sections:
        if not isinstance(section, dict):
            continue
        section_id = str(section.get("id") or section.get("sectionId") or "") or None
        questions = section.get("questions", [])
        if not isinstance(questions, list):
            continue
        for question in questions:
            if not isinstance(question, dict):
                continue
            slot_id = question.get("slotId") or question.get("slot_id") or question.get("id")
            if slot_id is not None:
                slots[str(slot_id)] = (section_id, question)
    return slots


def _section_map(content: dict[str, Any]) -> dict[str, dict[str, Any]]:
    result: dict[str, dict[str, Any]] = {}
    sections = content.get("sections", [])
    if not isinstance(sections, list):
        return result
    for section in sections:
        if isinstance(section, dict):
            section_id = section.get("id") or section.get("sectionId")
            if section_id is not None:
                result[str(section_id)] = section
    return result


def _changed_fields(
    before: dict[str, Any], after: dict[str, Any], *, exclude: set[str]
) -> list[str]:
    return sorted(
        key
        for key in set(before) | set(after)
        if key not in exclude and before.get(key) != after.get(key)
    )


def _shallow_diff(
    paper_id: uuid.UUID,
    base: PaperVersion | None,
    target: PaperVersion,
    basis: str,
) -> schemas.PaperVersionDiffOut:
    before_slots = _slot_map(base.content) if base is not None else {}
    after_slots = _slot_map(target.content)
    before_ids, after_ids = set(before_slots), set(after_slots)
    changed_slots = []
    for slot_id in sorted(before_ids & after_ids):
        before_section, before = before_slots[slot_id]
        after_section, after = after_slots[slot_id]
        fields = _changed_fields(
            before,
            after,
            exclude={"id", "slotId", "slot_id", "sectionId", "section_id"},
        )
        if before_section != after_section:
            fields.append("sectionId")
        if fields:
            changed_slots.append(
                schemas.SlotChangeOut(
                    slot_id=slot_id,
                    section_id=after_section,
                    changed_fields=sorted(set(fields)),
                )
            )

    before_sections = _section_map(base.content) if base is not None else {}
    after_sections = _section_map(target.content)
    before_section_ids, after_section_ids = set(before_sections), set(after_sections)
    changed_sections = []
    for section_id in sorted(before_section_ids & after_section_ids):
        fields = _changed_fields(
            before_sections[section_id],
            after_sections[section_id],
            exclude={"id", "sectionId", "questions"},
        )
        if fields:
            changed_sections.append(
                schemas.SectionChangeOut(
                    section_id=section_id, changed_fields=fields
                )
            )

    return schemas.PaperVersionDiffOut(
        paper_id=paper_id,
        base_version_id=base.id if base is not None else None,
        target_version_id=target.id,
        basis=basis,
        added_slot_ids=sorted(after_ids - before_ids),
        removed_slot_ids=sorted(before_ids - after_ids),
        changed_slots=changed_slots,
        added_section_ids=sorted(after_section_ids - before_section_ids),
        removed_section_ids=sorted(before_section_ids - after_section_ids),
        changed_sections=changed_sections,
    )


__all__ = ["ApprovalService"]
