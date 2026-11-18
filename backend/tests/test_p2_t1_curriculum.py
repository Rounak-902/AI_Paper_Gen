"""P2-T1 curriculum hierarchy, snapshots, lineage, guards and concurrency."""

from __future__ import annotations

import uuid

import pytest
from httpx import ASGITransport, AsyncClient
from pydantic import ValidationError as PydanticValidationError

from app.common.deps import OrgContext, get_org_context
from app.common.errors import BusinessRuleError, ConflictError, ValidationError
from app.db.exceptions import OptimisticLockError
from app.main import create_app
from app.models import Organization, User
from app.modules.curriculum import service
from app.modules.curriculum.repository import ChapterRepository, OutcomeRepository
from app.modules.curriculum.schemas import (
    BoardCreateIn,
    ChapterCreateIn,
    ChapterUpdateIn,
    ClassCreateIn,
    CurriculumVersionCreateIn,
    CurriculumVersionUpdateIn,
    OutcomeCreateIn,
    OutcomeUpdateIn,
    SubjectCreateIn,
    SubjectUpdateIn,
)


def _ctx(tenant) -> OrgContext:
    return OrgContext(
        org_id=tenant.org_id,
        user_id=tenant.user_id,
        role="super_admin",
        roles=("super_admin",),
    )


def _suffix() -> str:
    return uuid.uuid4().hex[:10]


async def _create_chain(db_session, ctx: OrgContext, *, suffix: str):
    board = await service.create_board(
        session=db_session,
        ctx=ctx,
        payload=BoardCreateIn(code=f"P2-{suffix}", name=f"P2 Board {suffix}"),
    )
    school_class = await service.create_class(
        session=db_session,
        ctx=ctx,
        payload=ClassCreateIn(board_id=board.id, name=f"Class {suffix}"),
    )
    version = await service.create_curriculum_version(
        session=db_session,
        ctx=ctx,
        payload=CurriculumVersionCreateIn(
            board_id=board.id,
            academic_year=f"{suffix}-year",
            label="Initial snapshot",
        ),
    )
    subject = await service.create_subject(
        session=db_session,
        ctx=ctx,
        payload=SubjectCreateIn(
            class_id=school_class.id,
            curriculum_version_id=version.id,
            name=f"Subject {suffix}",
        ),
    )
    chapter = await service.create_chapter(
        session=db_session,
        ctx=ctx,
        payload=ChapterCreateIn(
            subject_id=subject.id,
            curriculum_version_id=version.id,
            seq=1,
            title="Foundations",
        ),
    )
    outcome = await service.create_outcome(
        session=db_session,
        ctx=ctx,
        payload=OutcomeCreateIn(
            chapter_id=chapter.id,
            code=f"LO-{suffix}",
            description="Explain the foundational concept.",
        ),
    )
    await db_session.flush()
    return board, school_class, version, subject, chapter, outcome


@pytest.mark.db
async def test_full_hierarchy_round_trip_and_cross_org_global_read(
    db_session, tenant
):
    """Every relation round-trips and another org context sees the same IDs."""

    ctx_a = _ctx(tenant)
    suffix = _suffix()
    board, school_class, version, subject, chapter, outcome = await _create_chain(
        db_session, ctx_a, suffix=suffix
    )

    other_org = Organization(name=f"Other org {suffix}")
    other_user = User(
        email=f"other-{suffix}@example.test", full_name="Other Curriculum Reader"
    )
    db_session.add_all([other_org, other_user])
    await db_session.flush()
    ctx_b = OrgContext(
        org_id=other_org.id,
        user_id=other_user.id,
        role="teacher",
        roles=("teacher",),
    )

    # The request context differs; the global catalog result does not.
    assert (await service.get_board(session=db_session, ctx=ctx_b, board_id=board.id)).id == board.id
    assert (await service.get_class(session=db_session, ctx=ctx_b, class_id=school_class.id)).board_id == board.id
    assert (await service.get_subject(session=db_session, ctx=ctx_b, subject_id=subject.id)).class_id == school_class.id
    chapter_read = await service.get_chapter(
        session=db_session, ctx=ctx_b, chapter_id=chapter.id
    )
    assert chapter_read.subject_id == subject.id
    assert chapter_read.curriculum_version_id == version.id
    assert (await service.get_outcome(session=db_session, ctx=ctx_b, outcome_id=outcome.id)).chapter_id == chapter.id

    classes, _ = await service.list_classes(
        session=db_session, ctx=ctx_b, board_id=board.id, limit=10, offset=0
    )
    subjects, _ = await service.list_subjects(
        session=db_session,
        ctx=ctx_b,
        class_id=school_class.id,
        curriculum_version_id=version.id,
        limit=10,
        offset=0,
    )
    chapters, _ = await service.list_chapters(
        session=db_session,
        ctx=ctx_b,
        subject_id=subject.id,
        curriculum_version_id=version.id,
        limit=10,
        offset=0,
    )
    outcomes, _ = await service.list_outcomes(
        session=db_session, ctx=ctx_b, chapter_id=chapter.id, limit=10, offset=0
    )
    assert [row.id for row in classes] == [school_class.id]
    assert [row.id for row in subjects] == [subject.id]
    assert [row.id for row in chapters] == [chapter.id]
    assert [row.id for row in outcomes] == [outcome.id]


@pytest.mark.db
async def test_versioned_same_name_subjects_and_rollover_lineage(db_session, tenant):
    """Snapshots may repeat names/seq; lineage traverses immediate active ancestors."""

    ctx = _ctx(tenant)
    suffix = _suffix()
    board = await service.create_board(
        session=db_session,
        ctx=ctx,
        payload=BoardCreateIn(code=f"VER-{suffix}", name="Versioned board"),
    )
    school_class = await service.create_class(
        session=db_session,
        ctx=ctx,
        payload=ClassCreateIn(board_id=board.id, name="Class 10"),
    )
    v1 = await service.create_curriculum_version(
        session=db_session,
        ctx=ctx,
        payload=CurriculumVersionCreateIn(
            board_id=board.id, academic_year=f"{suffix}-v1"
        ),
    )
    subject_v1 = await service.create_subject(
        session=db_session,
        ctx=ctx,
        payload=SubjectCreateIn(
            class_id=school_class.id,
            curriculum_version_id=v1.id,
            name="Mathematics",
        ),
    )
    old_chapter = await service.create_chapter(
        session=db_session,
        ctx=ctx,
        payload=ChapterCreateIn(
            subject_id=subject_v1.id,
            curriculum_version_id=v1.id,
            seq=1,
            title="Real Numbers",
        ),
    )

    v1 = await service.update_curriculum_version(
        session=db_session,
        ctx=ctx,
        curriculum_version_id=v1.id,
        payload=CurriculumVersionUpdateIn(row_version=1, status="active"),
    )
    v2 = await service.create_curriculum_version(
        session=db_session,
        ctx=ctx,
        payload=CurriculumVersionCreateIn(
            board_id=board.id, academic_year=f"{suffix}-v2"
        ),
    )
    # Same class and display name, but a distinct subject snapshot.
    subject_v2 = await service.create_subject(
        session=db_session,
        ctx=ctx,
        payload=SubjectCreateIn(
            class_id=school_class.id,
            curriculum_version_id=v2.id,
            name="Mathematics",
        ),
    )
    assert subject_v1.id != subject_v2.id
    new_chapter = await service.create_chapter(
        session=db_session,
        ctx=ctx,
        payload=ChapterCreateIn(
            subject_id=subject_v2.id,
            curriculum_version_id=v2.id,
            seq=1,
            title="Real Numbers (revised)",
            derived_from_chapter_id=old_chapter.id,
        ),
    )
    lineage = await service.get_chapter_lineage(
        session=db_session, ctx=ctx, chapter_id=new_chapter.id
    )
    assert [row.id for row in lineage.lineage] == [old_chapter.id, new_chapter.id]
    assert lineage.lineage[-1].derived_from_chapter_id == old_chapter.id
    assert await ChapterRepository(db_session, ctx.org_id).has_dependencies(
        old_chapter.id
    )

    # The prior published snapshot cannot be edited in place.
    with pytest.raises(BusinessRuleError, match="immutable"):
        await service.update_chapter(
            session=db_session,
            ctx=ctx,
            chapter_id=old_chapter.id,
            payload=ChapterUpdateIn(
                row_version=old_chapter.row_version,
                derived_from_chapter_id=new_chapter.id,
            ),
        )
    # A direct self edge is still rejected while the target snapshot is draft.
    with pytest.raises(ValidationError, match="itself"):
        await service.update_chapter(
            session=db_session,
            ctx=ctx,
            chapter_id=new_chapter.id,
            payload=ChapterUpdateIn(
                row_version=new_chapter.row_version,
                derived_from_chapter_id=new_chapter.id,
            ),
        )

    # Publish v2, then prove a third rollover traverses both immediate edges.
    v1 = await service.update_curriculum_version(
        session=db_session,
        ctx=ctx,
        curriculum_version_id=v1.id,
        payload=CurriculumVersionUpdateIn(row_version=2, status="superseded"),
    )
    v2 = await service.update_curriculum_version(
        session=db_session,
        ctx=ctx,
        curriculum_version_id=v2.id,
        payload=CurriculumVersionUpdateIn(row_version=1, status="active"),
    )
    v3 = await service.create_curriculum_version(
        session=db_session,
        ctx=ctx,
        payload=CurriculumVersionCreateIn(
            board_id=board.id, academic_year=f"{suffix}-v3"
        ),
    )
    subject_v3 = await service.create_subject(
        session=db_session,
        ctx=ctx,
        payload=SubjectCreateIn(
            class_id=school_class.id,
            curriculum_version_id=v3.id,
            name="Mathematics",
        ),
    )
    newest_chapter = await service.create_chapter(
        session=db_session,
        ctx=ctx,
        payload=ChapterCreateIn(
            subject_id=subject_v3.id,
            curriculum_version_id=v3.id,
            seq=1,
            title="Real Numbers (second revision)",
            derived_from_chapter_id=new_chapter.id,
        ),
    )
    lineage = await service.get_chapter_lineage(
        session=db_session, ctx=ctx, chapter_id=newest_chapter.id
    )
    assert [row.id for row in lineage.lineage] == [
        old_chapter.id,
        new_chapter.id,
        newest_chapter.id,
    ]

    # A superseded/non-current source cannot be used as a future-facing edge.
    with pytest.raises(ValidationError, match="active prior snapshot"):
        await service.create_chapter(
            session=db_session,
            ctx=ctx,
            payload=ChapterCreateIn(
                subject_id=subject_v3.id,
                curriculum_version_id=v3.id,
                seq=2,
                title="Invalid skipped-generation link",
                derived_from_chapter_id=old_chapter.id,
            ),
        )


@pytest.mark.db
async def test_hierarchy_rejects_cross_board_curriculum_version(db_session, tenant):
    ctx = _ctx(tenant)
    suffix = _suffix()
    board_a = await service.create_board(
        session=db_session,
        ctx=ctx,
        payload=BoardCreateIn(code=f"A-{suffix}", name="Board A"),
    )
    board_b = await service.create_board(
        session=db_session,
        ctx=ctx,
        payload=BoardCreateIn(code=f"B-{suffix}", name="Board B"),
    )
    school_class = await service.create_class(
        session=db_session,
        ctx=ctx,
        payload=ClassCreateIn(board_id=board_a.id, name="Class 9"),
    )
    wrong_version = await service.create_curriculum_version(
        session=db_session,
        ctx=ctx,
        payload=CurriculumVersionCreateIn(
            board_id=board_b.id, academic_year=f"wrong-{suffix}"
        ),
    )
    with pytest.raises(ValidationError, match="class's board"):
        await service.create_subject(
            session=db_session,
            ctx=ctx,
            payload=SubjectCreateIn(
                class_id=school_class.id,
                curriculum_version_id=wrong_version.id,
                name="Science",
            ),
        )


@pytest.mark.db
async def test_curriculum_version_lifecycle_is_forward_only(db_session, tenant):
    ctx = _ctx(tenant)
    suffix = _suffix()
    board = await service.create_board(
        session=db_session,
        ctx=ctx,
        payload=BoardCreateIn(code=f"LIFE-{suffix}", name="Lifecycle Board"),
    )
    with pytest.raises(PydanticValidationError):
        CurriculumVersionCreateIn(
            board_id=board.id,
            academic_year=f"bypass-{suffix}",
            status="active",
        )
    school_class = await service.create_class(
        session=db_session,
        ctx=ctx,
        payload=ClassCreateIn(board_id=board.id, name="Class 12"),
    )
    version = await service.create_curriculum_version(
        session=db_session,
        ctx=ctx,
        payload=CurriculumVersionCreateIn(
            board_id=board.id, academic_year=f"life-{suffix}"
        ),
    )
    assert version.status == "draft" and version.row_version == 1
    subject = await service.create_subject(
        session=db_session,
        ctx=ctx,
        payload=SubjectCreateIn(
            class_id=school_class.id,
            curriculum_version_id=version.id,
            name="Physics",
        ),
    )
    chapter = await service.create_chapter(
        session=db_session,
        ctx=ctx,
        payload=ChapterCreateIn(
            subject_id=subject.id,
            curriculum_version_id=version.id,
            seq=1,
            title="Units and Measurements",
        ),
    )
    outcome = await service.create_outcome(
        session=db_session,
        ctx=ctx,
        payload=OutcomeCreateIn(
            chapter_id=chapter.id,
            code="PHY.LO.1",
            description="Apply dimensional analysis.",
        ),
    )
    active = await service.update_curriculum_version(
        session=db_session,
        ctx=ctx,
        curriculum_version_id=version.id,
        payload=CurriculumVersionUpdateIn(row_version=1, status="active"),
    )

    # Publication freezes version metadata and every version-owned hierarchy row.
    with pytest.raises(BusinessRuleError, match="metadata is immutable"):
        await service.update_curriculum_version(
            session=db_session,
            ctx=ctx,
            curriculum_version_id=version.id,
            payload=CurriculumVersionUpdateIn(row_version=2, label="Late rewrite"),
        )
    with pytest.raises(BusinessRuleError, match="immutable"):
        await service.create_subject(
            session=db_session,
            ctx=ctx,
            payload=SubjectCreateIn(
                class_id=school_class.id,
                curriculum_version_id=version.id,
                name="Chemistry",
            ),
        )
    with pytest.raises(BusinessRuleError, match="immutable"):
        await service.update_subject(
            session=db_session,
            ctx=ctx,
            subject_id=subject.id,
            payload=SubjectUpdateIn(row_version=1, name="Physical Science"),
        )
    with pytest.raises(BusinessRuleError, match="immutable"):
        await service.update_chapter(
            session=db_session,
            ctx=ctx,
            chapter_id=chapter.id,
            payload=ChapterUpdateIn(row_version=1, title="Rewritten chapter"),
        )
    with pytest.raises(BusinessRuleError, match="immutable"):
        await service.create_outcome(
            session=db_session,
            ctx=ctx,
            payload=OutcomeCreateIn(
                chapter_id=chapter.id,
                code="PHY.LO.2",
                description="This must not enter a published snapshot.",
            ),
        )
    with pytest.raises(BusinessRuleError, match="immutable"):
        await service.update_outcome(
            session=db_session,
            ctx=ctx,
            outcome_id=outcome.id,
            payload=OutcomeUpdateIn(row_version=1, description="Late rewrite"),
        )
    with pytest.raises(BusinessRuleError, match="immutable"):
        await service.delete_outcome(
            session=db_session, ctx=ctx, outcome_id=outcome.id, row_version=1
        )
    with pytest.raises(BusinessRuleError, match="immutable"):
        await service.delete_chapter(
            session=db_session, ctx=ctx, chapter_id=chapter.id, row_version=1
        )
    with pytest.raises(BusinessRuleError, match="immutable"):
        await service.delete_subject(
            session=db_session, ctx=ctx, subject_id=subject.id, row_version=1
        )

    superseded = await service.update_curriculum_version(
        session=db_session,
        ctx=ctx,
        curriculum_version_id=version.id,
        payload=CurriculumVersionUpdateIn(row_version=2, status="superseded"),
    )
    assert active.status == "active" and active.row_version == 2
    assert superseded.status == "superseded" and superseded.row_version == 3
    assert (
        await service.get_chapter(
            session=db_session, ctx=ctx, chapter_id=chapter.id
        )
    ).title == "Units and Measurements"
    assert (
        await service.get_outcome(
            session=db_session, ctx=ctx, outcome_id=outcome.id
        )
    ).description == "Apply dimensional analysis."
    with pytest.raises(ValidationError, match="draft to active to superseded"):
        await service.update_curriculum_version(
            session=db_session,
            ctx=ctx,
            curriculum_version_id=version.id,
            payload=CurriculumVersionUpdateIn(row_version=3, status="active"),
        )


@pytest.mark.db
async def test_optimistic_lock_and_soft_delete_are_atomic(db_session, tenant):
    ctx = _ctx(tenant)
    *_, chapter, outcome = await _create_chain(
        db_session, ctx, suffix=_suffix()
    )
    updated = await service.update_outcome(
        session=db_session,
        ctx=ctx,
        outcome_id=outcome.id,
        payload=OutcomeUpdateIn(
            row_version=1, description="Updated learning outcome"
        ),
    )
    assert updated.row_version == 2

    with pytest.raises(OptimisticLockError):
        await service.update_outcome(
            session=db_session,
            ctx=ctx,
            outcome_id=outcome.id,
            payload=OutcomeUpdateIn(row_version=1, description="Stale edit"),
        )
    with pytest.raises(OptimisticLockError):
        await service.delete_outcome(
            session=db_session,
            ctx=ctx,
            outcome_id=outcome.id,
            row_version=1,
        )

    await service.delete_outcome(
        session=db_session,
        ctx=ctx,
        outcome_id=outcome.id,
        row_version=2,
    )
    repo = OutcomeRepository(db_session, ctx.org_id)
    assert await repo.get_by_id(outcome.id) is None
    tombstone = await repo.get_by_id(outcome.id, include_deleted=True)
    assert tombstone is not None
    assert tombstone.deleted_at is not None
    assert tombstone.row_version == 3

    listed, _ = await service.list_outcomes(
        session=db_session, ctx=ctx, chapter_id=chapter.id, limit=10, offset=0
    )
    assert outcome.id not in {row.id for row in listed}


@pytest.mark.db
async def test_parent_and_lineage_source_soft_delete_are_blocked(db_session, tenant):
    ctx = _ctx(tenant)
    board, school_class, _, subject, chapter, _ = await _create_chain(
        db_session, ctx, suffix=_suffix()
    )
    with pytest.raises(ConflictError, match="live curriculum"):
        await service.delete_board(
            session=db_session,
            ctx=ctx,
            board_id=board.id,
            row_version=board.row_version,
        )
    with pytest.raises(ConflictError, match="live subjects"):
        await service.delete_class(
            session=db_session,
            ctx=ctx,
            class_id=school_class.id,
            row_version=school_class.row_version,
        )
    with pytest.raises(ConflictError, match="live records"):
        await service.delete_subject(
            session=db_session,
            ctx=ctx,
            subject_id=subject.id,
            row_version=subject.row_version,
        )
    with pytest.raises(ConflictError, match="live records"):
        await service.delete_chapter(
            session=db_session,
            ctx=ctx,
            chapter_id=chapter.id,
            row_version=chapter.row_version,
        )


async def test_router_contract_and_super_admin_write_guard():
    """All 31 operations are documented; a teacher is denied before get_db."""

    app = create_app()
    openapi = app.openapi()
    resources = {
        "boards": "board_id",
        "classes": "class_id",
        "subjects": "subject_id",
        "chapters": "chapter_id",
        "outcomes": "outcome_id",
        "curriculum-versions": "curriculum_version_id",
    }
    expected = set()
    for resource, parameter in resources.items():
        expected.update(
            {
                ("GET", f"/api/v1/{resource}"),
                ("POST", f"/api/v1/{resource}"),
                ("GET", f"/api/v1/{resource}/{{{parameter}}}"),
                ("PATCH", f"/api/v1/{resource}/{{{parameter}}}"),
                ("DELETE", f"/api/v1/{resource}/{{{parameter}}}"),
            }
        )
    expected.add(("GET", "/api/v1/chapters/{chapter_id}/lineage"))
    operations = {
        (method.upper(), path)
        for path, path_item in openapi["paths"].items()
        if path.startswith(tuple(f"/api/v1/{resource}" for resource in resources))
        for method in path_item
        if method in {"get", "post", "patch", "delete"}
    }
    assert operations == expected

    teacher = OrgContext(
        org_id=uuid.uuid4(),
        user_id=uuid.uuid4(),
        role="teacher",
        roles=("teacher",),
    )
    app.dependency_overrides[get_org_context] = lambda: teacher
    try:
        async with AsyncClient(
            transport=ASGITransport(app=app), base_url="http://testserver"
        ) as client:
            response = await client.post(
                "/api/v1/boards", json={"code": "DENIED", "name": "Denied"}
            )
    finally:
        app.dependency_overrides.clear()
    assert response.status_code == 403
    body = response.json()
    assert body["success"] is False
    assert body["error"]["code"] == "FORBIDDEN"
