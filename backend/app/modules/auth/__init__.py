"""Auth module — doc 07 §4.1 and §7 (P1-T7).

Layout follows doc 07 §3's per-module structure:

* :mod:`app.modules.auth.security` — Argon2id hashing and access-token cryptography.
  The only module that sees a plaintext password or a signing secret.
* :mod:`app.modules.auth.tokens` — the Redis-backed refresh session store: single-use
  rotation, reuse detection, family revocation (§7.1, §7.3).
* :mod:`app.modules.auth.repository` — ``users`` and the login-time membership check,
  through P1-T5's ``BaseRepository``.
* :mod:`app.modules.auth.service` — login / refresh / logout orchestration (§11).
* :mod:`app.modules.auth.router` — the three HTTP endpoints (§4.1).
* :mod:`app.modules.auth.schemas` — request and response DTOs (§9, §11.4).

Nothing is re-exported here. ``app/common/deps.py`` imports ``security`` and ``tokens``
directly, and a convenience re-export at package level would create an import cycle the
moment the tenancy module (which per doc 07 §4.2 depends on this one) imports the package
rather than a submodule.

Built in this task
------------------
Argon2id password hashing and verification (§7.2); JWT HS256 access tokens with §7.1's
payload (§7.1); opaque-UUID refresh tokens in Redis with single-use rotation, reuse
detection and session-family revocation (§7.1, §7.3); real signature verification in
``get_current_user`` (§7.5); ``POST /auth/login``, ``/auth/refresh``, ``/auth/logout``
(§4.1).

Deferred, and named rather than quietly skipped
-----------------------------------------------
``IMPLEMENTATION_PLAN.md`` line 142 assigns two more items to P1-T7 that this task's brief
scopes out, and doc 07 §4.1 lists their endpoints. Both are absent, not partially built:

* **Password reset** — ``POST /auth/password-reset`` and
  ``/auth/password-reset/confirm``, with a single-use expiring token. The Redis store in
  :mod:`app.modules.auth.tokens` is the right home for that token when it lands.
* **Login rate limiting and account lockout** — ``LOGIN_RATE_LIMIT`` already exists in
  ``app/core/config.py`` (default 50) and is currently unused. Without it, the Argon2id
  cost that protects the hashes is also a denial-of-service amplifier: each attempt costs
  the server ~250ms of a 64 MiB memory-hard computation, so an attacker spends far less
  than the defender. This is the most security-relevant gap in the module.

``JWT_REFRESH_SECRET`` in ``app/core/config.py`` is also unused — doc 07 §7.1 specifies an
opaque UUID rather than a signed refresh token, so nothing signs anything with it. See
:mod:`app.modules.auth.tokens` for the full account of that contradiction.
"""
