"""The guard chain — ``src/common/deps.py`` in doc 07 §3 (P1-T6).

Doc 07 §7.5 defines the chain as a table of five dependencies, reproduced verbatim:

    | Dependency            | Responsibility                                          |
    | get_current_user      | Decodes the access token, loads the user (401 on failure)|
    | get_org_context       | Resolves the active organization + role (403 on mismatch)|
    | require_roles(*roles) | Guards an endpoint by role (403)                        |
    | get_scope             | Resolves branch/subject scope for filtering             |
    | get_db                | Yields an AsyncSession in a transaction with            |
    |                       | SET LOCAL app.org_id already applied                    |

and §6 fixes the order: "get_current_user -> get_org_context -> require_roles ->
get_scope -> get_db", with the note that matters most here:

    "The dependency chain runs *before* the request body is validated ... ``get_db``
     opens the transaction last, after the org context is known."

That ordering is what makes proof 3 of this task's verification meaningful: an
unauthenticated or org-less request is rejected without a database transaction ever
being opened and without the handler body running.

P2-T4 adds :func:`get_global_write_db` as a separate opt-in adapter for the one question
creation route whose narrowly scoped RLS policy needs a verified actor UUID. It is not
part of the default chain above, and :func:`get_db` remains the frozen tenant-only
dependency. This clarification intentionally lives at module scope so the P1-T7
``get_current_user`` source segment remains byte-identical to its baseline.

Deny by default
---------------
Doc 07 §7.5: "Routers declare the chain once ... Endpoints add ``require_roles`` only
where the rule is narrower than the router default. **Nothing is public unless it is
explicitly declared so.**" Routers therefore carry the guards in
``APIRouter(dependencies=[...])`` rather than each endpoint restating them —
forgetting a decorator then produces a *guarded* route, not an open one.

What is NOT here, and why
-------------------------
This module builds the five dependencies above and nothing else. Doc 07 §6's
middleware stack also lists a rate limiter and a body-size cap; the built/deferred
split for those is documented in ``app/common/middleware/__init__.py``.
"""

from __future__ import annotations

import logging
import uuid
from dataclasses import dataclass
from typing import AsyncIterator, Callable, Optional

from fastapi import Depends, Request
from sqlalchemy.ext.asyncio import AsyncSession

from app.common.errors import AuthenticationError, AuthorizationError
from app.core.config import Settings, get_settings
from app.db.session import get_identity_session, get_tenant_session
from app.models.membership import MEMBERSHIP_ROLES
from app.modules.auth.security import InvalidToken, TokenExpired, decode_access_token
from app.modules.auth.tokens import is_session_revoked

logger = logging.getLogger("papergen.api.guards")

#: The only client-visible message any credential failure produces. One string for
#: "absent", "malformed", "bad signature", "expired", "wrong type" and "revoked", so that
#: no response distinguishes which part of a forged credential was wrong. The specific
#: reason goes to ``log_detail``, which ``errors.py`` never serialises.
_CREDENTIAL_REQUIRED = "Authentication is required."

#: The ``Authorization`` scheme, compared case-insensitively. RFC 7235 §2.1 makes the
#: scheme token case-insensitive, so a client sending ``bearer`` must not be refused for
#: capitalisation — that would be a spec-violating rejection dressed as an auth failure.
BEARER_SCHEME = "bearer"

# ---------------------------------------------------------------------------
# Retired placeholder constants — kept deliberately, and here is exactly why
# ---------------------------------------------------------------------------
#: P1-T7 removed the header-based authentication path from :func:`get_current_user`.
#: These three names no longer authenticate anything: a request carrying
#: ``X-Debug-User-Id`` and no bearer token now gets a 401 in every environment,
#: ``production`` included.
#:
#: They are not deleted, because two files outside this task's permitted blast radius
#: still reference them and the brief forbids modifying either:
#:
#: * ``app/common/middleware/audit.py`` imports ``DEBUG_USER_ID_HEADER`` and
#:   ``DEBUG_ORG_ID_HEADER`` (lines 38, 70-71) to label its mutation log lines. Deleting
#:   the constants would break that P1-T6 middleware at import time.
#: * :func:`get_org_context` below interpolates ``DEBUG_ORG_ID_HEADER`` into one
#:   ``log_detail`` string. That string is now inaccurate — the org arrives in the
#:   ``activeOrgId`` token claim, not a header — but it is server-side only, never
#:   serialised, and correcting it would mean editing ``get_org_context``, which the brief
#:   forbids. Reported as a known stale log message rather than fixed silently.
#:
#: CONSEQUENCE, stated plainly: because ``audit.py`` still reads the actor from a
#: client-supplied header, the audit *log line*'s ``actor=`` field remains forgeable even
#: though authentication no longer is. Fixing it means either teaching that middleware to
#: decode the token itself or moving the audit write into the service layer, which its own
#: docstring already assigns to P1-T8. This is the highest-value follow-up in the module.
DEBUG_USER_ID_HEADER = "X-Debug-User-Id"
DEBUG_ORG_ID_HEADER = "X-Debug-Org-Id"
DEBUG_ROLES_HEADER = "X-Debug-Roles"

#: Environments in which development-only surfaces are mounted. This no longer gates any
#: authentication decision — :func:`get_current_user` is now identical in every
#: environment — but ``app/main.py`` uses it to decide whether to mount the diagnostics
#: routers, and ``app/modules/auth/router.py`` uses it to decide whether the refresh
#: cookie gets the ``Secure`` attribute (a ``Secure`` cookie is not stored over plain
#: http, so hardcoding it on would break local development).
STUB_AUTH_ENVIRONMENTS: frozenset[str] = frozenset({"development", "test", "local"})


@dataclass(frozen=True, slots=True)
class AuthenticatedUser:
    """The principal, as far as P1-T6 is concerned.

    Deliberately minimal. P1-T7 will replace the *construction* of this object with
    real token decoding and a user lookup; every consumer downstream should keep
    working, which is why the guards below depend on this type and never on the
    headers directly.
    """

    user_id: uuid.UUID
    #: Raw role strings as presented. Validated against ``MEMBERSHIP_ROLES`` in
    #: :func:`get_org_context`, not here, so that an unknown role is an authorization
    #: failure (403) rather than an authentication failure (401).
    roles: tuple[str, ...]
    #: The org the caller is asking to act within. ``None`` when absent — turning that
    #: into a 403 is :func:`get_org_context`'s job.
    requested_org_id: Optional[uuid.UUID]
    #: True when this principal came from the placeholder headers rather than a real
    #: credential. Logged on every guarded request so the audit trail cannot silently
    #: attribute stub traffic to real authentication.
    is_stub: bool = True


@dataclass(frozen=True, slots=True)
class OrgContext:
    """Resolved tenant context — the output of :func:`get_org_context`.

    ``org_id`` is what ends up in ``SET LOCAL app.org_id`` (P1-T5's
    ``get_tenant_session``), so this object is the boundary between "what the request
    claimed" and "what the database will enforce".
    """

    org_id: uuid.UUID
    user_id: uuid.UUID
    role: str
    roles: tuple[str, ...]


@dataclass(frozen=True, slots=True)
class RequestScope:
    """Doc 07 §7.5's ``get_scope`` — "Resolves branch/subject scope for filtering".

    Doc 07 §7.3 gives the rule this encodes: "A ``teacher`` sees only their own
    branch's data; a ``coaching_admin`` sees the whole org." Doc 03's ``memberships``
    table carries the ``branch_id`` that narrows a membership, and
    ``app/models/membership.py`` records that ``branch_id IS NULL`` means org-wide
    access.

    JUDGMENT — and a deliberately shallow one. Doc 07 §7.5 does not say what
    ``get_scope`` returns, and the tables it would filter (questions, papers) do not
    exist as routes until P2. So this resolves the two facts that are already
    knowable from the guard chain — the org and whether the caller is org-wide — and
    stops there. ``branch_id`` is carried but always ``None`` in P1-T6, because
    resolving it requires reading the caller's ``memberships`` row, and that read is
    part of P1-T8's tenancy work rather than something to guess at here. Flagged in
    the report as under-built on purpose rather than complete.
    """

    org_id: uuid.UUID
    user_id: uuid.UUID
    role: str
    branch_id: Optional[uuid.UUID] = None
    subject_ids: Optional[tuple[uuid.UUID, ...]] = None

    @property
    def is_org_wide(self) -> bool:
        """True when the caller's reads are not narrowed to a single branch."""
        return self.branch_id is None


def _parse_uuid(raw: str) -> Optional[uuid.UUID]:
    """Lenient UUID parse, returning ``None`` instead of raising.

    Retained after P1-T7 removed its only call site (the placeholder header path).
    ``get_scope`` and P1-T8's membership-based ``branch_id`` resolution both need to turn
    an untrusted string into a UUID without an exception, and re-adding this three lines
    later would be churn. Flagged as currently-unused rather than deleted-and-restored.
    """
    try:
        return uuid.UUID(raw.strip())
    except (ValueError, AttributeError):
        return None


# ---------------------------------------------------------------------------
# 1. get_current_user — 401
# ---------------------------------------------------------------------------
async def get_current_user(
    request: Request,
    settings: Settings = Depends(get_settings),
) -> AuthenticatedUser:
    """Doc 07 §7.5: "Validates the JWT, resolves the user, rejects expired/invalid/
    revoked tokens (401)".

    P1-T7 replaced the body of this function and nothing else about it. The signature,
    the return type, and its position at the head of the guard chain are byte-identical
    to what P1-T6 froze, which is what lets ``get_org_context``, ``require_roles``,
    ``get_scope`` and ``get_db`` remain untouched.

    What actually verifies the credential
    -------------------------------------
    ``decode_access_token`` (``app/modules/auth/security.py``) performs a real HMAC
    signature check with an explicit single-algorithm allow-list. That allow-list is the
    security property, not the decode: a token whose header claims ``alg: "none"`` — or
    ``RS256``, the key-confusion variant — does not match and is rejected before a single
    claim is read. Shape-checking a decoded payload would accept an unsigned token that
    looked perfect, which is exactly the gap ``frontend/lib/mock-auth.ts`` has and the one
    this task exists to close on the server.

    Revocation, and why it costs a Redis round trip
    ----------------------------------------------
    §7.5 requires rejecting **revoked** tokens, and a signed 15-minute JWT is by
    construction unrevocable on its own — nothing about it changes when a session ends.
    The ``sid`` claim names the refresh-token family from §7.3, so one ``EXISTS`` against
    the family's tombstone answers it. That makes §7.3's "revoke entire session family"
    and §7.5's "reject revoked tokens" the same fact rather than two mechanisms that can
    disagree.

    Failure modes, and the one that is deliberately a 500
    ----------------------------------------------------
    Every *credential* problem — absent header, wrong scheme, bad signature, expired,
    wrong token type, revoked session — raises ``AuthenticationError`` with the one
    identical client-visible message. Only ``ehRef`` varies, and only for expiry (EH-15),
    so a client can tell "refresh me" from "log in again" while an attacker learns
    nothing about which part of a forged token was wrong.

    A Redis *outage* is not a credential problem and is not converted into one.
    ``RedisUnavailable`` and transport errors propagate to P1-T6's unhandled-exception
    handler and become a 500. This is still fail-closed — the request is denied and the
    handler never runs — but it reports a server fault as a server fault. Answering 401
    during an outage would tell every user their credentials had become invalid, and it is
    the same distinction P1-T6 drew when it mapped ``TenantContextMissing`` to 500 rather
    than 403: a request-level auth failure and a server-side infrastructure failure are
    different classes with different correct status codes.
    """
    header = request.headers.get("Authorization")
    if not header:
        raise AuthenticationError(
            _CREDENTIAL_REQUIRED, log_detail="no Authorization header presented"
        )

    scheme, _, raw_token = header.partition(" ")
    token = raw_token.strip()
    if scheme.lower() != BEARER_SCHEME or not token:
        # Same client-visible message as an absent header. Distinguishing "malformed" from
        # "absent" tells an attacker which half of a guess landed — the principle P1-T6
        # already applied to the placeholder header and doc 07 §20.4 states for emails.
        raise AuthenticationError(
            _CREDENTIAL_REQUIRED,
            log_detail=f"unsupported authorization scheme {scheme!r} or empty token",
        )

    try:
        claims = decode_access_token(token, settings)
    except TokenExpired as exc:
        raise AuthenticationError(
            _CREDENTIAL_REQUIRED, eh_ref="EH-15", log_detail=f"token expired: {exc}"
        ) from exc
    except InvalidToken as exc:
        raise AuthenticationError(
            _CREDENTIAL_REQUIRED, log_detail=f"token rejected: {exc}"
        ) from exc

    if await is_session_revoked(claims.session_id):
        logger.warning(
            "revoked_session_token_refused user_id=%s sid=%s path=%s",
            claims.user_id,
            claims.session_id,
            request.url.path,
        )
        raise AuthenticationError(
            _CREDENTIAL_REQUIRED,
            eh_ref="EH-15",
            log_detail=(
                f"session {claims.session_id} is revoked; token for user "
                f"{claims.user_id} refused"
            ),
        )

    # ``roles`` is a tuple because that is what P1-T6's dataclass declares and what
    # ``get_org_context`` iterates. Doc 07 §7.1's payload carries a single ``role``, so the
    # tuple has at most one member — a narrowing of the stub's multi-role capability, not a
    # contract change. An org-less token yields an empty tuple, which ``get_org_context``
    # already handles (it raises 403 on ``requested_org_id is None`` before roles matter,
    # and again on an empty role set).
    return AuthenticatedUser(
        user_id=claims.user_id,
        roles=(claims.role,) if claims.role else (),
        requested_org_id=claims.active_org_id,
        is_stub=False,
    )


# ---------------------------------------------------------------------------
# 2. get_org_context — 403
# ---------------------------------------------------------------------------
async def get_org_context(
    request: Request,
    current_user: AuthenticatedUser = Depends(get_current_user),
) -> OrgContext:
    """Doc 07 §7.5: "Resolves the active organization + role (403 on mismatch)".

    This is the guard that makes ``TenantContextMissing`` unreachable from a request.
    P1-T5's ``get_tenant_session`` raises it when handed ``org_id=None``; because this
    dependency rejects an org-less request with a 403 before ``get_db`` runs, a
    request can never be the cause. If that exception is ever seen in production it
    means some code path opened a session without going through the chain — which is
    exactly what P1-T5's docstring calls "the dependency chain has a hole", and why
    ``errors.py`` maps it to a 500 rather than a 403.

    ``ehRef`` is EH-14, whose doc 01 entry mandates a "Generic authorization message";
    the specific reason goes to ``log_detail`` and stays server-side.
    """
    if current_user.requested_org_id is None:
        logger.warning(
            "org_context_missing user_id=%s path=%s",
            current_user.user_id,
            request.url.path,
        )
        raise AuthorizationError(
            "You do not have access to this resource.",
            log_detail=f"missing or unparseable {DEBUG_ORG_ID_HEADER}",
        )

    # Role resolution. P1-T7/P1-T8 will read this from ``memberships``; until then the
    # presented roles are the source. An unknown role string is rejected rather than
    # ignored: silently dropping it would leave a caller with an empty role set that
    # ``require_roles`` then denies with a confusing message, and accepting it would
    # let an arbitrary string past a vocabulary that ADR-011 pins to exactly three
    # values.
    unknown = [r for r in current_user.roles if r not in MEMBERSHIP_ROLES]
    if unknown:
        logger.warning(
            "unknown_role_rejected user_id=%s roles=%s path=%s",
            current_user.user_id,
            unknown,
            request.url.path,
        )
        raise AuthorizationError(
            "You do not have access to this resource.",
            log_detail=f"roles not in MEMBERSHIP_ROLES: {unknown}",
        )

    if not current_user.roles:
        logger.warning(
            "no_role_for_org user_id=%s org_id=%s path=%s",
            current_user.user_id,
            current_user.requested_org_id,
            request.url.path,
        )
        raise AuthorizationError(
            "You do not have access to this resource.",
            log_detail=f"no role presented for org {current_user.requested_org_id}",
        )

    # The "active" role is the most privileged one presented, so that a user holding
    # both teacher and coaching_admin is not denied an admin route. MEMBERSHIP_ROLES
    # is ordered least-to-most privileged in app/models/membership.py
    # ("teacher", "coaching_admin", "super_admin"), which this relies on.
    role = max(current_user.roles, key=MEMBERSHIP_ROLES.index)

    logger.info(
        "org_context_resolved user_id=%s org_id=%s role=%s stub_auth=%s path=%s",
        current_user.user_id,
        current_user.requested_org_id,
        role,
        current_user.is_stub,
        request.url.path,
    )

    return OrgContext(
        org_id=current_user.requested_org_id,
        user_id=current_user.user_id,
        role=role,
        roles=current_user.roles,
    )


# ---------------------------------------------------------------------------
# 3. require_roles(*roles) — 403
# ---------------------------------------------------------------------------
def require_roles(*roles: str) -> Callable[..., OrgContext]:
    """Doc 07 §7.5: "Guards an endpoint by role (403)".

    A factory, used as ``dependencies=[Depends(require_roles("coaching_admin"))]`` or
    as a value dependency when the handler also wants the context.

    Role names are validated at *import* time, not per request. A typo in
    ``require_roles("coaching-admin")`` would otherwise be a route that silently
    denies everyone, and that is the kind of bug that survives to production because
    nothing about it looks like a failure.
    """
    unknown = [r for r in roles if r not in MEMBERSHIP_ROLES]
    if unknown:
        raise ValueError(
            f"require_roles() got unknown role(s) {unknown}; "
            f"MEMBERSHIP_ROLES is {list(MEMBERSHIP_ROLES)}"
        )
    allowed = frozenset(roles)

    async def _require(
        request: Request,
        org_context: OrgContext = Depends(get_org_context),
    ) -> OrgContext:
        if not allowed.intersection(org_context.roles):
            logger.warning(
                "role_denied user_id=%s org_id=%s have=%s need=%s path=%s",
                org_context.user_id,
                org_context.org_id,
                list(org_context.roles),
                sorted(allowed),
                request.url.path,
            )
            raise AuthorizationError(
                "You do not have access to this resource.",
                log_detail=(
                    f"role {list(org_context.roles)} not in {sorted(allowed)}"
                ),
            )
        return org_context

    return _require


# ---------------------------------------------------------------------------
# 4. get_scope
# ---------------------------------------------------------------------------
async def get_scope(
    org_context: OrgContext = Depends(get_org_context),
) -> RequestScope:
    """Doc 07 §7.5: "Resolves branch/subject scope for filtering".

    P1-T8 changed **only this function** in this module. ``branch_id`` was hardcoded
    ``None``; it is now read from the caller's ``memberships`` row, which is doc 07
    §8.2's rule ("``memberships.branch_id`` narrows a member to one branch; null means
    org-wide access") actually applied rather than described.

    STALE DOCSTRING ABOVE, flagged rather than edited. :class:`RequestScope`'s
    docstring still says "``branch_id`` is carried but always ``None`` in P1-T6,
    because resolving it requires reading the caller's ``memberships`` row". The first
    clause is now false and the second is exactly what this function does. It is left
    as written because this task's brief authorises one edit to this file — "This is
    the one edit to ``deps.py`` you're authorized to make; every other guard function
    in that file stays untouched" — and correcting a neighbouring class docstring is
    outside that. Reported in the P1-T8 report alongside the other known-stale string
    in this module (see :data:`DEBUG_ORG_ID_HEADER`), and it is a two-line fix for
    whoever owns the next change here.

    Deferred import, and why it has to be deferred
    ---------------------------------------------
    ``app.modules.tenancy.service`` imports :class:`OrgContext` from this module, so a
    module-level ``from app.modules.tenancy.service import resolve_branch_id`` would
    be a circular import — ``app.common.deps`` -> ``app.modules.tenancy.service`` ->
    ``app.common.deps`` — and would fail at interpreter start, not at request time.
    The import is therefore inside the function body, where it runs after both modules
    are fully initialised. This is not a style choice and it is not free: it is a
    ``sys.modules`` dict lookup per call, which is negligible next to the query below.

    The alternative considered and rejected was moving ``resolve_branch_id`` into
    ``app/db/`` or a new shared module so the import could be top-level. That means
    creating or editing a P1-T5 file to accommodate a P1-T8 dependency, and it would
    put a tenancy business rule (what a null ``branch_id`` means) outside the tenancy
    module. Recorded as a judgment call.

    Cost, stated plainly
    --------------------
    This function now performs a database round trip on every request that depends on
    it, in its own short transaction opened *before* ``get_db``'s. A route that uses
    both therefore checks out a connection twice in sequence. That is acceptable at
    P1's scale and it is the honest consequence of §7.5 ordering ``get_scope`` ahead of
    ``get_db``: the scope must be known before the session that would filter by it
    exists, so there is no session to reuse. It is also *only* paid where asked for —
    nothing in ``app/modules/tenancy/router.py`` depends on ``get_scope``, so P1-T8's
    own CRUD routes are unaffected. The eventual fix is a request-scoped cache (the
    membership row is already read by ``get_org_context``'s successor in P2, at which
    point the two reads become one); recommended in the report rather than
    approximated with a module-level cache that would go stale on a role change.
    """
    # Deferred to break the import cycle described above.
    from app.modules.tenancy.service import resolve_branch_id
    from app.modules.programs.service import resolve_teacher_subject_ids

    branch_id = await resolve_branch_id(
        org_id=org_context.org_id, user_id=org_context.user_id
    )
    subject_ids = await resolve_teacher_subject_ids(
        org_id=org_context.org_id, user_id=org_context.user_id, role=org_context.role
    )

    logger.info(
        "scope_resolved user_id=%s org_id=%s role=%s branch_id=%s org_wide=%s subject_ids_count=%s",
        org_context.user_id,
        org_context.org_id,
        org_context.role,
        branch_id,
        branch_id is None,
        len(subject_ids) if subject_ids is not None else "all",
    )

    return RequestScope(
        org_id=org_context.org_id,
        user_id=org_context.user_id,
        role=org_context.role,
        branch_id=branch_id,
        subject_ids=subject_ids,
    )


# ---------------------------------------------------------------------------
# 5. get_db
# ---------------------------------------------------------------------------
async def get_db(
    org_context: OrgContext = Depends(get_org_context),
) -> AsyncIterator[AsyncSession]:
    """Doc 07 §7.5: "Yields an ``AsyncSession`` in a transaction with
    ``SET LOCAL app.org_id`` already applied".

    A thin adapter over P1-T5's ``get_tenant_session``, which owns the transaction,
    the ``set_config('app.org_id', ..., true)`` call, and the commit/rollback. Nothing
    in ``app/db/`` is modified or re-implemented here; this dependency exists only to
    turn "an async context manager taking an org id" into "a FastAPI dependency that
    gets the org id from the guard chain".

    Because it depends on :func:`get_org_context`, a request that fails the org guard
    never reaches this function — so no transaction is opened and no connection is
    checked out for a request that was going to be rejected anyway. That is doc 07
    §6's "``get_db`` opens the transaction last, after the org context is known",
    enforced by the dependency graph rather than by convention.
    """
    async with get_tenant_session(org_context.org_id) as session:
        yield session


# ---------------------------------------------------------------------------
# Opt-in identity + tenant session (P2-T4 privileged global-question writes)
# ---------------------------------------------------------------------------
async def get_global_write_db(
    org_context: OrgContext = Depends(get_org_context),
) -> AsyncIterator[AsyncSession]:
    """Yield an actor-aware tenant session for explicitly opted-in handlers.

    This dependency is deliberately outside doc 07's default guard/database chain.
    Unlike :func:`get_db`, it sets both transaction-local ``app.user_id`` and
    ``app.org_id`` so a narrowly scoped RLS policy can re-derive authorization from
    live membership state. The actor UUID comes only from the verified
    :class:`OrgContext`; no request-controlled body, header, path, or query value can
    select it.

    P2-T4 opts in only the question-creation handler. A first-version global question
    performs an INSERT followed by the lineage self-rooting UPDATE in this same
    transaction, and migration 0031 requires the verified actor context for both.
    Ordinary handlers must continue to use :func:`get_db`, preserving P1-T8's
    identity-context opt-in boundary.
    """
    async with get_identity_session(
        org_context.user_id,
        org_id=org_context.org_id,
    ) as session:
        yield session


__all__ = [
    "DEBUG_ORG_ID_HEADER",
    "DEBUG_ROLES_HEADER",
    "DEBUG_USER_ID_HEADER",
    "STUB_AUTH_ENVIRONMENTS",
    "AuthenticatedUser",
    "OrgContext",
    "RequestScope",
    "get_current_user",
    "get_db",
    "get_global_write_db",
    "get_org_context",
    "get_scope",
    "require_roles",
]
