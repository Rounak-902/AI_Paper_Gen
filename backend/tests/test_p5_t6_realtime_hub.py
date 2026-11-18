"""P5-T6 durable reconnect projection and frozen-boundary contract."""

from __future__ import annotations

from contextlib import asynccontextmanager
from decimal import Decimal
from types import SimpleNamespace
import uuid

from httpx import ASGITransport, AsyncClient
import pytest

from app.common.deps import RequestScope, get_db, get_scope
from app.main import app
from app.models.assembly import PaperAssemblyRun, PaperAssemblySlot
from app.models.paper import GeneratedPaper, PaperVersion
from app.workers import tasks


def test_reconciliation_route_is_read_only_and_separate_from_mutations() -> None:
    paths = app.openapi()["paths"]
    assert set(paths["/api/v1/papers/{paper_id}/realtime-state"]) == {"get"}
    assert "/api/v1/papers/{paper_id}/regenerations" in paths
    assert "/api/v1/orgs/{org_id}/papers/generate" in paths


@pytest.mark.asyncio
async def test_exhausted_failure_is_durable_but_intermediate_retry_stays_pending(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    class _Engine:
        async def dispose(self) -> None:
            pass

    class _Result:
        def __init__(self, value) -> None:
            self.value = value

        def scalar_one_or_none(self):
            return self.value

    checkpoint = SimpleNamespace(status="running", attempt_count=1, last_error=None)
    run = SimpleNamespace(status="running", last_error=None)

    class _Session:
        calls = 0

        async def execute(self, statement):
            del statement
            self.calls += 1
            return _Result(checkpoint if self.calls == 1 else run)

    @asynccontextmanager
    async def _tenant_session(*args, **kwargs):
        del args, kwargs
        yield _Session()

    monkeypatch.setattr(tasks, "create_engine", lambda settings: _Engine())
    monkeypatch.setattr(tasks, "async_sessionmaker", lambda *args, **kwargs: object())
    monkeypatch.setattr(tasks, "get_tenant_session", _tenant_session)

    await tasks._record_slot_failure(
        org_id=uuid.uuid4(), run_id=uuid.uuid4(), slot_id="A:1",
        exc=RuntimeError("exhausted"), terminal=True,
    )
    assert checkpoint.status == "failed"
    assert run.status == "failed"
    assert "exhausted" in checkpoint.last_error
    assert run.last_error == checkpoint.last_error

    checkpoint.status = "running"
    checkpoint.last_error = None
    await tasks._record_slot_failure(
        org_id=uuid.uuid4(), run_id=uuid.uuid4(), slot_id="A:1",
        exc=RuntimeError("retry"), terminal=False,
    )
    assert checkpoint.status == "pending"


@pytest.mark.db
async def test_reconciliation_projects_committed_slots_and_terminal_version(
    db_session, tenant, curriculum
) -> None:
    paper = GeneratedPaper(
        org_id=tenant.org_id,
        created_by=tenant.user_id,
        curriculum_version_id=curriculum.curriculum_version_id,
        blueprint={"schema": "p5-t6"},
        title="Reconnect proof",
        total_marks=Decimal("2.0"),
        duration_min=30,
        status="draft",
    )
    db_session.add(paper)
    await db_session.flush()
    version = PaperVersion(
        paper_id=paper.id,
        label="v1",
        content={"schemaVersion": 1, "sections": [], "assembly": {"coverage": [], "compromises": []}},
        answer_key={"questions": []},
    )
    db_session.add(version)
    await db_session.flush()
    run = PaperAssemblyRun(
        org_id=tenant.org_id,
        paper_id=paper.id,
        label="p5-t6-run",
        status="completed",
        solver_snapshot={"assignments": []},
        paper_version_id=version.id,
    )
    db_session.add(run)
    await db_session.flush()
    db_session.add(PaperAssemblySlot(
        org_id=tenant.org_id,
        run_id=run.id,
        paper_id=paper.id,
        slot_id="A:1",
        section_id="A",
        position=1,
        status="completed",
        idempotency_key=f"p5-t6:{uuid.uuid4()}",
        solver_question_id=uuid.uuid4(),
        question_id=None,
        attempt_count=1,
        snapshot={"id": "question-1", "stem": "Committed during disconnect", "marks": 2},
    ))
    await db_session.flush()

    scope = RequestScope(org_id=tenant.org_id, user_id=tenant.user_id, role="super_admin")

    async def override_db():
        yield db_session

    app.dependency_overrides[get_scope] = lambda: scope
    app.dependency_overrides[get_db] = override_db
    try:
        async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
            response = await client.get(f"/api/v1/papers/{paper.id}/realtime-state")
    finally:
        app.dependency_overrides.clear()

    assert response.status_code == 200, response.text
    state = response.json()["data"]
    assert state["status"] == "completed"
    assert state["versionId"] == str(version.id)
    assert state["filledSlots"] == state["totalSlots"] == 1
    assert state["slots"][0]["question"]["stem"] == "Committed during disconnect"
