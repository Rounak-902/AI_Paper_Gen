"""End-to-end proof that the P1-T3 tenancy models and migrations agree — against a
real PostgreSQL database.

Nothing here is mocked or faked, and that is the whole point. A model file can be
read; what it cannot tell you is whether ``CREATE EXTENSION citext`` actually ran,
whether the CHECK constraint from ADR-011 is really enforced by the server, or
whether ``ON DELETE SET NULL`` behaves the way doc 03 §5 describes. Only a live
database answers those, so every test below performs real SQL against one.

Requirements (the fixtures fail with instructions rather than skipping if unmet):

* PostgreSQL reachable at ``TEST_DATABASE_URL``, defaulting to the local container
  from ``infra/docker-compose.yml``;
* ``alembic upgrade head`` already applied to it.

Each test runs inside a transaction that is rolled back afterwards, so the database
is left exactly as it was found. See ``tests/conftest.py`` for the mechanism.
"""

from __future__ import annotations

import datetime as dt

import pytest
import sqlalchemy as sa
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import selectinload

from app.models import Branch, Membership, Organization, User

pytestmark = pytest.mark.db


# ---------------------------------------------------------------------------
# Migration 0001 — extensions
# ---------------------------------------------------------------------------


async def test_required_extensions_are_installed(db_connection):
    """Revision 0001 (doc 03 §9 step 1) did what it claims."""

    result = await db_connection.execute(
        sa.text("SELECT extname FROM pg_extension ORDER BY extname")
    )
    installed = {row[0] for row in result}

    assert {"pgcrypto", "citext", "pg_trgm"} <= installed, (
        f"missing extension(s): {sorted({'pgcrypto', 'citext', 'pg_trgm'} - installed)}"
    )
    # ADR-007 defers pgvector and doc 03 §9 step 7 gates it on a recorded
    # paraphrase-recall gap in the eval suite. If it turns up here, something
    # enabled it outside the migration history.
    assert "vector" not in installed


# ---------------------------------------------------------------------------
# The round trip
# ---------------------------------------------------------------------------


async def test_organization_user_membership_round_trip(db_session):
    """Insert an organization, a user and the membership linking them; read it back.

    The read is deliberately hostile to false positives: the session's identity map
    is emptied before the SELECT, so the objects asserted on cannot be the ones that
    were inserted — they are reconstructed from what PostgreSQL actually stored.
    """

    org = Organization(name="Sunrise Coaching Centre")
    user = User(
        email="Asha.Rao@Example.com",
        full_name="Asha Rao",
        # A syntactically plausible placeholder. Real hashing is P1-T7's job; this
        # column only has to prove it round-trips.
        password_hash="$argon2id$v=19$m=65536,t=3,p=4$placeholder",
    )
    db_session.add_all([org, user])
    # Flush, not commit: the membership needs the server-generated UUIDs, and a flush
    # is what makes PostgreSQL hand them back.
    await db_session.flush()

    assert org.id is not None, "gen_random_uuid() server default did not populate id"
    assert user.id is not None

    membership = Membership(user_id=user.id, org_id=org.id, role="coaching_admin")
    db_session.add(membership)
    await db_session.commit()

    org_id = org.id
    user_id = user.id
    inserted_org = org
    db_session.expunge_all()

    loaded = (
        await db_session.execute(
            sa.select(Organization)
            .where(Organization.id == org_id)
            .options(
                selectinload(Organization.memberships).selectinload(Membership.user)
            )
        )
    ).scalar_one()

    assert loaded is not inserted_org, "read came from the identity map, not the database"

    # --- organizations: explicit values and every server default -----------------
    assert loaded.name == "Sunrise Coaching Centre"
    assert loaded.org_type == "coaching_center"   # DEFAULT 'coaching_center'
    assert loaded.region == "ap-south-1"          # DEFAULT 'ap-south-1'
    assert loaded.status == "active"              # DEFAULT 'active'
    assert loaded.settings == {}                  # DEFAULT '{}'::jsonb
    assert loaded.subscription_id is None
    assert loaded.row_version == 1                # DEFAULT 1
    assert loaded.deleted_at is None              # ADR-004: alive
    # timestamptz, not a naive timestamp - the tzinfo is the assertion.
    assert isinstance(loaded.created_at, dt.datetime)
    assert loaded.created_at.tzinfo is not None
    assert loaded.updated_at.tzinfo is not None

    # --- memberships: the link, plus its own defaults ----------------------------
    assert len(loaded.memberships) == 1
    linked = loaded.memberships[0]
    assert linked.role == "coaching_admin"
    assert linked.status == "active"
    assert linked.branch_id is None  # null means org-wide (doc 03 §5)
    assert linked.org_id == org_id
    assert linked.row_version == 1
    assert linked.deleted_at is None

    # --- users: reached through the relationship, so the FK really resolves ------
    assert linked.user.id == user_id
    # citext preserves the casing it was given; it only ignores case when comparing.
    assert linked.user.email == "Asha.Rao@Example.com"
    assert linked.user.full_name == "Asha Rao"
    # doc 03 §7.1 / ADR-012 §1 specify an ORM attribute named `fullName`; it is a
    # synonym for the same column rather than a second column.
    assert linked.user.fullName == "Asha Rao"
    assert linked.user.password_hash is not None
    assert linked.user.status == "active"

    # And the reverse direction, so back_populates is wired both ways.
    from_user = (
        await db_session.execute(
            sa.select(User)
            .where(User.id == user_id)
            .options(selectinload(User.memberships))
        )
    ).scalar_one()
    assert [m.org_id for m in from_user.memberships] == [org_id]


async def test_email_lookup_is_case_insensitive(db_session):
    """``users.email`` is citext, so lookups do not need a ``lower()`` on both sides.

    Worth an explicit test because it is quietly fragile: if the bind parameter were
    cast to VARCHAR on the way in, PostgreSQL would use the text ``=`` operator and
    the comparison would silently become case-sensitive. SQLAlchemy avoids that by
    mapping CITEXT to a type with no bind cast, which is exactly the kind of
    behaviour that deserves a test rather than a comment.
    """

    db_session.add(User(email="Nikhil.Verma@Example.com", full_name="Nikhil Verma"))
    await db_session.commit()
    db_session.expunge_all()

    found = (
        await db_session.execute(
            sa.select(User).where(User.email == "nikhil.verma@example.com")
        )
    ).scalar_one()

    assert found.email == "Nikhil.Verma@Example.com"


# ---------------------------------------------------------------------------
# Constrained vocabularies (ADR-011)
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    ("field", "value", "constraint"),
    [
        # 'hod' is reserved by ADR-011 but deliberately not implemented. If this case
        # ever stops raising, the three-role boundary has been widened by accident.
        ("role", "hod", "ck_memberships_role"),
        ("role", "owner", "ck_memberships_role"),
        ("status", "pending", "ck_memberships_status"),
        ("row_version", 0, "ck_memberships_row_version_positive"),
    ],
)
async def test_membership_check_constraints_reject_invalid_values(
    db_session, field, value, constraint
):
    """The database, not just the application, refuses values outside the vocabulary."""

    org = Organization(name="Constraint Probe Org")
    user = User(email="probe@example.com", full_name="Probe User")
    db_session.add_all([org, user])
    await db_session.flush()

    membership = Membership(user_id=user.id, org_id=org.id, role="teacher")
    setattr(membership, field, value)
    db_session.add(membership)

    with pytest.raises(IntegrityError) as exc_info:
        await db_session.flush()

    assert constraint in str(exc_info.value)
    await db_session.rollback()


@pytest.mark.parametrize(
    ("field", "value", "constraint"),
    [
        ("org_type", "university", "ck_organizations_org_type"),
        ("status", "deleted", "ck_organizations_status"),
        ("row_version", 0, "ck_organizations_row_version_positive"),
    ],
)
async def test_organization_check_constraints_reject_invalid_values(
    db_session, field, value, constraint
):
    org = Organization(name="Constraint Probe Org")
    setattr(org, field, value)
    db_session.add(org)

    with pytest.raises(IntegrityError) as exc_info:
        await db_session.flush()

    assert constraint in str(exc_info.value)
    await db_session.rollback()


# ---------------------------------------------------------------------------
# Partial uniqueness (ADR-004)
# ---------------------------------------------------------------------------


async def test_email_uniqueness_applies_only_to_live_rows(db_session):
    """One live row may hold an address; a soft-deleted one releases it.

    This is ADR-004's recorded consequence of soft deletes, and the reason
    ``uq_users_email`` is a partial unique index rather than a UNIQUE constraint.
    """

    first = User(email="shared@example.com", full_name="First Holder")
    db_session.add(first)
    await db_session.commit()
    first_id = first.id

    # Same address, different casing: citext means the index sees a duplicate.
    db_session.add(User(email="Shared@Example.com", full_name="Second Holder"))
    with pytest.raises(IntegrityError) as exc_info:
        await db_session.flush()
    assert "uq_users_email" in str(exc_info.value)
    await db_session.rollback()

    # Re-fetch rather than reusing `first`: Session.rollback() expires everything it
    # holds, and an expired attribute on a detached-ish instance is exactly the kind
    # of implicit IO that raises MissingGreenlet under asyncio.
    db_session.expunge_all()
    first = (
        await db_session.execute(sa.select(User).where(User.id == first_id))
    ).scalar_one()

    # Soft-delete, ADR-004 style: an UPDATE, never a DELETE.
    first.deleted_at = dt.datetime.now(dt.timezone.utc)
    await db_session.commit()

    db_session.add(User(email="shared@example.com", full_name="Second Holder"))
    await db_session.commit()  # must not raise: the live slot is free again

    db_session.expunge_all()
    live = (
        await db_session.execute(
            sa.select(User).where(
                User.email == "shared@example.com", User.deleted_at.is_(None)
            )
        )
    ).scalars().all()
    assert [u.full_name for u in live] == ["Second Holder"]

    total = (
        await db_session.execute(
            sa.select(sa.func.count())
            .select_from(User)
            .where(User.email == "shared@example.com")
        )
    ).scalar_one()
    assert total == 2, "the soft-deleted row should still be there, tombstoned"


async def test_branch_name_uniqueness_is_per_organization_and_partial(db_session):
    """``unique(org_id, name)`` scopes to the tenant and ignores tombstones."""

    org_a = Organization(name="Org A")
    org_b = Organization(name="Org B")
    db_session.add_all([org_a, org_b])
    await db_session.flush()
    org_a_id, org_b_id = org_a.id, org_b.id

    andheri_a = Branch(org_id=org_a_id, name="Andheri")
    db_session.add_all([andheri_a, Branch(org_id=org_b_id, name="Andheri")])
    await db_session.commit()  # same name, different tenants: fine
    andheri_a_id = andheri_a.id

    db_session.add(Branch(org_id=org_a_id, name="Andheri"))
    with pytest.raises(IntegrityError) as exc_info:
        await db_session.flush()
    assert "uq_branches_org_id_name" in str(exc_info.value)
    await db_session.rollback()

    db_session.expunge_all()
    andheri_a = (
        await db_session.execute(sa.select(Branch).where(Branch.id == andheri_a_id))
    ).scalar_one()
    andheri_a.deleted_at = dt.datetime.now(dt.timezone.utc)
    await db_session.commit()

    db_session.add(Branch(org_id=org_a_id, name="Andheri"))
    await db_session.commit()  # the name is free again

    live_names = (
        await db_session.execute(
            sa.select(sa.func.count())
            .select_from(Branch)
            .where(
                Branch.org_id == org_a_id,
                Branch.name == "Andheri",
                Branch.deleted_at.is_(None),
            )
        )
    ).scalar_one()
    assert live_names == 1


# ---------------------------------------------------------------------------
# Referential actions (doc 03 §5)
# ---------------------------------------------------------------------------


async def test_hard_deleting_a_branch_nulls_membership_branch_id(db_session):
    """doc 03 §5: "deleting a branch should set branch_id to null".

    Losing a branch must widen a member's scope to the organization, not revoke
    their access and not delete their membership.

    The DELETE below is the one legitimate hard delete in this codebase: verifying
    what an ``ON DELETE`` rule does is the only way to observe it, and it happens
    inside a transaction that is rolled back. Application code never issues one —
    ADR-004 makes deletion an UPDATE of ``deleted_at``, which cascades nothing. It
    is written against ``Branch.__table__`` (Core, not ORM) precisely so that what
    is being tested is the database's rule and not SQLAlchemy's own FK handling.
    """

    org = Organization(name="Cascade Probe Org")
    user = User(email="cascade@example.com", full_name="Cascade Probe")
    db_session.add_all([org, user])
    await db_session.flush()
    org_id = org.id

    branch = Branch(org_id=org_id, name="Kurla", region="ap-south-1")
    db_session.add(branch)
    await db_session.flush()

    membership = Membership(
        user_id=user.id, org_id=org_id, branch_id=branch.id, role="teacher"
    )
    db_session.add(membership)
    await db_session.commit()

    membership_id = membership.id
    branch_id = branch.id
    db_session.expunge_all()

    await db_session.execute(
        sa.delete(Branch.__table__).where(Branch.__table__.c.id == branch_id)
    )
    await db_session.commit()

    survivor = (
        await db_session.execute(
            sa.select(Membership).where(Membership.id == membership_id)
        )
    ).scalar_one()

    assert survivor.branch_id is None, "ON DELETE SET NULL did not fire"
    assert survivor.org_id == org_id, "the membership must stay attached to its org"
    assert survivor.role == "teacher"
