"""P5-T5 editor-draft persistence and optimistic-conflict contract."""

from __future__ import annotations

import uuid

from httpx import ASGITransport, AsyncClient
import pytest
from sqlalchemy import func, select

from app.common.deps import RequestScope, get_db, get_scope
from app.main import app
from app.models.paper import PaperVersion
from app.models.paper_draft import PaperEditorDraft


def test_draft_routes_are_separate_from_generation_and_versions() -> None:
    paths = app.openapi()["paths"]
    route = "/api/v1/orgs/{org_id}/papers/drafts/{draft_id}"
    assert set(paths[route]) >= {"get", "put"}
    assert paths[route]["put"]["responses"]["200"]
    assert PaperEditorDraft.__tablename__ == "paper_editor_drafts"
    assert PaperEditorDraft.__table__ is not PaperVersion.__table__


@pytest.mark.db
async def test_real_draft_round_trip_and_stale_revision_409(
    db_session, tenant
) -> None:
    draft_id = uuid.uuid4()
    scope = RequestScope(
        org_id=tenant.org_id,
        user_id=tenant.user_id,
        role="super_admin",
    )

    async def override_db():
        yield db_session

    app.dependency_overrides[get_scope] = lambda: scope
    app.dependency_overrides[get_db] = override_db
    version_count_before = await db_session.scalar(select(func.count()).select_from(PaperVersion))
    try:
        async with AsyncClient(
            transport=ASGITransport(app=app), base_url="http://test"
        ) as client:
            created = await client.put(
                f"/api/v1/orgs/{tenant.org_id}/papers/drafts/{draft_id}",
                json={
                    "expectedRevision": None,
                    "document": {"paperId": None, "title": "First autosave"},
                },
            )
            loaded = await client.get(
                f"/api/v1/orgs/{tenant.org_id}/papers/drafts/{draft_id}"
            )
            updated = await client.put(
                f"/api/v1/orgs/{tenant.org_id}/papers/drafts/{draft_id}",
                json={
                    "expectedRevision": 1,
                    "document": {"paperId": None, "title": "Second autosave"},
                },
            )
            stale = await client.put(
                f"/api/v1/orgs/{tenant.org_id}/papers/drafts/{draft_id}",
                json={
                    "expectedRevision": 1,
                    "document": {"paperId": None, "title": "Losing tab"},
                },
            )
    finally:
        app.dependency_overrides.clear()

    assert created.status_code == 200, created.text
    assert created.json()["data"]["revision"] == 1
    assert loaded.status_code == 200, loaded.text
    assert loaded.json()["data"]["document"]["title"] == "First autosave"
    assert updated.status_code == 200, updated.text
    assert updated.json()["data"]["revision"] == 2
    assert stale.status_code == 409, stale.text
    assert stale.json()["error"]["code"] == "CONCURRENT_MODIFICATION"

    row = await db_session.get(PaperEditorDraft, draft_id)
    assert row is not None
    assert row.revision == 2
    assert row.document["title"] == "Second autosave"
    assert await db_session.scalar(select(func.count()).select_from(PaperVersion)) == version_count_before
