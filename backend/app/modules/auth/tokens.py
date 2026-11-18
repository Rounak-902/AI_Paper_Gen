"""Refresh-token session store — doc 07 §7.1 and §7.3 (P1-T7).

Doc 07 §7.1, verbatim
---------------------
    Refresh Token (opaque UUID, ~7 days TTL)
      Stored server-side in Redis with session metadata
      Single-use: each refresh issues new pair + invalidates old

Doc 07 §7.3, verbatim
---------------------
    Redis lookup -> not found / expired -> 401
    found -> new pair, invalidate old, store new
    If old token is reused after invalidation:
      -> Reuse detection -> revoke entire session family

REPORTED CONTRADICTION — the brief vs. the doc, and the config vs. the doc.
The task brief describes refresh tokens as JWTs ("JWT access tokens ... and refresh
tokens (longer-lived)"), and ``app/core/config.py`` carries a ``JWT_REFRESH_SECRET``
that only makes sense for a signed refresh token. Doc 07 §7.1 specifies an **opaque
UUID** held server-side. The doc wins, per this task's standing rule that the doc is
authoritative. Consequences, both reported rather than hidden:

* ``JWT_REFRESH_SECRET`` is **unused** by this implementation. It is left in config
  untouched — removing it would edit a validated settings surface and break the
  existing distinctness validator — but nothing signs anything with it. If a future
  task switches to signed refresh tokens, that is the secret to use.
* An opaque token is strictly stronger for this requirement set. Single-use invalidation
  and family revocation are *stateful* by nature; a self-contained JWT would still need
  the Redis lookup to know whether it had been used, so the signature would buy nothing
  and the token would leak its claims to anyone who read the cookie.

REPORTED ADDITION — the stored key is a hash, not the token.
Doc 07 says "Stored server-side in Redis" without saying in what form. Keys here are
``sha256(token)``, so a Redis dump, a ``KEYS *`` from an operator shell, or a leaked
RDB snapshot yields no usable bearer credentials — the same reason
``users.password_hash`` exists rather than a password column. SHA-256 without a KDF is
correct here and would be wrong for a password: the token is 122 bits of ``os.urandom``
entropy, so there is no dictionary to attack and nothing for a slow hash to defend.

Atomicity of single-use
-----------------------
Rotation claims a token with ``GETDEL`` — a single round trip that returns the value and
deletes the key. Two concurrent refreshes with the same token therefore cannot both
succeed: exactly one gets the payload, the other gets ``None`` and lands on the
reuse-detection path. A ``GET`` followed by a ``DEL`` would let both read the payload
before either deleted it, which is a race that hands out two valid token families from
one credential and is invisible in testing.
"""

from __future__ import annotations

import datetime as _dt
import hashlib
import json
import logging
import uuid
from dataclasses import dataclass
from typing import Final, Optional

from app.core.config import Settings
from app.infra.redis import get_redis

logger = logging.getLogger("papergen.auth.tokens")

_PREFIX: Final[str] = "papergen:auth"

#: Live refresh token -> session metadata JSON. Deleted the moment it is used.
_KEY_REFRESH: Final[str] = _PREFIX + ":refresh:{digest}"
#: Consumed-token marker. Its existence after a ``GETDEL`` miss is what distinguishes
#: "this token was already spent" (reuse -> revoke the family) from "this token never
#: existed" (a guess or a typo -> plain 401). Without it, both look identical and reuse
#: detection is impossible to implement.
_KEY_CONSUMED: Final[str] = _PREFIX + ":consumed:{digest}"
#: Set of every digest ever issued within one session family, so revocation can delete
#: all live descendants rather than only the newest.
_KEY_FAMILY: Final[str] = _PREFIX + ":family:{sid}"
#: Tombstone read by ``get_current_user``. Present => every access token bearing this
#: ``sid`` is refused, which is how doc 07 §7.5's "rejects ... revoked tokens" is
#: satisfied for a credential that is otherwise stateless and unexpired.
_KEY_REVOKED: Final[str] = _PREFIX + ":revoked:{sid}"


class RefreshTokenInvalid(Exception):
    """The presented refresh token is unknown, expired, or already spent."""


class RefreshTokenReused(RefreshTokenInvalid):
    """A token was presented after it had already been rotated.

    A subclass, not a sibling, so that a caller which only cares about "reject this
    request" cannot accidentally handle the ordinary-invalid case and let the reuse case
    escape as a 500. The distinction matters only for the response *reason* and the
    family revocation, never for the status code.
    """


@dataclass(frozen=True, slots=True)
class RefreshSession:
    """Session metadata stored alongside a refresh token — doc 07 §7.1's "session metadata"."""

    session_id: str
    user_id: uuid.UUID
    org_id: Optional[uuid.UUID]
    role: Optional[str]
    issued_at: _dt.datetime
    #: Monotonic counter across a family: 0 for the token minted at login, 1 after the
    #: first rotation, and so on. Purely diagnostic — it makes a reuse-detection log
    #: line say *how far back* the replayed token was.
    generation: int

    def to_json(self) -> str:
        return json.dumps(
            {
                "sid": self.session_id,
                "userId": str(self.user_id),
                "orgId": str(self.org_id) if self.org_id else None,
                "role": self.role,
                "issuedAt": self.issued_at.isoformat(),
                "generation": self.generation,
            }
        )

    @staticmethod
    def from_json(raw: str) -> "RefreshSession":
        data = json.loads(raw)
        return RefreshSession(
            session_id=data["sid"],
            user_id=uuid.UUID(data["userId"]),
            org_id=uuid.UUID(data["orgId"]) if data.get("orgId") else None,
            role=data.get("role"),
            issued_at=_dt.datetime.fromisoformat(data["issuedAt"]),
            generation=int(data.get("generation", 0)),
        )


def _digest(token: str) -> str:
    """Key-derivation for the Redis key. See the module docstring on why SHA-256."""
    return hashlib.sha256(token.encode("utf-8")).hexdigest()


def _new_token() -> str:
    """Doc 07 §7.1: "opaque UUID".

    ``uuid4`` draws its 122 random bits from ``os.urandom`` in CPython, so this is a
    CSPRNG-backed value despite the friendly-looking format. ``uuid1`` would embed the
    MAC address and a timestamp and be partially predictable — the reason the version is
    pinned here rather than left to a generic "a uuid".
    """
    return str(uuid.uuid4())


async def issue_session(
    *,
    user_id: uuid.UUID,
    org_id: Optional[uuid.UUID],
    role: Optional[str],
    settings: Settings,
    session_id: Optional[str] = None,
    generation: int = 0,
) -> tuple[str, RefreshSession]:
    """Mint and store a refresh token. Returns ``(plaintext_token, session)``.

    ``session_id`` is passed on rotation so the new token stays inside the same family;
    omitted at login so a fresh family is created. That is what makes "revoke entire
    session family" able to reach tokens minted long after the login it descends from.

    The plaintext is returned to the caller once and never stored. There is no code path
    in this module that can recover it.
    """
    token = _new_token()
    sid = session_id or str(uuid.uuid4())
    ttl = _dt.timedelta(days=settings.JWT_REFRESH_EXPIRY_DAYS)

    session = RefreshSession(
        session_id=sid,
        user_id=user_id,
        org_id=org_id,
        role=role,
        issued_at=_dt.datetime.now(_dt.timezone.utc),
        generation=generation,
    )

    digest = _digest(token)
    redis = get_redis()
    pipe = redis.pipeline(transaction=True)
    pipe.set(_KEY_REFRESH.format(digest=digest), session.to_json(), ex=ttl)
    pipe.sadd(_KEY_FAMILY.format(sid=sid), digest)
    # The family set outlives its members by a margin so that a replay arriving just
    # after the last member expired still finds a family to revoke. Without the margin,
    # revocation would silently no-op on an empty set.
    pipe.expire(_KEY_FAMILY.format(sid=sid), ttl + _dt.timedelta(days=1))
    await pipe.execute()

    logger.info(
        "refresh_session_issued sid=%s user_id=%s generation=%s ttl_days=%s",
        sid,
        user_id,
        generation,
        settings.JWT_REFRESH_EXPIRY_DAYS,
    )
    return token, session


async def rotate_session(token: str, settings: Settings) -> tuple[str, RefreshSession]:
    """Doc 07 §7.3's rotation. Returns the replacement ``(token, session)``.

    Raises :class:`RefreshTokenReused` when the token was already spent — after revoking
    the whole family — and :class:`RefreshTokenInvalid` when it was never valid.
    """
    digest = _digest(token)
    redis = get_redis()

    # Atomic claim: see the module docstring. ``getdel`` requires Redis >= 6.2; the
    # compose service pins 7.x.
    raw = await redis.getdel(_KEY_REFRESH.format(digest=digest))

    if raw is None:
        consumed = await redis.get(_KEY_CONSUMED.format(digest=digest))
        if consumed is not None:
            sid = consumed
            revoked_count = await revoke_family(sid, settings)
            logger.error(
                "refresh_token_reuse_detected sid=%s revoked_descendants=%s "
                "action=session_family_revoked",
                sid,
                revoked_count,
            )
            raise RefreshTokenReused(f"token already consumed; family {sid} revoked")
        # Never existed, or its TTL lapsed. Doc 07 §7.3: "not found / expired -> 401".
        logger.info("refresh_token_unknown_or_expired digest_prefix=%s", digest[:12])
        raise RefreshTokenInvalid("unknown or expired refresh token")

    session = RefreshSession.from_json(raw)

    if await redis.exists(_KEY_REVOKED.format(sid=session.session_id)):
        # The family was revoked while this token was still live — the losing side of a
        # reuse-detection race. Checked after the claim rather than before so the token
        # is spent either way and cannot be retried against a re-armed family.
        logger.warning(
            "refresh_token_in_revoked_family sid=%s user_id=%s",
            session.session_id,
            session.user_id,
        )
        raise RefreshTokenInvalid("session family is revoked")

    # Mark the spent digest so a later replay is *identifiable* as a replay rather than
    # merely unknown. The marker's TTL matches the refresh window: past that point the
    # original token would have expired anyway, so a replay is indistinguishable from a
    # guess and the plain-invalid path is the honest answer.
    await redis.set(
        _KEY_CONSUMED.format(digest=digest),
        session.session_id,
        ex=_dt.timedelta(days=settings.JWT_REFRESH_EXPIRY_DAYS),
    )

    new_token, new_session = await issue_session(
        user_id=session.user_id,
        org_id=session.org_id,
        role=session.role,
        settings=settings,
        session_id=session.session_id,
        generation=session.generation + 1,
    )
    logger.info(
        "refresh_session_rotated sid=%s user_id=%s generation=%s->%s",
        session.session_id,
        session.user_id,
        session.generation,
        new_session.generation,
    )
    return new_token, new_session


async def revoke_family(session_id: str, settings: Settings) -> int:
    """Revoke an entire session family. Returns how many live tokens were deleted.

    Two things happen, and both are needed. Every live refresh digest in the family is
    deleted, which stops future rotations. And a ``revoked:{sid}`` tombstone is written,
    which is what makes already-issued *access* tokens stop working — they are signed,
    unexpired and otherwise perfectly valid, so nothing else could refuse them.

    The tombstone's TTL is the access-token lifetime plus a minute of clock skew. Beyond
    that every access token bearing this ``sid`` has expired on its own, so keeping the
    tombstone would only grow Redis without changing any decision.
    """
    redis = get_redis()
    family_key = _KEY_FAMILY.format(sid=session_id)

    digests = await redis.smembers(family_key)
    deleted = 0
    if digests:
        keys = [_KEY_REFRESH.format(digest=d) for d in digests]
        deleted = int(await redis.delete(*keys))

    await redis.set(
        _KEY_REVOKED.format(sid=session_id),
        "1",
        ex=_dt.timedelta(minutes=settings.JWT_ACCESS_EXPIRY_MINUTES) + _dt.timedelta(minutes=1),
    )
    logger.info(
        "session_family_revoked sid=%s live_tokens_deleted=%s family_size=%s",
        session_id,
        deleted,
        len(digests) if digests else 0,
    )
    return deleted


async def retarget_family(
    *,
    session_id: str,
    org_id: uuid.UUID,
    role: str,
) -> int:
    """Point every live refresh token in a family at a new active org. Returns the count.

    No ``settings`` parameter, unlike every sibling in this module. That asymmetry is
    the point rather than an oversight: the siblings need ``settings`` to compute a
    TTL, and this function must **not** compute one — see the ``KEEPTTL`` argument
    below. Accepting an unused ``settings`` would invite a future edit to reach for
    ``JWT_REFRESH_EXPIRY_DAYS`` because it was conveniently in scope, which is
    exactly the bug (a silently extended refresh window on every org switch) that
    rewriting-in-place exists to avoid.

    P1-T8 addition, and doc-silent: doc 08 lines 934-938 describe ``POST
    /auth/switch-org`` as re-minting the **access** token and say nothing about the
    refresh session. Without this function the switch silently un-does itself, and
    the mechanism is worth spelling out because it is not obvious from the endpoint:
    :class:`RefreshSession` stores ``org_id`` and ``role``, and
    :func:`rotate_session` re-mints the next access token *from the stored values*.
    So a user who switches from org A to org B and then sits idle past the
    access-token TTL is put back into org A by the next automatic refresh — a
    15-minute-fuse bug that no test of the switch endpoint alone would catch.

    Why rewrite rather than revoke-and-reissue
    ------------------------------------------
    The alternative was ``revoke_family`` + ``issue_session``, which is simpler and
    was rejected for two reasons. It would restart the 7-day refresh window on every
    org switch, so a user could hold a session open indefinitely by switching orgs —
    extending authentication without re-authenticating, which is exactly what a
    bounded refresh window exists to prevent. And it would require the endpoint to
    return a fresh ``Set-Cookie``, which the client must then be relied upon to
    store. Rewriting in place keeps the original expiry and needs no cookie round
    trip: the token in the browser stays valid and simply means something new.

    ``SET ... XX KEEPTTL``, and why both flags are load-bearing
    ----------------------------------------------------------
    ``KEEPTTL`` preserves each token's remaining lifetime, which is the whole point —
    a plain ``SET`` would drop the TTL and make the refresh token immortal.

    ``XX`` closes a genuine resurrection hole. This function reads a digest's payload
    and writes it back, and :func:`rotate_session` may ``GETDEL`` that same digest in
    between. Without ``XX``, the write would **recreate the key that rotation just
    spent** — and because ``KEEPTTL`` on a non-existent key means "no expiry", the
    resurrected single-use token would be valid forever. With ``XX`` the write simply
    fails when the key is gone, which is the correct outcome: rotation already
    minted a successor, and the successor is handled by the residual-race note below.

    RESIDUAL RACE, stated rather than papered over. A rotation that completes after
    the family set is enumerated mints a *new* digest carrying the old ``org_id``, so
    a switch racing a refresh can fail to stick. The consequence is that the user
    stays in (or reverts to) the previous org and switches again — a liveness
    annoyance, not an authorization failure, because every path that grants access
    re-derives the role from a real membership in whichever org the token names.
    Closing it properly means moving the family read-modify-write into a Lua script
    or a ``WATCH``/``MULTI`` retry loop; recommended in the P1-T8 report rather than
    approximated here.
    """
    redis = get_redis()
    digests = await redis.smembers(_KEY_FAMILY.format(sid=session_id))
    if not digests:
        logger.info("refresh_family_retarget_empty sid=%s", session_id)
        return 0

    updated = 0
    for digest in digests:
        key = _KEY_REFRESH.format(digest=digest)
        raw = await redis.get(key)
        if raw is None:
            # Spent by a rotation, or expired. Nothing to retarget; a spent token is
            # never revived (see the ``XX`` argument above).
            continue
        current = RefreshSession.from_json(raw)
        rewritten = RefreshSession(
            session_id=current.session_id,
            user_id=current.user_id,
            org_id=org_id,
            role=role,
            issued_at=current.issued_at,
            generation=current.generation,
        )
        if await redis.set(key, rewritten.to_json(), xx=True, keepttl=True):
            updated += 1

    logger.info(
        "refresh_family_retargeted sid=%s org_id=%s role=%s tokens_updated=%s "
        "family_size=%s",
        session_id,
        org_id,
        role,
        updated,
        len(digests),
    )
    return updated


async def revoke_by_token(token: str, settings: Settings) -> Optional[str]:
    """Logout. Revokes the family the token belongs to; returns the ``sid`` if found.

    Doc 08 line 982: logout "revokes the Redis refresh session and clears the cookie".
    Family-wide rather than token-only, because revoking one token would leave any
    already-issued access token in that session valid for up to its full TTL — a logout
    that does not log you out.

    Returns ``None`` for an unknown token instead of raising. Doc 07 §4.1 lists
    ``POST /auth/logout`` with no failure mode, and a logout that 401s on an
    already-expired session is hostile: the caller's intent (be logged out) is already
    satisfied, and the only thing an error accomplishes is telling an attacker which
    tokens are real.
    """
    digest = _digest(token)
    redis = get_redis()
    raw = await redis.getdel(_KEY_REFRESH.format(digest=digest))
    if raw is None:
        consumed = await redis.get(_KEY_CONSUMED.format(digest=digest))
        if consumed is None:
            logger.info("logout_unknown_token digest_prefix=%s", digest[:12])
            return None
        sid = consumed
    else:
        sid = RefreshSession.from_json(raw).session_id

    await revoke_family(sid, settings)
    return sid


async def is_session_revoked(session_id: str) -> bool:
    """True when this ``sid``'s family has been revoked.

    Called by ``get_current_user`` on every guarded request. Any transport failure
    propagates rather than being swallowed — see that function for why the failure mode
    is closed rather than open.
    """
    return bool(await get_redis().exists(_KEY_REVOKED.format(sid=session_id)))


async def session_exists(token: str) -> bool:
    """Whether a refresh token is currently live. For verification, not request paths."""
    return bool(await get_redis().exists(_KEY_REFRESH.format(digest=_digest(token))))


__all__ = [
    "RefreshSession",
    "RefreshTokenInvalid",
    "RefreshTokenReused",
    "issue_session",
    "rotate_session",
    "retarget_family",
    "revoke_family",
    "revoke_by_token",
    "is_session_revoked",
    "session_exists",
]
