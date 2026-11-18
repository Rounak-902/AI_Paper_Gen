"""Redis client — ``src/infra/redis.py`` in doc 07 §3 (P1-T7).

Doc 07 §4.1 is explicit that the auth module "**Owns tables:** Session state in Redis
(not a SQL table)", and §7.1 that the refresh token is "Stored server-side in Redis with
session metadata". That makes Redis a *correctness* dependency of authentication, not a
cache — which is why every failure path in this module fails closed rather than
degrading to "allow".

Why a module-level client rather than a dependency
--------------------------------------------------
``get_current_user`` needs the revocation check (doc 07 §7.5: "rejects
expired/invalid/**revoked** tokens"), and it runs before ``get_db`` and outside any
transaction. Threading a Redis handle through the guard chain would mean adding a
parameter to a dependency signature P1-T6 froze. A module-level client initialised in
the lifespan — exactly the shape ``app/db/session.py`` already uses for the session
factory — keeps the guard signature untouched.

Connections are pooled and lazily established: ``Redis.from_url`` does not connect, so
importing this module or building the app never touches the network. That preserves the
property ``tests/test_health.py`` relies on (drive the app with no infrastructure).
"""

from __future__ import annotations

import logging
from typing import Optional

from redis.asyncio import Redis

from app.core.config import Settings

logger = logging.getLogger("papergen.infra.redis")

#: Set by :func:`init_redis` in the app lifespan. ``None`` until then.
_redis: Optional[Redis] = None


class RedisUnavailable(RuntimeError):
    """Raised when Redis is needed but was never initialised or cannot be reached.

    Deliberately *not* an :class:`app.common.errors.AppError`. A missing Redis during a
    login is a server-side wiring or infrastructure fault (500), whereas a token this
    module cannot find is an authentication failure (401). Conflating them would let an
    outage read as "your credentials are wrong" — the same distinction P1-T6 drew
    between ``TenantContextMissing`` (500) and a guard rejection (403).
    """


def init_redis(settings: Settings) -> Redis:
    """Create the module-level client. Called once, from the app lifespan."""
    global _redis

    _redis = Redis.from_url(
        settings.REDIS_URL,
        # Every value this module stores is JSON text or a short marker, so decoding at
        # the client keeps call sites free of ``.decode()`` noise. It also means a
        # ``GETDEL`` miss is ``None`` rather than ``b''``, which the rotation logic
        # distinguishes from a present-but-empty value.
        decode_responses=True,
        # Doc 07 §20.2 lists TLS for data in transit. Honoured via the URL scheme when
        # ``REDIS_TLS`` is on: ``rediss://`` is what actually selects TLS in redis-py,
        # so the flag is validated here rather than silently ignored.
        socket_connect_timeout=5,
        socket_timeout=5,
        retry_on_timeout=True,
        health_check_interval=30,
    )

    if settings.REDIS_TLS and not settings.REDIS_URL.startswith("rediss://"):
        # Loud, not fatal: refusing to boot would break local development where the
        # flag is off anyway, but a silent no-op on a security setting is worse.
        logger.error(
            "redis_tls_requested_but_url_is_not_rediss url_scheme=%s — "
            "REDIS_TLS has no effect unless REDIS_URL uses rediss://",
            settings.REDIS_URL.split("://", 1)[0],
        )

    logger.info("redis_client_initialised db=%s", settings.REDIS_URL.rsplit("/", 1)[-1])
    return _redis


def get_redis() -> Redis:
    """Return the initialised client, or raise :class:`RedisUnavailable`."""
    if _redis is None:
        raise RedisUnavailable(
            "Redis client not initialised. Call init_redis(settings) in the app "
            "lifespan before serving requests."
        )
    return _redis


async def close_redis() -> None:
    """Release the connection pool on shutdown."""
    global _redis
    if _redis is not None:
        await _redis.aclose()
        _redis = None
        logger.info("redis_client_closed")


async def ping_redis() -> bool:
    """True when Redis answers. Used by the verification harness, not by request paths."""
    try:
        return bool(await get_redis().ping())
    except RedisUnavailable:
        raise
    except Exception as exc:  # noqa: BLE001 - any transport failure means "not ready"
        logger.error("redis_ping_failed error=%s", type(exc).__name__)
        return False


__all__ = ["RedisUnavailable", "init_redis", "get_redis", "close_redis", "ping_redis"]
