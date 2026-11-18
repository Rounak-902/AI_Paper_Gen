"""Real-PostgreSQL coverage for lineage visibility predicates.

These tests deliberately seed unusual mixed-visibility lineages as the database
owner, then read them through :meth:`QuestionRepository.get_lineage`.  Using the
owner for the read is intentional: PostgreSQL RLS must not mask a missing
repository predicate and turn a false positive into a passing authorization test.
"""

from __future__ import annotations

import datetime as dt
import uuid
from decimal import Decimal

import pytest
import sqlalchemy as sa

from app.models import Organization, QuestionBank, Subject
from app.modules.questions.repository import QuestionRepository

pytestmark = pytest.mark.db


def _version(
    tenant,
    curriculum,
    *,
    root_id: uuid.UUID,
    version_number: int,
    org_id: uuid.UUID | None,
    subject_id: uuid.UUID,
    is_current: bool,
    deleted_at: dt.datetime | None = None,
) -> QuestionBank:
    """Build one valid, explicitly rooted version for a predicate test."""

    question_id = root_id if version_number == 1 else uuid.uuid4()
    return QuestionBank(
        id=question_id,
        root_question_id=root_id,
        version_number=version_number,
        is_current=is_current,
        org_id=org_id,
        scope="global" if org_id is None else "org",
        source="institute_bank",
        board_id=curriculum.board_id,
        class_id=curriculum.class_id,
        subject_id=subject_id,
        chapter_id=(
            curriculum.chapter_id
            if subject_id == curriculum.subject_id
            else None
        ),
        qtype="true_false",
        marks=Decimal("1.0"),
        difficulty="easy",
        language="en",
        stem=f"Lineage authorization DB proof v{version_number} {question_id}",
        answer={"value": True},
        content_hash=uuid.uuid4().hex,
        status="draft",
        created_by=tenant.user_id,
        deleted_at=deleted_at,
    )


async def _stored_ids(db_session, root_id: uuid.UUID) -> set[uuid.UUID]:
    """Return the unfiltered physical lineage as a positive fixture control."""

    return set(
        (
            await db_session.execute(
                sa.select(QuestionBank.id).where(
                    QuestionBank.root_question_id == root_id
                )
            )
        ).scalars()
    )


async def test_lineage_excludes_a_version_owned_by_another_org(
    db_session, tenant, curriculum
) -> None:
    other_org = Organization(name=f"Lineage other org {uuid.uuid4()}")
    db_session.add(other_org)
    await db_session.flush()

    root_id = uuid.uuid4()
    own = _version(
        tenant,
        curriculum,
        root_id=root_id,
        version_number=1,
        org_id=tenant.org_id,
        subject_id=curriculum.subject_id,
        is_current=False,
    )
    foreign = _version(
        tenant,
        curriculum,
        root_id=root_id,
        version_number=2,
        org_id=other_org.id,
        subject_id=curriculum.subject_id,
        is_current=True,
    )
    db_session.add_all([own, foreign])
    await db_session.commit()

    assert await _stored_ids(db_session, root_id) == {own.id, foreign.id}
    db_session.expunge_all()

    visible = await QuestionRepository(db_session, tenant.org_id).get_lineage(root_id)
    assert [row.id for row in visible] == [own.id]


async def test_lineage_includes_a_global_version_for_every_caller_org(
    db_session, tenant, curriculum
) -> None:
    other_org = Organization(name=f"Lineage global reader {uuid.uuid4()}")
    db_session.add(other_org)
    await db_session.flush()

    root_id = uuid.uuid4()
    own = _version(
        tenant,
        curriculum,
        root_id=root_id,
        version_number=1,
        org_id=tenant.org_id,
        subject_id=curriculum.subject_id,
        is_current=False,
    )
    global_version = _version(
        tenant,
        curriculum,
        root_id=root_id,
        version_number=2,
        org_id=None,
        subject_id=curriculum.subject_id,
        is_current=True,
    )
    db_session.add_all([own, global_version])
    await db_session.commit()

    assert await _stored_ids(db_session, root_id) == {own.id, global_version.id}
    db_session.expunge_all()

    tenant_visible = await QuestionRepository(db_session, tenant.org_id).get_lineage(
        root_id
    )
    other_visible = await QuestionRepository(db_session, other_org.id).get_lineage(
        root_id
    )
    assert [row.id for row in tenant_visible] == [own.id, global_version.id]
    assert [row.id for row in other_visible] == [global_version.id]


async def test_lineage_excludes_a_soft_deleted_version(
    db_session, tenant, curriculum
) -> None:
    root_id = uuid.uuid4()
    live = _version(
        tenant,
        curriculum,
        root_id=root_id,
        version_number=1,
        org_id=tenant.org_id,
        subject_id=curriculum.subject_id,
        is_current=True,
    )
    deleted = _version(
        tenant,
        curriculum,
        root_id=root_id,
        version_number=2,
        org_id=tenant.org_id,
        subject_id=curriculum.subject_id,
        is_current=False,
        deleted_at=dt.datetime.now(dt.timezone.utc),
    )
    db_session.add_all([live, deleted])
    await db_session.commit()

    assert await _stored_ids(db_session, root_id) == {live.id, deleted.id}
    db_session.expunge_all()

    visible = await QuestionRepository(db_session, tenant.org_id).get_lineage(root_id)
    assert [row.id for row in visible] == [live.id]


async def test_lineage_subject_scope_includes_inside_and_excludes_outside(
    db_session, tenant, curriculum
) -> None:
    outside_subject = Subject(
        class_id=curriculum.class_id,
        curriculum_version_id=curriculum.curriculum_version_id,
        name=f"Lineage outside subject {uuid.uuid4()}",
    )
    db_session.add(outside_subject)
    await db_session.flush()

    root_id = uuid.uuid4()
    inside_v1 = _version(
        tenant,
        curriculum,
        root_id=root_id,
        version_number=1,
        org_id=tenant.org_id,
        subject_id=curriculum.subject_id,
        is_current=False,
    )
    outside_v2 = _version(
        tenant,
        curriculum,
        root_id=root_id,
        version_number=2,
        org_id=tenant.org_id,
        subject_id=outside_subject.id,
        is_current=False,
    )
    inside_v3 = _version(
        tenant,
        curriculum,
        root_id=root_id,
        version_number=3,
        org_id=tenant.org_id,
        subject_id=curriculum.subject_id,
        is_current=True,
    )
    db_session.add_all([inside_v1, outside_v2, inside_v3])
    await db_session.commit()

    assert await _stored_ids(db_session, root_id) == {
        inside_v1.id,
        outside_v2.id,
        inside_v3.id,
    }
    db_session.expunge_all()

    visible = await QuestionRepository(db_session, tenant.org_id).get_lineage(
        root_id,
        subject_ids=(curriculum.subject_id,),
    )
    assert [row.id for row in visible] == [inside_v1.id, inside_v3.id]


async def test_lineage_empty_subject_tuple_fails_closed_against_postgres(
    db_session, tenant, curriculum
) -> None:
    root_id = uuid.uuid4()
    stored = _version(
        tenant,
        curriculum,
        root_id=root_id,
        version_number=1,
        org_id=tenant.org_id,
        subject_id=curriculum.subject_id,
        is_current=True,
    )
    db_session.add(stored)
    await db_session.commit()

    assert await _stored_ids(db_session, root_id) == {stored.id}
    db_session.expunge_all()

    repo = QuestionRepository(db_session, tenant.org_id)
    assert [row.id for row in await repo.get_lineage(root_id)] == [stored.id]
    assert await repo.get_lineage(root_id, subject_ids=()) == []
