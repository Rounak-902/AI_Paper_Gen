"""Focused API/session contracts for the P2-T4 global-question security fix."""

from __future__ import annotations

import uuid
from contextlib import asynccontextmanager
from types import SimpleNamespace
from typing import Any

import pytest
from fastapi.routing import APIRoute
from sqlalchemy.exc import ProgrammingError

from app.common import deps
from app.common.deps import OrgContext, RequestScope
from app.main import app
from app.modules.questions import schemas, service


ORG_ID = uuid.UUID("c5100000-0000-4000-8000-000000000001")
USER_ID = uuid.UUID("c5200000-0000-4000-8000-000000000001")


class _PostgresOriginalError(Exception):
    def __init__(self, message: str, sqlstate: str) -> None:
        super().__init__(message)
        self.sqlstate = sqlstate


def _programming_error(message: str, sqlstate: str = "42501") -> ProgrammingError:
    return ProgrammingError(
        "INSERT INTO question_bank ...",
        {},
        _PostgresOriginalError(message, sqlstate),
    )


def _scope(role: str) -> RequestScope:
    return RequestScope(org_id=ORG_ID, user_id=USER_ID, role=role)


def _payload() -> schemas.QuestionCreateIn:
    return schemas.QuestionCreateIn(
        qtype="true_false",
        marks="1.0",
        difficulty="easy",
        language="en",
        source="institute_bank",
        scope="global",
        stem="A focused global authorization contract question.",
        answer={"value": True},
    )


@pytest.mark.parametrize("role", ["teacher", "coaching_admin"])
def test_non_super_admin_global_creation_has_stable_domain_403(role: str) -> None:
    with pytest.raises(service.GlobalQuestionCreationForbidden) as exc_info:
        service._resolve_scope_and_org(_scope(role), "global")

    assert exc_info.value.status_code == 403
    assert exc_info.value.code == "GLOBAL_QUESTION_CREATION_FORBIDDEN"
    assert exc_info.value.code != "INTERNAL_ERROR"


def test_super_admin_resolves_to_null_org_global_scope() -> None:
    assert service._resolve_scope_and_org(_scope("super_admin"), "global") == (
        "global",
        None,
    )


def test_rls_classifier_requires_state_policy_text_and_question_table() -> None:
    assert service._is_question_bank_rls_denial(
        _programming_error(
            'new row violates row-level security policy for table "question_bank"'
        )
    )
    assert not service._is_question_bank_rls_denial(
        _programming_error('permission denied for table "question_bank"')
    )
    assert not service._is_question_bank_rls_denial(
        _programming_error(
            'new row violates row-level security policy for table "audit_logs"'
        )
    )
    assert not service._is_question_bank_rls_denial(
        _programming_error(
            'new row violates row-level security policy for table "question_bank"',
            sqlstate="42601",
        )
    )


@pytest.mark.asyncio
async def test_global_question_rls_denial_maps_to_domain_403(monkeypatch) -> None:
    denial = _programming_error(
        'new row violates row-level security policy for table "question_bank"'
    )

    class DeniedRepository:
        def __init__(self, session: Any, org_id: uuid.UUID) -> None:
            assert org_id == ORG_ID

        async def create_question(self, **kwargs: Any) -> Any:
            assert kwargs["scope"] == "global"
            assert kwargs["org_id"] is None
            raise denial

    monkeypatch.setattr(service, "QuestionRepository", DeniedRepository)
    monkeypatch.setattr(
        service,
        "get_settings",
        lambda: SimpleNamespace(DEDUP_REVIEW_THRESHOLD=0.80),
    )

    with pytest.raises(service.GlobalQuestionCreationForbidden) as exc_info:
        await service.create_question(
            session=object(),  # type: ignore[arg-type]
            scope=_scope("super_admin"),
            org_id=ORG_ID,
            payload=_payload(),
        )

    assert exc_info.value.status_code == 403
    assert exc_info.value.code == "GLOBAL_QUESTION_CREATION_FORBIDDEN"
    assert "authorization policy" in exc_info.value.message


@pytest.mark.asyncio
async def test_unrelated_programming_error_is_not_mislabeled(monkeypatch) -> None:
    unrelated = _programming_error("syntax error at or near INSERT", sqlstate="42601")

    class BrokenRepository:
        def __init__(self, session: Any, org_id: uuid.UUID) -> None:
            pass

        async def create_question(self, **kwargs: Any) -> Any:
            raise unrelated

    monkeypatch.setattr(service, "QuestionRepository", BrokenRepository)
    monkeypatch.setattr(
        service,
        "get_settings",
        lambda: SimpleNamespace(DEDUP_REVIEW_THRESHOLD=0.80),
    )

    with pytest.raises(ProgrammingError) as exc_info:
        await service.create_question(
            session=object(),  # type: ignore[arg-type]
            scope=_scope("super_admin"),
            org_id=ORG_ID,
            payload=_payload(),
        )

    assert exc_info.value is unrelated


@pytest.mark.asyncio
async def test_get_db_remains_tenant_only(monkeypatch) -> None:
    captured: dict[str, Any] = {}
    sentinel_session = object()

    @asynccontextmanager
    async def fake_tenant_session(org_id: uuid.UUID):
        captured.update(org_id=org_id)
        yield sentinel_session

    monkeypatch.setattr(deps, "get_tenant_session", fake_tenant_session)
    context = OrgContext(
        org_id=ORG_ID,
        user_id=USER_ID,
        role="super_admin",
        roles=("super_admin",),
    )

    dependency = deps.get_db(context)
    assert await anext(dependency) is sentinel_session
    await dependency.aclose()
    assert captured == {"org_id": ORG_ID}


@pytest.mark.asyncio
async def test_get_global_write_db_sets_verified_actor_and_tenant_context(
    monkeypatch,
) -> None:
    captured: dict[str, Any] = {}
    sentinel_session = object()

    @asynccontextmanager
    async def fake_identity_session(
        user_id: uuid.UUID,
        *,
        org_id: uuid.UUID,
    ):
        captured.update(user_id=user_id, org_id=org_id)
        yield sentinel_session

    monkeypatch.setattr(deps, "get_identity_session", fake_identity_session)
    context = OrgContext(
        org_id=ORG_ID,
        user_id=USER_ID,
        role="super_admin",
        roles=("super_admin",),
    )

    dependency = deps.get_global_write_db(context)
    assert await anext(dependency) is sentinel_session
    await dependency.aclose()
    assert captured == {"user_id": USER_ID, "org_id": ORG_ID}


def test_global_write_db_is_opt_in_only_for_question_creation() -> None:
    """Freeze the actor-aware transaction to the audited question-write routes."""

    consumers: list[tuple[str, str]] = []
    pending_routers: list[Any] = [app]
    visited: set[int] = set()
    production_routes: list[APIRoute] = []
    while pending_routers:
        current_router = pending_routers.pop()
        if id(current_router) in visited:
            continue
        visited.add(id(current_router))
        for route in current_router.routes:
            if isinstance(route, APIRoute):
                production_routes.append(route)
                continue
            included_router = getattr(route, "original_router", None)
            if included_router is not None:
                pending_routers.append(included_router)

    for route in production_routes:
        pending = list(route.dependant.dependencies)
        uses_global_write_db = False
        while pending:
            dependency = pending.pop()
            if dependency.call is deps.get_global_write_db:
                uses_global_write_db = True
                break
            pending.extend(dependency.dependencies)

        if uses_global_write_db:
            consumers.extend(
                (method, route.path) for method in sorted(route.methods or ())
            )

    assert consumers == [
        ("POST", "/api/v1/orgs/{org_id}/questions"),
        ("POST", "/api/v1/orgs/{org_id}/questions/import"),
        ("POST", "/api/v1/orgs/{org_id}/questions/bulk"),
    ]
