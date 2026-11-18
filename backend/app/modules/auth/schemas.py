"""Request/response DTOs for the auth endpoints — doc 07 §9 and doc 08's consumer contract.

Two model bases, on purpose. Requests use ``extra="forbid"`` (doc 07 §20.1's
mass-assignment defence — ``{"email": ..., "password": ..., "role": "super_admin"}`` must
be a 422, not a silently ignored key). Responses use an explicit field allowlist (doc 07
§11.4), so a column added to ``users`` later cannot appear in a login response by
accident.

The response shapes are taken from doc 08 rather than invented, because the frontend is
already written against them:

* line 913 — login returns ``200 { data: { accessToken, user, orgs } }`` plus a
  ``Set-Cookie: refresh=...; HttpOnly; Secure; SameSite``
* lines 964-966 — ``POST /auth/refresh`` takes an empty body (the cookie travels
  automatically) and returns ``200 { accessToken' }`` with a rotated cookie

REPORTED GAP — the ``orgs`` list in doc 08's login response is **not** returned here.
Returning it would require reading every membership of the user, and ``memberships`` is
RLS-protected on ``org_id = current_setting('app.org_id')`` with no self-read policy and
no ``app.user_id`` GUC in the schema — so as ``app_user`` that read returns zero rows
before an org context exists. Doc 07 §4.2 assigns ``GET /orgs`` ("list user's
organizations") to the tenancy module, which is P1-T8, so the list is deferred there
rather than obtained by weakening RLS or by connecting as a bypassing role. ``activeOrg``
is returned instead: the one org the caller actually asked for and was verified against.
"""

from __future__ import annotations

import uuid
from typing import Optional

from pydantic import BaseModel, ConfigDict, Field
from pydantic.alias_generators import to_camel

#: Loose email shape check. See :class:`LoginIn` for why this is not ``EmailStr``.
_EMAIL_PATTERN = r"^[^@\s]+@[^@\s]+\.[^@\s]+$"


class AuthRequest(BaseModel):
    """Request base: camelCase in, unknown fields rejected."""

    model_config = ConfigDict(
        alias_generator=to_camel,
        populate_by_name=True,
        extra="forbid",
    )


class AuthResponse(BaseModel):
    """Response base: camelCase out, explicit fields only."""

    model_config = ConfigDict(alias_generator=to_camel, populate_by_name=True)


class LoginIn(AuthRequest):
    """``POST /api/v1/auth/login`` body.

    ``password`` has a ``max_length`` and it is a denial-of-service control, not
    tidiness. Argon2id at m=64MiB is deliberately expensive, and the cost scales with
    input length, so an unbounded password field is a request that can be made to consume
    an arbitrary amount of server memory and CPU. 1024 is far above any real passphrase.
    ``min_length=1`` rather than a policy minimum: enforcing password *strength* at login
    would reject users whose existing password predates the policy, so strength belongs on
    the set-password path (P1-T7's deferred password-reset work), not here.

    ``org_id`` is optional. See :mod:`app.modules.auth.service` for the full reasoning;
    in short, a user with one org can log straight in with a usable token, and a
    multi-org user gets an org-less token then calls P1-T8's org-switch endpoint.

    REPORTED JUDGMENT — ``email`` is a pattern-checked ``str``, not pydantic's
    ``EmailStr``. ``EmailStr`` requires the ``email-validator`` package, which is neither
    installed in ``backend/.venv`` nor declared in ``pyproject.toml``; using it would
    raise at import time and turn a login schema into a boot failure. Adding a runtime
    dependency was rejected as out of scope for a task whose brief is authentication
    logic. The looser check costs nothing here: an address that is well-formed but
    non-existent and one that is malformed both fail to match a row, and doc 07 §20.4
    requires them to produce the same generic response regardless. ``users.email`` is
    ``CITEXT`` with no format constraint in the database either, so this does not weaken
    an invariant that existed elsewhere. If RFC-grade validation is wanted later, the
    right place is the registration/invitation path P1-T8 owns, with the dependency
    declared properly.
    """

    email: str = Field(min_length=3, max_length=320, pattern=_EMAIL_PATTERN)
    password: str = Field(min_length=1, max_length=1024)
    org_id: Optional[uuid.UUID] = None


class UserOut(AuthResponse):
    """The authenticated user, as an allowlist.

    ``password_hash`` is absent, and its absence is asserted by the verification harness
    rather than trusted — an allowlist is only a control if something checks it.
    """

    id: uuid.UUID
    email: str
    full_name: str
    status: str


class ActiveOrgOut(AuthResponse):
    """The org the token is scoped to, when the caller named one at login."""

    org_id: uuid.UUID
    role: str


class LoginOut(AuthResponse):
    """``POST /api/v1/auth/login`` response body (inside P1-T6's ``data`` envelope).

    ``expires_in`` is seconds, mirroring OAuth 2.0's ``expires_in``, so the client can
    schedule a refresh without decoding the token. It is derived from settings rather
    than hardcoded, so retuning ``JWT_ACCESS_EXPIRY_MINUTES`` cannot desynchronise the
    advertised lifetime from the real one.

    The refresh token is **not** in this body. It leaves only as an ``HttpOnly`` cookie,
    per doc 08 line 913, which is what stops a successful XSS from reading it out of a
    JavaScript variable.
    """

    access_token: str
    token_type: str = "Bearer"
    expires_in: int
    user: UserOut
    active_org: Optional[ActiveOrgOut] = None


class RefreshOut(AuthResponse):
    """``POST /api/v1/auth/refresh`` response — doc 08 lines 964-966's ``{ accessToken' }``."""

    access_token: str
    token_type: str = "Bearer"
    expires_in: int
    active_org: Optional[ActiveOrgOut] = None


class LogoutOut(AuthResponse):
    """``POST /api/v1/auth/logout`` response.

    ``revoked`` reports whether a live session was actually found and revoked. It is
    informational only: the endpoint returns 200 either way, because a logout that errors
    on an already-dead session both fails the caller's intent and tells an attacker which
    tokens were real.
    """

    revoked: bool


class SwitchOrgIn(AuthRequest):
    """``POST /api/v1/auth/switch-org`` body — doc 08 lines 934-938.

    Doc 08 gives this endpoint exactly one field, ``orgId``, and that is all it takes.
    In particular it takes **no ``role``**: the role for the new token is re-derived
    server-side from the caller's membership in the target org. Accepting a role here
    would be a self-declared privilege, which is the whole class of bug this endpoint
    is most exposed to.
    """

    org_id: uuid.UUID


class SwitchOrgOut(AuthResponse):
    """``POST /api/v1/auth/switch-org`` response.

    Doc 08 line 938 shows only the re-minted access token. ``activeOrg`` is included
    as well, for a reason that is a correctness matter rather than convenience: the
    role in the new token is re-derived from the target org's membership and may
    differ from the role the caller had a moment ago. A client that assumed its old
    role survived the switch would render an admin UI for a ``teacher``. Returning
    the authoritative pair means doc 08 §9.7's "hard reset" has something to reset
    *to* without decoding the JWT client-side.

    Same shape as :class:`RefreshOut` minus the cookie, deliberately, so doc 08 §10's
    response handling can treat all three token-issuing endpoints alike.
    """

    access_token: str
    token_type: str = "Bearer"
    expires_in: int
    active_org: ActiveOrgOut


__all__ = [
    "LoginIn",
    "UserOut",
    "ActiveOrgOut",
    "LoginOut",
    "RefreshOut",
    "LogoutOut",
    "SwitchOrgIn",
    "SwitchOrgOut",
]
