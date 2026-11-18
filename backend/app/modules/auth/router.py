"""Auth endpoints — doc 07 §4.1's ``POST /auth/login``, ``/auth/refresh``, ``/auth/logout``.

Thin by design (doc 07 §11: the router does HTTP, the service does work). Everything here
is either request plumbing, cookie policy, or the envelope — no credential logic.

Deny-by-default, and why this router is the documented exception
---------------------------------------------------------------
Doc 07 §7.5: "Nothing is public unless it is explicitly declared so." This router carries
**no** ``dependencies=[...]`` because login cannot require the credential it issues. That
is the explicit declaration, stated here rather than left as an absence someone has to
notice. The two things that make the exception safe to grant:

* The router's prefix is exactly ``/api/v1/auth`` and it hosts exactly three routes. No
  data-bearing endpoint lives on it, so a mistake here cannot expose tenant data.
* ``/refresh`` and ``/logout`` are unguarded but not unauthenticated — they authenticate
  with the refresh cookie, which the store validates. What they do not require is an
  *access* token, which is the point: refresh exists precisely for when the access token
  has expired.

``route_class=EnvelopeRoute`` is P1-T6's, so these responses come back in the same
``{ success, data, meta }`` shape as everything else and the error paths go through
P1-T6's registered handlers. No parallel response format, per the brief.

REPORTED GAPS — cookie attributes doc 08 leaves unspecified.
Doc 08 line 913 shows ``Set-Cookie: refresh=...; HttpOnly; Secure; SameSite`` — it names
the cookie and three attributes but gives ``SameSite`` no value and says nothing about
``Path`` or ``Max-Age``. Resolutions, all reported rather than silent:

* **Name** — ``refresh``, taken verbatim from doc 08 rather than prefixed. The frontend is
  already written against the doc and this task must not touch ``frontend/``, so
  inventing a nicer name would break the consumer to satisfy a preference.
* **SameSite=lax** — ``none`` would disable CSRF protection on the refresh endpoint
  entirely and mandates ``Secure`` (breaking local http development); ``strict`` would
  drop the cookie on a top-level navigation back into the app, silently logging users out
  when they follow an emailed link. ``lax`` keeps cross-site POST protection while
  surviving both. Note that ``localhost:3000`` → ``localhost:8077`` and
  ``app.example.com`` → ``api.example.com`` are both *same-site* (SameSite ignores port
  and compares registrable domains), so ``lax`` does not break either topology.
* **Path=/api/v1/auth** — the cookie is only useful to these three endpoints, so scoping
  it there keeps it off every other request. A refresh credential attached to every API
  call is a credential in every proxy log and every browser devtools panel for no benefit.
* **Max-Age** — set from ``JWT_REFRESH_EXPIRY_DAYS`` so the browser discards the cookie on
  the same schedule Redis expires the session. Omitting it would make a session cookie
  that vanishes on browser close, which contradicts a 7-day refresh window.
* **Secure** — on everywhere except ``STUB_AUTH_ENVIRONMENTS``. A ``Secure`` cookie is
  simply not stored over plain http, so hardcoding it on would make local development fail
  in a way that looks like a server bug.
"""

from __future__ import annotations

import logging
from typing import Optional

from fastapi import APIRouter, Cookie, Depends, Response, status

from app.common.deps import STUB_AUTH_ENVIRONMENTS
from app.common.routing import EnvelopeRoute
from app.core.config import Settings, get_settings
from app.modules.auth import service
from app.modules.auth.schemas import (
    ActiveOrgOut,
    LoginIn,
    LoginOut,
    LogoutOut,
    RefreshOut,
    UserOut,
)

logger = logging.getLogger("papergen.api.auth")

#: Doc 08 line 913's cookie name, verbatim.
REFRESH_COOKIE_NAME = "refresh"
#: See the module docstring on why the cookie is scoped this narrowly.
REFRESH_COOKIE_PATH = "/api/v1/auth"

router = APIRouter(
    prefix="/api/v1/auth",
    tags=["Auth"],
    route_class=EnvelopeRoute,
    # No `dependencies=[...]`: see the module docstring. This is the explicit public
    # declaration doc 07 §7.5 requires, not an omission.
)


def _set_refresh_cookie(response: Response, token: str, settings: Settings) -> None:
    """Attach the rotated refresh cookie. The token is never logged."""
    response.set_cookie(
        key=REFRESH_COOKIE_NAME,
        value=token,
        max_age=settings.JWT_REFRESH_EXPIRY_DAYS * 24 * 60 * 60,
        path=REFRESH_COOKIE_PATH,
        httponly=True,
        secure=settings.ENVIRONMENT not in STUB_AUTH_ENVIRONMENTS,
        samesite="lax",
    )


def _clear_refresh_cookie(response: Response, settings: Settings) -> None:
    """Delete the refresh cookie.

    ``path`` must match the one used when setting it — a ``delete_cookie`` on a different
    path is a no-op that looks like it worked, which would leave a revoked-but-present
    cookie in the browser after logout.
    """
    response.delete_cookie(
        key=REFRESH_COOKIE_NAME,
        path=REFRESH_COOKIE_PATH,
        httponly=True,
        secure=settings.ENVIRONMENT not in STUB_AUTH_ENVIRONMENTS,
        samesite="lax",
    )


def _active_org(creds: service.IssuedCredentials) -> Optional[ActiveOrgOut]:
    if creds.org_id is None or creds.role is None:
        return None
    return ActiveOrgOut(org_id=creds.org_id, role=creds.role)


@router.post("/login", status_code=status.HTTP_200_OK)
async def login(
    payload: LoginIn,
    response: Response,
    settings: Settings = Depends(get_settings),
) -> LoginOut:
    """Doc 07 §4.1 ``POST /auth/login``; response shape from doc 08 line 913.

    200 rather than 201. Doc 08 shows ``200 { data: { accessToken, user, orgs } }``, and a
    login creates no addressable resource, so there is no ``Location`` for a 201 to point
    at. The Redis session is an implementation detail of the credential, not a REST
    resource.
    """
    creds = await service.login(
        email=payload.email,
        password=payload.password,
        org_id=payload.org_id,
        settings=settings,
    )
    _set_refresh_cookie(response, creds.refresh_token, settings)
    return LoginOut(
        access_token=creds.access_token,
        expires_in=creds.expires_in,
        user=UserOut(
            id=creds.user_id,
            email=creds.email,
            full_name=creds.full_name,
            status=creds.status,
        ),
        active_org=_active_org(creds),
    )


@router.post("/refresh", status_code=status.HTTP_200_OK)
async def refresh(
    response: Response,
    settings: Settings = Depends(get_settings),
    refresh_token: Optional[str] = Cookie(default=None, alias=REFRESH_COOKIE_NAME),
) -> RefreshOut:
    """Doc 07 §7.3's rotation; doc 08 lines 964-966 (empty body, cookie travels itself).

    A missing cookie is a 401, not a 422. Pydantic would call an absent required cookie a
    validation error, but "you presented no credential" is an authentication failure — the
    same one ``get_current_user`` raises for a missing bearer token — and routing it
    through the validation handler would also produce a *different* envelope shape for the
    same logical condition.
    """
    if not refresh_token:
        # Imported locally: the module-level import surface stays limited to what the
        # happy path needs, and this keeps the 401 next to the reason for it.
        from app.common.errors import AuthenticationError

        logger.info("refresh_rejected reason=no_cookie")
        raise AuthenticationError(
            "Your session has expired. Please sign in again.",
            eh_ref="EH-15",
            log_detail=f"no {REFRESH_COOKIE_NAME} cookie presented",
        )

    creds = await service.refresh(refresh_token=refresh_token, settings=settings)
    _set_refresh_cookie(response, creds.refresh_token, settings)
    return RefreshOut(
        access_token=creds.access_token,
        expires_in=creds.expires_in,
        active_org=_active_org(creds),
    )


@router.post("/logout", status_code=status.HTTP_200_OK)
async def logout(
    response: Response,
    settings: Settings = Depends(get_settings),
    refresh_token: Optional[str] = Cookie(default=None, alias=REFRESH_COOKIE_NAME),
) -> LogoutOut:
    """Doc 08 line 982: "revokes the Redis refresh session and clears the cookie".

    Always 200, and the cookie is always cleared — even when no live session was found.
    The caller's intent is "end my session", and that is satisfied either way; a 401 here
    would leave the browser holding a cookie the server has already forgotten.
    """
    revoked = await service.logout(refresh_token=refresh_token, settings=settings)
    _clear_refresh_cookie(response, settings)
    return LogoutOut(revoked=revoked)


__all__ = ["router", "REFRESH_COOKIE_NAME", "REFRESH_COOKIE_PATH"]
