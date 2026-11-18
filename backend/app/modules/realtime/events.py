"""Paper event envelope and Redis publication.

Redis pub/sub is intentionally a live transport, not an event store.  The event id and
per-paper sequence make live delivery orderable and duplicate-safe, but this module
does not retain events or implement ``Last-Event-ID`` replay.  P5-T6 owns reconnect
and reconciliation against durable paper/slot state.
"""

from __future__ import annotations

import json
import uuid
from datetime import datetime, timezone
from typing import Any

from redis import Redis


PAPER_GENERATION_PROGRESS = "paper.generation.progress"
PAPER_GENERATION_FAILED = "paper.generation.failed"
PAPER_QUESTION_READY = "paper.question.ready"
PAPER_GENERATED = "paper.generated"

_CHANNEL_PREFIX = "papergen:events:paper"
_SEQUENCE_PREFIX = "papergen:events:sequence:paper"

# Assign the sequence and publish in one Redis operation.  A separate INCR followed
# by PUBLISH can be reordered when multiple workers finish at nearly the same time.
_PUBLISH_SCRIPT = """
local sequence = redis.call('INCR', KEYS[1])
local event = cjson.decode(ARGV[1])
event['sequence'] = sequence
local encoded = cjson.encode(event)
redis.call('PUBLISH', KEYS[2], encoded)
return {sequence, encoded}
"""


def paper_event_channel(org_id: uuid.UUID | str, paper_id: uuid.UUID | str) -> str:
    """Return the tenant- and paper-scoped live event channel."""

    return f"{_CHANNEL_PREFIX}:{org_id}:{paper_id}"


def paper_sequence_key(org_id: uuid.UUID | str, paper_id: uuid.UUID | str) -> str:
    """Return the Redis key holding the monotonic sequence for one paper."""

    return f"{_SEQUENCE_PREFIX}:{org_id}:{paper_id}"


def utc_timestamp() -> str:
    return datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")


def publish_paper_event(
    redis_client: Redis,
    *,
    event_type: str,
    org_id: uuid.UUID | str,
    paper_id: uuid.UUID | str,
    payload: dict[str, Any],
    occurred_at: str | None = None,
    event_id: uuid.UUID | str | None = None,
) -> dict[str, Any]:
    """Publish one frontend-contract event and return the exact encoded envelope."""

    org = str(org_id)
    paper = str(paper_id)
    timestamp = occurred_at or utc_timestamp()
    event = {
        "eventId": str(event_id or uuid.uuid4()),
        "type": event_type,
        "orgId": org,
        "resourceType": "paper",
        "resourceId": paper,
        # Lua replaces this placeholder atomically before publication.
        "sequence": 0,
        "occurredAt": timestamp,
        "payload": payload,
    }
    result = redis_client.eval(
        _PUBLISH_SCRIPT,
        2,
        paper_sequence_key(org, paper),
        paper_event_channel(org, paper),
        json.dumps(event, separators=(",", ":"), ensure_ascii=False),
    )
    encoded = result[1]
    if isinstance(encoded, bytes):
        encoded = encoded.decode("utf-8")
    return json.loads(encoded)


__all__ = [
    "PAPER_GENERATED",
    "PAPER_GENERATION_FAILED",
    "PAPER_GENERATION_PROGRESS",
    "PAPER_QUESTION_READY",
    "paper_event_channel",
    "paper_sequence_key",
    "publish_paper_event",
    "utc_timestamp",
]
