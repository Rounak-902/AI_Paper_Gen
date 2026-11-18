"""Task-envelope idempotency for Celery producers and consumers.

An atomic Redis ``SET ... NX`` stores the first Celery task ID for a caller-supplied
idempotency key. A duplicate enqueue returns an ``AsyncResult`` for that original
task and publishes no second message. The value is the task ID (not a lock token),
so broker redelivery of the *same* unacknowledged message is accepted after a worker
crash while a different task ID carrying the same key is ignored.

This protects queue publication. P4-T5 must additionally persist per-slot
checkpoints and enforce write idempotency in PostgreSQL; Redis cannot make a
business transaction atomic with a database commit.
"""

from __future__ import annotations

import hashlib
from dataclasses import dataclass
from functools import lru_cache
from typing import Any, Mapping, MutableMapping, Optional
from uuid import uuid4

from celery import Task
from celery.exceptions import Ignore
from celery.result import AsyncResult
from redis import Redis

from app.core.config import get_settings

_KEY_NAMESPACE = "papergen:task-idempotency:v1"


@dataclass(frozen=True)
class Reservation:
    """Outcome of atomically reserving a caller key."""

    owner_task_id: str
    acquired: bool


class RedisIdempotencyStore:
    """Redis-backed first-writer-wins mapping from caller key to task ID."""

    _RELEASE_IF_OWNER = """
    local value = redis.call('GET', KEYS[1])
    if value == ARGV[1] or value == ('pending:' .. ARGV[1]) then
      return redis.call('DEL', KEYS[1])
    end
    return 0
    """

    _CONFIRM_IF_OWNER = """
    local value = redis.call('GET', KEYS[1])
    if value == ARGV[1] or value == ('pending:' .. ARGV[1]) then
      redis.call('SET', KEYS[1], ARGV[1], 'EX', ARGV[2])
      return 1
    end
    return 0
    """

    def __init__(
        self,
        redis_client: Redis,
        *,
        ttl_seconds: int,
        reservation_ttl_seconds: int | None = None,
    ) -> None:
        self._redis = redis_client
        self._ttl_seconds = ttl_seconds
        self._reservation_ttl_seconds = reservation_ttl_seconds or ttl_seconds

    @staticmethod
    def redis_key(idempotency_key: str) -> str:
        digest = hashlib.sha256(idempotency_key.encode("utf-8")).hexdigest()
        return f"{_KEY_NAMESPACE}:{digest}"

    def reserve(self, idempotency_key: str, task_id: str) -> Reservation:
        key = self.redis_key(idempotency_key)
        acquired = bool(
            self._redis.set(
                key,
                f"pending:{task_id}",
                nx=True,
                ex=self._reservation_ttl_seconds,
            )
        )
        if acquired:
            return Reservation(owner_task_id=task_id, acquired=True)

        owner = self._redis.get(key)
        if owner is None:
            # The key may have expired between SET NX and GET. One bounded retry
            # closes that race without spinning during a Redis outage.
            acquired = bool(
                self._redis.set(
                    key,
                    f"pending:{task_id}",
                    nx=True,
                    ex=self._reservation_ttl_seconds,
                )
            )
            if acquired:
                return Reservation(owner_task_id=task_id, acquired=True)
            owner = self._redis.get(key)
        if owner is None:
            raise RuntimeError("idempotency reservation disappeared during enqueue")
        owner_task_id = str(owner)
        if owner_task_id.startswith("pending:"):
            owner_task_id = owner_task_id.removeprefix("pending:")
        return Reservation(owner_task_id=owner_task_id, acquired=False)

    def confirm(self, idempotency_key: str, task_id: str) -> bool:
        """Promote a short producer lease to the normal deduplication TTL."""

        key = self.redis_key(idempotency_key)
        return bool(
            self._redis.eval(
                self._CONFIRM_IF_OWNER,
                1,
                key,
                task_id,
                self._ttl_seconds,
            )
        )

    def release(self, idempotency_key: str, task_id: str) -> bool:
        """Release only a reservation still owned by ``task_id``."""

        key = self.redis_key(idempotency_key)
        return bool(self._redis.eval(self._RELEASE_IF_OWNER, 1, key, task_id))


@lru_cache(maxsize=1)
def get_idempotency_store() -> RedisIdempotencyStore:
    settings = get_settings()
    client = Redis.from_url(settings.QUEUE_REDIS_URL, decode_responses=True)
    return RedisIdempotencyStore(
        client,
        ttl_seconds=settings.TASK_IDEMPOTENCY_TTL_SECONDS,
        reservation_ttl_seconds=settings.TASK_PUBLISH_RESERVATION_TTL_SECONDS,
    )


def _require_key(kwargs: Mapping[str, Any]) -> str:
    raw_key = kwargs.get("idempotency_key")
    if not isinstance(raw_key, str) or not raw_key.strip():
        raise ValueError(
            "Every PaperGen task requires a non-empty caller-supplied "
            "idempotency_key keyword argument"
        )
    return raw_key.strip()


class IdempotentTask(Task):
    """Celery base task enforcing late ack and caller-key deduplication."""

    abstract = True
    acks_late = True
    reject_on_worker_lost = True

    def apply_async(
        self,
        args: Optional[tuple[Any, ...]] = None,
        kwargs: Optional[MutableMapping[str, Any]] = None,
        task_id: Optional[str] = None,
        **options: Any,
    ) -> AsyncResult:
        task_kwargs: MutableMapping[str, Any] = dict(kwargs or {})
        idempotency_key = _require_key(task_kwargs)
        task_kwargs["idempotency_key"] = idempotency_key
        selected_task_id = task_id or str(uuid4())
        store = get_idempotency_store()
        reservation = store.reserve(idempotency_key, selected_task_id)

        if not reservation.acquired and reservation.owner_task_id != selected_task_id:
            return self.AsyncResult(reservation.owner_task_id)

        try:
            result = super().apply_async(
                args=args,
                kwargs=task_kwargs,
                task_id=selected_task_id,
                **options,
            )
            # Publication returned successfully.  If this process dies before
            # this promotion, the short lease expires and a durable outbox may
            # safely republish the same deterministic task id.
            confirm = getattr(store, "confirm", None)
            if confirm is not None:
                confirm(idempotency_key, selected_task_id)
            return result
        except Exception:
            # A publish failure must not poison the key. Compare-and-delete keeps
            # this producer from releasing a reservation it no longer owns.
            store.release(idempotency_key, selected_task_id)
            raise

    def __call__(self, *args: Any, **kwargs: Any) -> Any:
        idempotency_key = _require_key(kwargs)
        task_id = self.request.id
        if not task_id:
            raise RuntimeError("Celery did not assign a task ID")

        reservation = get_idempotency_store().reserve(idempotency_key, task_id)
        if reservation.owner_task_id != task_id:
            # This catches messages published outside apply_async/send_task. It is
            # acknowledged as ignored and never reaches business logic.
            raise Ignore()
        confirm = getattr(get_idempotency_store(), "confirm", None)
        if confirm is not None:
            confirm(idempotency_key, task_id)
        return super().__call__(*args, **kwargs)


def enqueue_idempotent(
    task: IdempotentTask,
    *,
    idempotency_key: str,
    kwargs: Optional[Mapping[str, Any]] = None,
    **options: Any,
) -> AsyncResult:
    """Public producer API; the key is explicit and cannot be positional."""

    payload = dict(kwargs or {})
    payload["idempotency_key"] = idempotency_key
    return task.apply_async(kwargs=payload, **options)


__all__ = [
    "IdempotentTask",
    "RedisIdempotencyStore",
    "Reservation",
    "enqueue_idempotent",
    "get_idempotency_store",
]
