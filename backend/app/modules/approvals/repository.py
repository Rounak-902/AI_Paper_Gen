"""Tenant-scoped persistence for P6-T4 approvals."""

from __future__ import annotations

import uuid

from sqlalchemy import select, text
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.approval import ApprovalComment, ApprovalRequest, ApprovalRequestEvent
from app.models.export import ExportArtifact
from app.models.organization import Organization
from app.models.paper import GeneratedPaper, PaperVersion


class ApprovalRepository:
    def __init__(self, session: AsyncSession, org_id: uuid.UUID) -> None:
        self.session = session
        self.org_id = org_id

    async def lock_paper(self, paper_id: uuid.UUID) -> None:
        # Reuses P6-T1's transaction-scoped advisory-lock pattern. Including the
        # organization in the key keeps tenant operations in disjoint namespaces.
        await self.session.execute(
            text("SELECT pg_advisory_xact_lock(hashtextextended(:lock_key, 0))"),
            {"lock_key": f"{self.org_id}:approval:{paper_id}"},
        )

    async def paper(self, paper_id: uuid.UUID) -> GeneratedPaper | None:
        return (
            await self.session.execute(
                select(GeneratedPaper).where(
                    GeneratedPaper.id == paper_id,
                    GeneratedPaper.org_id == self.org_id,
                    GeneratedPaper.deleted_at.is_(None),
                )
            )
        ).scalar_one_or_none()

    async def version(
        self, paper_id: uuid.UUID, version_id: uuid.UUID
    ) -> PaperVersion | None:
        return (
            await self.session.execute(
                select(PaperVersion).where(
                    PaperVersion.id == version_id, PaperVersion.paper_id == paper_id
                )
            )
        ).scalar_one_or_none()

    async def artifact(self, artifact_id: uuid.UUID) -> ExportArtifact | None:
        return (
            await self.session.execute(
                select(ExportArtifact).where(
                    ExportArtifact.id == artifact_id,
                    ExportArtifact.org_id == self.org_id,
                )
            )
        ).scalar_one_or_none()

    async def separation_required(self) -> bool:
        settings = (
            await self.session.execute(
                select(Organization.settings).where(Organization.id == self.org_id)
            )
        ).scalar_one()
        value = settings.get("approvalSeparationOfDutiesRequired", True)
        return value if isinstance(value, bool) else True

    async def request(self, request_id: uuid.UUID) -> ApprovalRequest | None:
        return (
            await self.session.execute(
                select(ApprovalRequest).where(
                    ApprovalRequest.id == request_id,
                    ApprovalRequest.org_id == self.org_id,
                    ApprovalRequest.deleted_at.is_(None),
                )
            )
        ).scalar_one_or_none()

    async def requests_for_paper(self, paper_id: uuid.UUID) -> list[ApprovalRequest]:
        return list(
            (
                await self.session.execute(
                    select(ApprovalRequest)
                    .where(
                        ApprovalRequest.paper_id == paper_id,
                        ApprovalRequest.org_id == self.org_id,
                        ApprovalRequest.deleted_at.is_(None),
                    )
                    .order_by(ApprovalRequest.created_at.desc(), ApprovalRequest.id.desc())
                )
            ).scalars()
        )

    async def queue(self) -> list[ApprovalRequest]:
        return list(
            (
                await self.session.execute(
                    select(ApprovalRequest)
                    .where(
                        ApprovalRequest.org_id == self.org_id,
                        ApprovalRequest.status.in_(("submitted", "review")),
                        ApprovalRequest.deleted_at.is_(None),
                    )
                    .order_by(ApprovalRequest.submitted_at, ApprovalRequest.id)
                )
            ).scalars()
        )

    async def events(self, request_id: uuid.UUID) -> list[ApprovalRequestEvent]:
        return list(
            (
                await self.session.execute(
                    select(ApprovalRequestEvent)
                    .where(
                        ApprovalRequestEvent.approval_request_id == request_id,
                        ApprovalRequestEvent.org_id == self.org_id,
                    )
                    .order_by(
                        ApprovalRequestEvent.occurred_at, ApprovalRequestEvent.id
                    )
                )
            ).scalars()
        )

    async def has_event(self, request_id: uuid.UUID, action: str) -> bool:
        return (
            await self.session.execute(
                select(ApprovalRequestEvent.id)
                .where(
                    ApprovalRequestEvent.approval_request_id == request_id,
                    ApprovalRequestEvent.org_id == self.org_id,
                    ApprovalRequestEvent.action == action,
                )
                .limit(1)
            )
        ).scalar_one_or_none() is not None

    async def comments(self, request_id: uuid.UUID) -> list[ApprovalComment]:
        return list(
            (
                await self.session.execute(
                    select(ApprovalComment)
                    .where(
                        ApprovalComment.approval_request_id == request_id,
                        ApprovalComment.org_id == self.org_id,
                    )
                    .order_by(ApprovalComment.created_at, ApprovalComment.id)
                )
            ).scalars()
        )

    async def approved_baseline(
        self, paper_id: uuid.UUID, target: PaperVersion
    ) -> PaperVersion | None:
        return (
            await self.session.execute(
                select(PaperVersion)
                .join(
                    ApprovalRequest,
                    ApprovalRequest.paper_version_id == PaperVersion.id,
                )
                .where(
                    ApprovalRequest.org_id == self.org_id,
                    ApprovalRequest.paper_id == paper_id,
                    ApprovalRequest.status == "approved",
                    ApprovalRequest.deleted_at.is_(None),
                    PaperVersion.created_at < target.created_at,
                )
                .order_by(PaperVersion.created_at.desc(), PaperVersion.id.desc())
                .limit(1)
            )
        ).scalar_one_or_none()

    async def preceding_version(
        self, paper_id: uuid.UUID, target: PaperVersion
    ) -> PaperVersion | None:
        return (
            await self.session.execute(
                select(PaperVersion)
                .where(
                    PaperVersion.paper_id == paper_id,
                    PaperVersion.created_at < target.created_at,
                )
                .order_by(PaperVersion.created_at.desc(), PaperVersion.id.desc())
                .limit(1)
            )
        ).scalar_one_or_none()


__all__ = ["ApprovalRepository"]
