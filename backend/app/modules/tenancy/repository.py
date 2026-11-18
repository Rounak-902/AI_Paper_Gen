"""Tenancy data access — doc 07 §11's repository layer, P1-T5's base class.

Every class here extends :class:`app.db.repository.BaseRepository`, so soft-delete
filtering, the ``org_id`` predicate, optimistic locking and the "no hard DELETE"
rule are inherited rather than restated. No service or router in this module emits
SQL of its own.

Two deliberate exceptions, both documented at their definition:

* :class:`OrganizationRepository` overrides ``_org_id_column`` because
  ``organizations`` has no ``org_id`` column — its ``id`` *is* the tenant key. Left
  alone, the base class would apply **no** tenant predicate to the one table where
  a missing predicate means "any tenant's organization", and ``organizations`` is
  RLS-exempt (revision 0025) so the database would not catch it either.
* :class:`IdentityRepository` does not extend ``BaseRepository`` at all. Its single
  query is deliberately *not* org-scoped — "which organizations does this user
  belong to" is the question asked before an org exists — and it is authorised by
  revision 0027's ``memberships_self_select`` RLS policy rather than by an
  application predicate.
"""

from __future__ import annotations

import uuid
from typing import Any, Optional, Sequence

from sqlalchemy import Select, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.db.repository import BaseRepository
from app.models.branch import Branch
from app.models.invitation import Invitation
from app.models.membership import Membership
from app.models.organization import Organization


class OrganizationRepository(BaseRepository[Organization]):
    """``organizations`` — the tenant root.

    Constructed with the org the caller is acting within, so that ``get_by_id``,
    ``update`` and ``soft_delete`` all refuse to touch any other organization even
    when handed its id.
    """

    model_class = Organization

    def _org_id_column(self) -> Any:
        """``organizations.id`` is the tenant key.

        The base implementation returns ``getattr(model, "org_id", None)``, which
        is ``None`` here — and a ``None`` org column makes ``_base_query``,
        ``update`` and ``soft_delete`` skip the tenant predicate entirely. On every
        other table that is safe, because RLS is the backstop; on this one it is
        not, because ``organizations`` is deliberately **excluded** from RLS in
        revision 0025 (a tenant has to be able to read its own org row, and there
        is no ``org_id`` for a policy to compare). Returning ``id`` restores the
        predicate on the only table where the application is the sole line of
        defence.
        """
        return Organization.id

    async def create(self, **kwargs: Any) -> Organization:
        """Insert the organization this repository is scoped to.

        Overridden for one reason: the base ``create`` injects
        ``kwargs["org_id"] = self._org_id`` whenever ``_org_id_column()`` is not
        ``None``, and ``Organization(org_id=...)`` is a ``TypeError`` — there is no
        such attribute. So the id is set instead, from the same value.

        This is why ``POST /orgs`` generates the organization's UUID in Python
        before inserting anything: the audit row for the creation has to be written
        in the same transaction, ``audit_logs``' RLS ``WITH CHECK`` requires
        ``app.org_id`` to equal the row's ``org_id``, and the org's id therefore has
        to be known before the transaction opens. See
        :func:`app.modules.tenancy.service.create_organization`.
        """
        supplied = kwargs.pop("id", None)
        if supplied is not None and supplied != self._org_id:
            raise ValueError(
                f"OrganizationRepository is scoped to {self._org_id} and cannot "
                f"create an organization with id {supplied}"
            )
        instance = Organization(id=self._org_id, **kwargs)
        self._session.add(instance)
        await self._session.flush()
        return instance


class BranchRepository(BaseRepository[Branch]):
    """``branches`` — org-scoped, so the base class needs no help."""

    model_class = Branch

    async def list_for_org(
        self,
        *,
        include_deleted: bool = False,
        limit: Optional[int] = None,
        offset: Optional[int] = None,
    ) -> Sequence[Branch]:
        """Every branch in the current org, oldest first.

        Ordered by ``created_at`` and then ``id``. The ``id`` tiebreak is not
        decoration: two branches created in the same transaction share a
        ``now()``, and an unstable sort under ``limit``/``offset`` silently drops
        and duplicates rows across pages.
        """
        return await self.find_many(
            include_deleted=include_deleted,
            order_by=(Branch.created_at, Branch.id),
            limit=limit,
            offset=offset,
        )

    async def name_exists(self, name: str, *, exclude_id: Optional[uuid.UUID] = None) -> bool:
        """Whether a live branch in this org already uses ``name``.

        The database enforces this with the partial unique index
        ``uq_branches_org_id_name``; this pre-check exists so the caller can raise
        a 409 with a useful message instead of surfacing an
        ``IntegrityError``. It is a *race-prone* check by nature — the index is
        still the authority, and the service treats an ``IntegrityError`` as the
        same conflict.
        """
        stmt = self._base_query().where(Branch.name == name)
        if exclude_id is not None:
            stmt = stmt.where(Branch.id != exclude_id)
        result = await self._session.execute(stmt.limit(1))
        return result.scalar_one_or_none() is not None


class MembershipRepository(BaseRepository[Membership]):
    """``memberships`` — org-scoped.

    NOTE on the duplicate. ``app/modules/auth/repository.py`` also defines a
    ``MembershipRepository``, and its docstring asks P1-T8 to delete it and absorb
    it here. That was **not** done, deliberately: the auth module's login flow
    needs a membership read, and importing this class into ``auth`` would make
    ``auth`` depend on ``tenancy`` while doc 07 §4.2 has the dependency running the
    other way ("Tenancy depends on: Auth module"). Both classes extend the same
    ``BaseRepository``, so the divergent-scoping hazard the note warns about is
    already mitigated by the shared base. Reported as a judgment call with a
    recommendation: the right resolution is a shared ``memberships`` repository in
    a module both can import, which is a refactor rather than part of this task.
    """

    model_class = Membership

    async def get_for_user(
        self, user_id: uuid.UUID, *, include_deleted: bool = False
    ) -> Optional[Membership]:
        """This user's membership in the current org, whatever its status.

        Returns the row even when ``status`` is ``invited`` or ``suspended``, which
        is what the invitation-accept path needs: it has to find and *activate* an
        existing invited row rather than insert a second one and collide with
        ``uq_memberships_user_id_org_id``.
        """
        rows = await self.find_many(
            Membership.user_id == user_id,
            include_deleted=include_deleted,
            limit=1,
        )
        return rows[0] if rows else None

    async def get_active_for_user(self, user_id: uuid.UUID) -> Optional[Membership]:
        """This user's **active** membership in the current org, or None.

        Branch-assigned rows sort after org-wide ones so that a user holding both
        resolves to the wider access, matching
        ``app/modules/auth/repository.py``'s ordering. In practice
        ``uq_memberships_user_id_org_id`` makes at most one row possible, so the
        ordering is defence against a future relaxation of that index rather than a
        live case.
        """
        rows = await self.find_many(
            Membership.user_id == user_id,
            Membership.status == "active",
            order_by=(
                Membership.branch_id.is_(None).desc(),
                Membership.created_at,
            ),
            limit=1,
        )
        return rows[0] if rows else None

    async def list_for_org(
        self,
        *,
        limit: Optional[int] = None,
        offset: Optional[int] = None,
    ) -> Sequence[Membership]:
        """Every live membership in the current org."""
        return await self.find_many(
            order_by=(Membership.created_at, Membership.id),
            limit=limit,
            offset=offset,
        )


class InvitationRepository(BaseRepository[Invitation]):
    """``invitations`` — org-scoped (revision 0026's RLS policy matches)."""

    model_class = Invitation

    async def get_by_token_hash(self, token_hash: str) -> Optional[Invitation]:
        """Find a live invitation by ``sha256(token)`` within the current org.

        The org scoping is not incidental. The accept endpoint is public, so the
        org for this repository comes from the token's own ``<org_uuid>.<secret>``
        prefix — which is untrusted input, and is therefore used *only* to set
        ``app.org_id`` so that RLS permits the read. Authentication is the hash
        match inside that scope. A caller who rewrites the prefix scopes the lookup
        to an org where their hash does not exist and gets the same "invalid or
        expired" answer as a caller who invented the token outright.
        """
        rows = await self.find_many(Invitation.token_hash == token_hash, limit=1)
        return rows[0] if rows else None

    async def list_for_org(
        self,
        *,
        status: Optional[str] = None,
        limit: Optional[int] = None,
        offset: Optional[int] = None,
    ) -> Sequence[Invitation]:
        """Invitations in the current org, newest first, optionally by status."""
        filters: list[Any] = []
        if status is not None:
            filters.append(Invitation.status == status)
        return await self.find_many(
            *filters,
            order_by=(Invitation.created_at.desc(), Invitation.id),
            limit=limit,
            offset=offset,
        )

    async def get_open_for_email(self, email: str) -> Optional[Invitation]:
        """An existing ``pending`` invitation for ``email`` in the current org.

        ``uq_invitations_org_id_email`` (partial on ``status = 'pending'``) is the
        authority; this read exists so a duplicate invite becomes a 409 with a
        message rather than an ``IntegrityError``.
        """
        rows = await self.find_many(
            Invitation.email == email,
            Invitation.status == "pending",
            limit=1,
        )
        return rows[0] if rows else None


class IdentityRepository:
    """The one **cross-org** read in this module. Not a ``BaseRepository``.

    Deliberately not a subclass, because everything ``BaseRepository`` provides is
    wrong for this query: there is no ``org_id`` to scope to, and applying one
    would defeat the purpose. The authorisation comes from revision 0027's
    ``memberships_self_select`` policy, which restricts the visible rows to
    ``user_id = NULLIF(current_setting('app.user_id', true), '')::uuid AND status =
    'active' AND deleted_at IS NULL``.

    Two properties of that arrangement are worth stating, because they are what
    make an un-scoped query safe here:

    1. The predicate's identity comes from ``app.user_id``, which
       ``get_identity_session`` sets from ``AuthenticatedUser.user_id`` and nothing
       else. ``user_id`` is **not** a parameter of any method below — there is no
       way for a caller to ask about a different user, because the query never
       names one.
    2. With ``app.user_id`` unset the ``NULLIF`` yields ``NULL``, ``user_id = NULL``
       is ``NULL``, and a ``NULL`` policy predicate is false. The query returns zero
       rows. It fails **closed**, and it does not raise — which is exactly what
       revision 0027's hardening of the org policy buys, and what proof 4's
       negative case checks.
    """

    def __init__(self, session: AsyncSession) -> None:
        self._session = session

    def _self_memberships(self) -> Select:
        """``SELECT`` over the caller's own active memberships, joined to their orgs.

        Note what is absent: any ``WHERE user_id = ...``. The row filter is the RLS
        policy. Adding an application predicate would require accepting a
        ``user_id`` argument, and an endpoint that accepts a user id is an endpoint
        that can be asked about somebody else.

        ``organizations`` is RLS-exempt, so the join returns the org's name and type
        rather than a bare list of ids — which is what doc 08 line 913's login
        ``orgs`` array and its org-picker screen need.

        Every column is explicitly labelled. ``memberships`` and ``organizations``
        both have a ``status``, and an unlabelled projection of both would make
        ``row.status`` mean whichever one SQLAlchemy deduplicated to — a bug that
        would read as "the org is suspended" when the membership was.
        """
        return (
            select(
                Membership.id.label("membership_id"),
                Membership.org_id.label("org_id"),
                Membership.role.label("role"),
                Membership.branch_id.label("branch_id"),
                Membership.status.label("membership_status"),
                Organization.name.label("org_name"),
                Organization.org_type.label("org_type"),
                Organization.status.label("org_status"),
            )
            .join(Organization, Organization.id == Membership.org_id)
            .where(Organization.deleted_at.is_(None))
            .order_by(Organization.name, Membership.org_id)
        )

    async def list_own_memberships(self) -> Sequence[Any]:
        """Every org the session's identity is an active member of.

        Returns SQLAlchemy ``Row`` objects with the columns named in
        :meth:`_self_memberships`. Rows, not ORM instances, on purpose: this is a
        projection across two tables and materialising ``Membership`` objects would
        invite a lazy load of ``Membership.organization`` after the transaction
        closed.
        """
        result = await self._session.execute(self._self_memberships())
        return result.all()

    async def get_own_membership_in(self, org_id: uuid.UUID) -> Optional[Any]:
        """The caller's own active membership in ``org_id``, or None.

        This is the authorisation read behind ``POST /auth/switch-org``. ``org_id``
        is a *narrowing* filter on rows RLS has already restricted to the caller —
        it cannot widen the result set, so naming an org the caller does not belong
        to returns nothing rather than someone else's membership.
        """
        stmt = self._self_memberships().where(Membership.org_id == org_id).limit(1)
        result = await self._session.execute(stmt)
        return result.first()


__all__ = [
    "BranchRepository",
    "IdentityRepository",
    "InvitationRepository",
    "MembershipRepository",
    "OrganizationRepository",
]
