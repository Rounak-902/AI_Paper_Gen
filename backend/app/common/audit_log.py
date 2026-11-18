"""Durable service-layer audit rows (P1-T8).

Doc 07's two answers, and why this file is the second one
--------------------------------------------------------
Doc 07 states the placement of audit writes twice, and contradicts itself:

* §4.17 line 581 (also lines 198 and 639): audit is "Written by an interceptor on
  every mutating handler, **not by business services**."
* §13.2 line 1209 shows the exact opposite, inside a service method's
  transaction::

      self._s.add(AuditLog(org_id=org.id, actor_id=user.id, action="paper.create",
                           entity="generated_papers", entity_id=paper.id))

Both are quoted verbatim in the P1-T8 report as an unresolved contradiction in the
document. This implementation follows §13.2, for three reasons that the
interceptor reading cannot satisfy:

1. **Atomicity.** An interceptor runs after the handler returns, on a separate
   transaction. A crash between commit and interception loses the audit row for a
   mutation that happened; a rollback after interception records one that did not.
   Inside the service transaction, "the change happened" and "the change is
   recorded" are the same commit.
2. **Attribution.** ``app/common/middleware/audit.py`` — the interceptor that
   exists — derives its actor from the ``X-Debug-User-Id`` *request header*. Its
   own docstring says so, and P1-T7's report listed it as "a real, confirmed
   forgeability gap, not a benign placeholder". A middleware cannot cheaply do
   better: it would have to decode and verify the bearer token a second time,
   duplicating ``get_current_user``'s work and its security decisions in a place
   where a divergence between the two would be invisible.
3. **Subject identity.** Only the service knows which row it touched. An
   interceptor sees a method, a path and a status code, so ``entity_id`` for a
   ``POST`` is unavailable to it unless it parses the response body.

``app/common/middleware/audit.py`` is deliberately **not modified**. It is an
operational request log that happens to be called "audit", it emits log lines and
not rows, and P1-T8's brief forbids editing it. The recommendation to either fix
or rename it is in the report.

What this module is
-------------------
One function. It does not open a session, does not commit, and does not catch
anything — it adds a row to the caller's already-open transaction and returns it.
That is the whole point: the caller's ``async with session.begin()`` block decides
whether both the mutation and its audit row survive.

RLS, and the constraint it puts on callers
------------------------------------------
``audit_logs`` is RLS-protected (revision 0025, ``_DIRECT_ORG_ID_NULLABLE``) with::

    USING (org_id = current_setting('app.org_id')::uuid OR org_id IS NULL)
    WITH CHECK (org_id = current_setting('app.org_id')::uuid)

Note that ``WITH CHECK`` has **no** ``OR org_id IS NULL``. Two consequences that
every caller here has to respect, and that are easy to get wrong:

* As ``app_user`` you **cannot** insert a platform-level row with
  ``org_id IS NULL``. Every audit row written by this application belongs to an
  org.
* You cannot insert *any* row unless ``app.org_id`` is set to that org. So
  ``POST /orgs`` — which creates the org the audit row will belong to — must
  generate the organization's UUID in Python *before* the INSERT and open its
  session with ``get_identity_session(user_id, org_id=<that new uuid>)``. See
  ``app.modules.tenancy.service.create_organization``.

Revision 0024 also ``REVOKE``d ``UPDATE`` on this table from ``app_user`` and
never granted ``DELETE`` on anything, so these rows are insert-only with no
correction path. ADR-004's append-only rule, enforced by privilege rather than by
convention.

Action vocabulary
-----------------
``<entity>.<verb>``, lowercase, matching doc 07 §13.2's ``"paper.create"`` and
§4.2's emitted event names (``membership.created``, ``membership.role_changed``,
``invitation.sent``, ``org.settings_updated``). ``audit_logs.action`` deliberately
carries no CHECK constraint (see ``app/models/audit.py``), so the vocabulary is a
convention and the constants in :data:`ACTIONS` are the place to keep it honest.
"""

from __future__ import annotations

import logging
import uuid
from typing import Any, Mapping, Optional

from sqlalchemy.ext.asyncio import AsyncSession

from app.models.audit import AuditLog

logger = logging.getLogger("papergen.audit")


class ACTIONS:
    """The reviewed audit ``action`` vocabulary used by service-layer writes.

    A class rather than a module-level block of constants so that a reader sees
    the whole vocabulary in one place and a typo at a call site is an
    ``AttributeError`` at import time instead of a string that silently never
    matches a report filter.

    Four of these are doc 07 §4.2's "Emits" list verbatim
    (``membership.created``, ``membership.role_changed``, ``invitation.sent``,
    ``org.settings_updated``). The rest are authored on the same
    ``<entity>.<verb>`` pattern. P2 modules extend that vocabulary here rather
    than scattering string literals across services.
    """

    ORG_CREATED = "org.created"
    ORG_SETTINGS_UPDATED = "org.settings_updated"
    ORG_DELETED = "org.deleted"
    BRANCH_CREATED = "branch.created"
    BRANCH_UPDATED = "branch.updated"
    BRANCH_DELETED = "branch.deleted"
    INVITATION_SENT = "invitation.sent"
    INVITATION_REVOKED = "invitation.revoked"
    INVITATION_ACCEPTED = "invitation.accepted"
    MEMBERSHIP_CREATED = "membership.created"
    MEMBERSHIP_ROLE_CHANGED = "membership.role_changed"
    #: A membership changed in some way other than its role (status, branch).
    #: Distinct from ``membership.role_changed`` so that a report filtering for
    #: privilege changes is not diluted by branch reassignments.
    MEMBERSHIP_UPDATED = "membership.updated"
    #: An existing ``status='invited'`` membership became ``active`` because the
    #: invitee accepted. Deliberately not ``membership.created`` — the row already
    #: existed, and claiming creation would make the audit trail contradict the
    #: earlier ``invitation.sent`` row that created it.
    MEMBERSHIP_ACTIVATED = "membership.activated"
    USER_CREATED = "user.created"
    AUTH_ORG_SWITCHED = "auth.org_switched"

    # P2-T1 global curriculum mutations.  ``app_user`` cannot insert a truly
    # platform-scoped audit row (audit_logs WITH CHECK requires the active org), so
    # these records are attributed to the super-admin's verified active-org context.
    # The curriculum service documents that temporary infrastructure constraint.
    BOARD_CREATED = "board.created"
    BOARD_UPDATED = "board.updated"
    BOARD_DELETED = "board.deleted"
    CLASS_CREATED = "class.created"
    CLASS_UPDATED = "class.updated"
    CLASS_DELETED = "class.deleted"
    SUBJECT_CREATED = "subject.created"
    SUBJECT_UPDATED = "subject.updated"
    SUBJECT_DELETED = "subject.deleted"
    CHAPTER_CREATED = "chapter.created"
    CHAPTER_UPDATED = "chapter.updated"
    CHAPTER_DELETED = "chapter.deleted"
    OUTCOME_CREATED = "outcome.created"
    OUTCOME_UPDATED = "outcome.updated"
    OUTCOME_DELETED = "outcome.deleted"
    CURRICULUM_VERSION_CREATED = "curriculum_version.created"
    CURRICULUM_VERSION_UPDATED = "curriculum_version.updated"
    CURRICULUM_VERSION_PUBLISHED = "curriculum.version_published"
    CURRICULUM_VERSION_SUPERSEDED = "curriculum_version.superseded"
    CURRICULUM_VERSION_DELETED = "curriculum_version.deleted"


def record_audit(
    session: AsyncSession,
    *,
    org_id: uuid.UUID,
    actor_id: Optional[uuid.UUID],
    action: str,
    entity: Optional[str] = None,
    entity_id: Optional[uuid.UUID] = None,
    meta: Optional[Mapping[str, Any]] = None,
    ip: Optional[str] = None,
) -> AuditLog:
    """Add one durable ``audit_logs`` row to an open transaction.

    Not ``async``: ``Session.add`` does no I/O. The INSERT is emitted when the
    caller's transaction flushes or commits, which is exactly the coupling this
    function exists to create — there is no way to commit the mutation and lose
    the audit row, because they are the same commit.

    :param session: an **already-open** session whose transaction also carries the
        mutation being recorded. Must have ``app.org_id`` set to ``org_id`` or the
        INSERT is refused by RLS (see the module docstring).
    :param org_id: the tenant the event belongs to. Required, not optional — a
        ``NULL`` here is rejected by ``audit_logs``' ``WITH CHECK`` clause when
        inserting as ``app_user``, so accepting ``None`` would only defer a
        confusing database error.
    :param actor_id: the authenticated principal, from
        ``AuthenticatedUser.user_id`` or ``OrgContext.user_id``. **Never** from a
        header, a body field or a path parameter. ``None`` is permitted for
        exactly one case — an unauthenticated invitation acceptance, where the
        actor's user row is created by the same transaction; see
        ``app.modules.tenancy.service.accept_invitation``, which passes the newly
        created id and therefore does not use ``None`` either.
    :param meta: JSONB payload. Keep it to facts about the change (old and new
        values, the target email). Never a password, a token, or a token hash.
    :returns: the pending :class:`~app.models.audit.AuditLog` instance, so a
        caller that wants its ``id`` can ``await session.flush()``.
    """
    row = AuditLog(
        org_id=org_id,
        actor_id=actor_id,
        action=action,
        entity=entity,
        entity_id=entity_id,
        # dict(...) rather than the mapping itself: JSONB serialisation walks the
        # object at flush time, and passing a live mapping that the caller mutates
        # afterwards would record whatever it happened to contain then.
        meta=dict(meta) if meta is not None else None,
        ip=ip,
    )
    session.add(row)

    # Emitted at INFO with the real actor, alongside the durable row, so that
    # ``middleware/audit.py``'s header-derived line and this one can be compared in
    # a single log stream during the transition. If the two disagree on ``actor``,
    # the middleware's value was forged.
    logger.info(
        "audit_row_staged action=%s org_id=%s actor_id=%s entity=%s entity_id=%s",
        action,
        org_id,
        actor_id,
        entity,
        entity_id,
    )
    return row


__all__ = ["ACTIONS", "record_audit"]
