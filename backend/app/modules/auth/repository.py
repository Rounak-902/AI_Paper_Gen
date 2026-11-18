"""Auth data access — ``users`` and the membership check, through P1-T5's repository.

Deliberately thin, and deliberately built on :class:`app.db.repository.BaseRepository`
rather than beside it. The brief's constraint is the reason: credential storage goes
through the same soft-delete and optimistic-locking path as everything else, so a user
row cannot be resurrected by a login or updated without a version check just because the
code that touches it happens to be authentication.

Two facts from P1-T5 make that work without any change to the base class:

* ``BaseRepository._org_id_column()`` returns ``getattr(model_class, "org_id", None)``,
  and ``_base_query`` skips the org predicate when that is ``None`` while keeping the
  ``deleted_at IS NULL`` filter. ``users`` has no ``org_id`` column, so
  :class:`UserRepository` inherits correct behaviour rather than needing an override.
* ``memberships`` *does* have ``org_id``, so :class:`MembershipRepository` gets the org
  predicate automatically — belt and braces alongside the RLS policy that already
  constrains the same column.

Which session each one needs, and why it is not interchangeable
--------------------------------------------------------------
``users`` is one of the nine tables migration 0025 excludes from RLS ("global
identity"), so a user lookup must use ``get_unscoped_session``: there is no
``app.org_id`` to set at login, because finding out which orgs the caller belongs to is
the thing login is trying to do.

``memberships`` is RLS-protected with a ``org_id = current_setting('app.org_id')::uuid``
policy and no self-read exception, and there is no ``app.user_id`` GUC anywhere in the
schema or the docs. As ``app_user`` (NOBYPASSRLS), a query for "all memberships of user
X" therefore returns zero rows no matter what. That is not a bug to work around here —
it is why this task validates a *specific* requested ``orgId`` inside
``get_tenant_session(orgId)``, where the policy is satisfied, and leaves "list the orgs
this user belongs to" to P1-T8's ``GET /orgs``, which doc 07 §4.2 assigns to the tenancy
module anyway.
"""

from __future__ import annotations

import logging
import uuid
from typing import Optional

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.db.repository import BaseRepository
from app.models.membership import Membership
from app.models.user import User

logger = logging.getLogger("papergen.auth.repository")


class UserRepository(BaseRepository[User]):
    """``users`` access for authentication.

    Instantiated with ``org_id=None``: ``users`` is global, and passing a real org would
    imply a scoping this table does not have.
    """

    model_class = User

    def __init__(self, session: AsyncSession) -> None:
        super().__init__(session, org_id=None)

    async def get_by_email(self, email: str) -> Optional[User]:
        """Look up an active-or-not user by email, excluding soft-deleted rows.

        ``_base_query`` supplies the ``deleted_at IS NULL`` filter, which is the part
        that matters: without it a deactivated account could still authenticate, and the
        partial unique index ``uq_users_email`` (``WHERE deleted_at IS NULL``) means a
        deleted address can legitimately be re-registered — so a query that ignored
        ``deleted_at`` could match two rows for one address.

        ``email`` is compared with ``==`` against a ``CITEXT`` column, so the comparison
        is case-insensitive in the database. No ``.lower()`` is applied here on purpose:
        normalising in Python as well would mean two different notions of equality (one
        in the app, one in the index) that agree today and could drift.

        Account *status* is not filtered here. ``USER_STATUSES`` includes ``disabled``
        and ``suspended``, and a suspended user presenting the right password should be
        refused for that reason with a distinct server-side log line rather than being
        indistinguishable from "no such user" at the query level. The service layer makes
        that decision; see :mod:`app.modules.auth.service`.
        """
        stmt = self._base_query().where(User.email == email)
        result = await self._session.execute(stmt)
        return result.scalar_one_or_none()


class MembershipRepository(BaseRepository[Membership]):
    """``memberships`` access for the login-time org check.

    NOTE FOR P1-T8: this class exists only to answer "is user U an active member of org
    O, and with what role?" for token minting. P1-T8 owns memberships CRUD per doc 07
    §4.2 and will need its own richer repository in ``app/modules/tenancy/``. If both
    exist, this one should be deleted and its single method absorbed there — two
    repositories over one table is the parallel-work hazard flagged in the T7/T8
    dependency check, not a design intent.
    """

    model_class = Membership

    def __init__(self, session: AsyncSession, org_id: uuid.UUID) -> None:
        super().__init__(session, org_id=org_id)

    async def get_active_for_user(self, user_id: uuid.UUID) -> Optional[Membership]:
        """The caller's active membership in this repository's org, or ``None``.

        ``status == "active"`` is required rather than merely preferred: ``invited``
        means the user has been offered access and has not accepted, and ``suspended``
        means access was withdrawn. Treating either as a member would let an invitation
        act as an authorization.

        The org predicate is not written here — ``_base_query`` adds it from
        ``self._org_id``, and the RLS policy enforces the same thing independently at the
        database. Both layers agreeing is the point; if this method is ever called
        outside a tenant-scoped session the RLS policy returns nothing and the caller
        sees "not a member", which is the safe direction to fail.
        """
        # ``.first()`` rather than ``.scalar_one_or_none()``: a user can hold more than
        # one membership row in an org when branch-scoped rows exist (``branch_id`` is
        # nullable and not part of any uniqueness constraint), so "more than one" is a
        # legitimate state that must not raise. Ordering puts the org-wide row first so
        # the role resolved for the token is the least surprising one.
        stmt = (
            self._base_query()
            .where(
                Membership.user_id == user_id,
                Membership.status == "active",
            )
            .order_by(Membership.branch_id.is_(None).desc(), Membership.created_at)
        )
        result = await self._session.execute(stmt)
        return result.scalars().first()


async def count_active_memberships(session: AsyncSession, org_id: uuid.UUID) -> int:
    """Diagnostic helper for the verification harness. Not used by request paths."""
    stmt = select(Membership).where(
        Membership.org_id == org_id,
        Membership.status == "active",
        Membership.deleted_at.is_(None),
    )
    return len((await session.execute(stmt)).scalars().all())


__all__ = ["UserRepository", "MembershipRepository", "count_active_memberships"]
