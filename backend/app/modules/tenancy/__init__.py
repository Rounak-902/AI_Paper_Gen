"""Tenancy module (P1-T8) — doc 07 §4.2.

    | Module  | Owns tables                                        | Routes |
    | Tenancy | organizations, branches, users, memberships        | POST /orgs, GET /orgs,
    |         |                                                    | PATCH /orgs/:id,
    |         |                                                    | POST /orgs/:id/branches,
    |         |                                                    | GET /orgs/:id/branches,
    |         |                                                    | POST /orgs/:id/invitations,
    |         |                                                    | PATCH /memberships/:id,
    |         |                                                    | GET /auth/me |
    | Depends on | Auth module (password hashing for new users)              |

``invitations`` is added to that "owns tables" list by this task; see
``app/models/invitation.py`` for why the table exists at all despite ADR-012.

``GET /auth/me`` is listed under this module in §4.2 and **is** implemented here, on
``app/modules/tenancy/router.py``'s ``principal_router`` (prefix ``/api/v1/auth``,
OpenAPI tag ``Auth``), alongside ``POST /auth/switch-org``.

CORRECTED — an earlier draft of this docstring said both routes would live in
``app/modules/auth/router.py`` "so that every route under the ``/api/v1/auth`` prefix
lives in one file". That plan is wrong and was abandoned once the import direction was
checked. §4.2's own "Depends on: Auth module" arrow is one-way, and both routes need
tenancy logic — ``describe_principal`` reads ``memberships``, and
``switch_active_org`` must verify one before re-minting a token. Hosting them in
``auth`` would therefore make ``auth`` import ``tenancy`` and create a circular
dependency between the two modules. The URL prefix and OpenAPI tag still group them
with login/refresh/logout for the client's benefit; only the file placement differs,
and it differs in the direction §4.2 actually specifies. Recorded in the P1-T8 report.
"""
