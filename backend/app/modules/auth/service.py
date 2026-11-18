"""Auth service — login, refresh, logout. Doc 07 §7.1-§7.3 and §11's service layer.

The router below this is thin on purpose (doc 07 §11: routers do HTTP, services do
work), and every security-relevant decision in the credential flow lives here so there
is exactly one place to audit.

The one client-visible message
------------------------------
Every failed login raises ``AuthenticationError("Invalid email or password.")`` — the
same string for a missing user, a wrong password, a disabled account and a suspended
one. Doc 07 §20.4: "Generic error messages that do not reveal whether an email exists."
The real reason goes to ``log_detail``, which ``errors.py`` never serialises. Four
distinct server-side reasons, one client-visible answer.

The timing side of the same rule
--------------------------------
A generic *message* is not enough. Argon2id at m=64MiB takes ~250ms by design, so a
missing email would answer in under a millisecond while a wrong password takes a quarter
of a second — an account-existence oracle that is trivially measurable over a network
and completely invisible in a response body. The user-not-found branch therefore calls
:func:`app.modules.auth.security.dummy_verify_async`, which performs a real Argon2id
verification against a throwaway hash so both branches cost the same.

Every Argon2id call here uses the ``_async`` wrapper, never the bare function. See the
"Async wrappers" block in :mod:`app.modules.auth.security` — a 250ms memory-hard C
computation awaited directly on the event loop stalls the whole process, not just the one
request.

REPORTED DECISION — ``orgId`` is optional at login, and the response carries no org list.
Doc 07 §7.1 puts ``activeOrgId`` in the access-token payload and doc 08 line 913 has
login return an ``orgs`` array, but migration 0025 protects ``memberships`` with
``org_id = current_setting('app.org_id')::uuid`` and adds no self-read policy, and no
``app.user_id`` GUC exists anywhere in the schema or docs. As ``app_user`` (NOBYPASSRLS),
"which orgs does this user belong to" is therefore an unanswerable query at login time —
before any org context exists. Three options were possible: add an ``app.user_id`` GUC
and a self-read policy (a migration and an RLS change, in a task that owns neither),
connect as a bypassing role for this one read (defeats P1-T5 entirely), or have the
client name the org it wants and verify *that*. The third was chosen. Consequences:

* A caller that names an ``orgId`` gets a fully-scoped token in one round trip.
* A caller that names none gets a token that authenticates but does not authorize —
  ``get_org_context`` returns 403 on ``requested_org_id is None``, exactly as P1-T6
  already proved. Doc 08 lines 934-938's ``/select-org`` then re-mints, which is P1-T8's
  endpoint calling :func:`app.modules.auth.security.create_access_token`.
* No RLS policy, no migration and no bypassing connection were introduced.

REPORTED ADDITION — refresh re-checks account status.
Doc 07 §7.3 describes rotation purely in terms of the Redis lookup and says nothing about
re-validating the user. Taken literally, disabling an account would leave its holder able
to mint fresh 15-minute access tokens for the remaining 7 days of the refresh window.
This implementation re-reads ``users.status`` on every refresh and refuses a
non-``active`` account. The cost is one indexed primary-key read per refresh; the
alternative is an account suspension that does not take effect for a week.
"""

from __future__ import annotations

import logging
import uuid
from dataclasses import dataclass
from typing import Optional

from app.common.errors import AuthenticationError, AuthorizationError
from app.core.config import Settings
from app.db.session import get_tenant_session, get_unscoped_session
from app.modules.auth import security, tokens
from app.modules.auth.repository import MembershipRepository, UserRepository

logger = logging.getLogger("papergen.auth.service")

#: The single client-visible failure string for every credential rejection.
_GENERIC_LOGIN_FAILURE = "Invalid email or password."

#: The single client-visible failure string for every refresh rejection. Distinct from
#: the login one only because the *situation* is different (your session ended vs. your
#: credentials are wrong); it still says nothing about which of the several possible
#: reasons applied.
_GENERIC_REFRESH_FAILURE = "Your session has expired. Please sign in again."


@dataclass(frozen=True, slots=True)
class IssuedCredentials:
    """What a successful login or refresh produces.

    ``refresh_token`` is the plaintext, and this is the only object in the process that
    carries it. The router puts it straight into a ``Set-Cookie`` header and never logs
    it; nothing persists it (the store keeps only ``sha256`` of it).
    """

    access_token: str
    refresh_token: str
    expires_in: int
    user_id: uuid.UUID
    email: str
    full_name: str
    status: str
    org_id: Optional[uuid.UUID]
    role: Optional[str]
    session_id: str


async def _resolve_membership(
    user_id: uuid.UUID, org_id: uuid.UUID
) -> str:
    """Verify active membership in ``org_id`` and return the role, or raise 403.

    The read happens inside ``get_tenant_session(org_id)``, so ``app.org_id`` is set to
    the org being checked and the RLS policy on ``memberships`` is satisfied for exactly
    that org and no other. A caller cannot use this to enumerate a different tenant's
    membership rows: the GUC is set from the requested org, and the policy compares
    against the same value, so a row for any other org is invisible regardless of what
    the query asks for.

    403 rather than 401. The credential was valid — this is "wrong org", which doc 07
    §10.2 lists under "Authorization errors | 403" and which ``get_org_context`` already
    answers the same way for the same condition. Returning 401 here would tell the caller
    their password was wrong when it was not.
    """
    async with get_tenant_session(org_id) as session:
        membership = await MembershipRepository(session, org_id).get_active_for_user(user_id)

    if membership is None:
        logger.warning(
            "login_org_membership_denied user_id=%s org_id=%s", user_id, org_id
        )
        raise AuthorizationError(
            "You do not have access to this resource.",
            log_detail=f"no active membership for user={user_id} in org={org_id}",
        )
    return membership.role


async def login(
    *,
    email: str,
    password: str,
    org_id: Optional[uuid.UUID],
    settings: Settings,
) -> IssuedCredentials:
    """Authenticate a password and issue an access/refresh pair.

    Ordering is deliberate: the password is verified *before* the org membership is
    checked. Doing it the other way round would let an unauthenticated caller probe
    whether a given email belongs to a given org by comparing a 403 against a 401.
    """
    # ``users`` is RLS-exempt global identity (migration 0025), and there is no org
    # context to set yet — an unscoped session is the correct and only option here.
    async with get_unscoped_session() as session:
        repo = UserRepository(session)
        user = await repo.get_by_email(email)

        if user is None:
            # Equalise the timing before failing. See the module docstring.
            await security.dummy_verify_async(settings)
            logger.info("login_failed reason=user_not_found")
            raise AuthenticationError(
                _GENERIC_LOGIN_FAILURE, log_detail=f"no user row for email={email!r}"
            )

        if not await security.verify_password_async(
            password, user.password_hash, settings
        ):
            logger.info("login_failed reason=bad_password user_id=%s", user.id)
            raise AuthenticationError(
                _GENERIC_LOGIN_FAILURE,
                log_detail=f"password mismatch for user={user.id}",
            )

        if user.status != "active":
            logger.warning(
                "login_failed reason=inactive_account user_id=%s status=%s",
                user.id,
                user.status,
            )
            raise AuthenticationError(
                _GENERIC_LOGIN_FAILURE,
                log_detail=f"user={user.id} status={user.status!r} is not active",
            )

        # Transparent parameter upgrade. This is the only moment the plaintext exists
        # alongside a known-correct identity, so it is the only moment a rehash is
        # possible. Routed through the repository's optimistic-locking ``update`` rather
        # than a raw UPDATE, per the brief's "no parallel data-access path".
        if security.password_needs_rehash(user.password_hash or "", settings):
            new_hash = await security.hash_password_async(password, settings)
            await repo.update(
                user.id, expected_version=user.row_version, password_hash=new_hash
            )
            logger.info("password_hash_upgraded user_id=%s", user.id)

        # Snapshot every attribute needed after this block. The ``session.begin()``
        # context commits on exit, which expires ORM instances, so touching
        # ``user.email`` afterwards would emit a lazy refresh against a closed session.
        user_id = user.id
        user_email = user.email
        user_full_name = user.full_name
        user_status = user.status

    role: Optional[str] = None
    if org_id is not None:
        role = await _resolve_membership(user_id, org_id)

    refresh_token, session_meta = await tokens.issue_session(
        user_id=user_id, org_id=org_id, role=role, settings=settings
    )
    access_token = security.create_access_token(
        user_id=user_id,
        active_org_id=org_id,
        role=role,
        session_id=session_meta.session_id,
        settings=settings,
    )

    logger.info(
        "login_succeeded user_id=%s org_id=%s role=%s sid=%s",
        user_id,
        org_id,
        role,
        session_meta.session_id,
    )
    return IssuedCredentials(
        access_token=access_token,
        refresh_token=refresh_token,
        expires_in=settings.JWT_ACCESS_EXPIRY_MINUTES * 60,
        user_id=user_id,
        email=user_email,
        full_name=user_full_name,
        status=user_status,
        org_id=org_id,
        role=role,
        session_id=session_meta.session_id,
    )


async def refresh(*, refresh_token: str, settings: Settings) -> IssuedCredentials:
    """Doc 07 §7.3's rotation, plus the account-status re-check described above.

    Both :class:`tokens.RefreshTokenInvalid` and its
    :class:`tokens.RefreshTokenReused` subclass become the same 401 with the same
    message. The difference is entirely server-side: the reuse path has already revoked
    the whole session family inside :func:`tokens.rotate_session` and logged
    ``refresh_token_reuse_detected``. Telling the client which one happened would
    confirm to an attacker holding a stolen token that the token had been genuine.
    """
    try:
        new_refresh, session_meta = await tokens.rotate_session(refresh_token, settings)
    except tokens.RefreshTokenReused as exc:
        raise AuthenticationError(
            _GENERIC_REFRESH_FAILURE,
            eh_ref="EH-15",
            log_detail=f"refresh reuse detected: {exc}",
        ) from exc
    except tokens.RefreshTokenInvalid as exc:
        raise AuthenticationError(
            _GENERIC_REFRESH_FAILURE,
            eh_ref="EH-15",
            log_detail=f"refresh rejected: {exc}",
        ) from exc

    async with get_unscoped_session() as session:
        user = await UserRepository(session).get_by_id(session_meta.user_id)
        if user is None or user.status != "active":
            # The rotation already happened, so the new token exists — revoke the family
            # rather than leaving a live credential for an account that may no longer be
            # allowed to hold one.
            await tokens.revoke_family(session_meta.session_id, settings)
            logger.warning(
                "refresh_denied reason=account_not_active user_id=%s sid=%s",
                session_meta.user_id,
                session_meta.session_id,
            )
            raise AuthenticationError(
                _GENERIC_REFRESH_FAILURE,
                eh_ref="EH-15",
                log_detail=(
                    f"user={session_meta.user_id} missing or not active at refresh; "
                    f"family {session_meta.session_id} revoked"
                ),
            )
        user_email = user.email
        user_full_name = user.full_name
        user_status = user.status

    access_token = security.create_access_token(
        user_id=session_meta.user_id,
        active_org_id=session_meta.org_id,
        role=session_meta.role,
        session_id=session_meta.session_id,
        settings=settings,
    )
    return IssuedCredentials(
        access_token=access_token,
        refresh_token=new_refresh,
        expires_in=settings.JWT_ACCESS_EXPIRY_MINUTES * 60,
        user_id=session_meta.user_id,
        email=user_email,
        full_name=user_full_name,
        status=user_status,
        org_id=session_meta.org_id,
        role=session_meta.role,
        session_id=session_meta.session_id,
    )


async def logout(*, refresh_token: Optional[str], settings: Settings) -> bool:
    """Revoke the caller's session family. Returns whether one was found.

    Never raises for an unknown or absent token — see
    :func:`app.modules.auth.tokens.revoke_by_token` for why an erroring logout is both
    unhelpful and a disclosure.
    """
    if not refresh_token:
        logger.info("logout_no_cookie_presented")
        return False
    sid = await tokens.revoke_by_token(refresh_token, settings)
    if sid:
        logger.info("logout_succeeded sid=%s", sid)
    return sid is not None


__all__ = ["IssuedCredentials", "login", "refresh", "logout"]
