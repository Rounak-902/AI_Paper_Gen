"""P4-T6 event-contract and SSE relay tests."""

from __future__ import annotations

import json
import uuid
from collections import deque

import pytest
from sqlalchemy.dialects.postgresql import JSONB

from app.main import app
from app.models.assembly import PaperAssemblyRun
from app.models.paper import GeneratedPaper
from app.modules.realtime.events import (
    PAPER_GENERATION_FAILED,
    PAPER_GENERATION_PROGRESS,
    PAPER_QUESTION_READY,
    paper_event_channel,
    paper_sequence_key,
    publish_paper_event,
)
from app.modules.realtime.router import encode_sse_event, paper_event_stream
from app.workers import tasks


class _ScriptRedis:
    def __init__(self) -> None:
        self.sequence = 0
        self.calls: list[tuple] = []

    def eval(self, script, key_count, sequence_key, channel, raw_event):
        self.calls.append((script, key_count, sequence_key, channel, raw_event))
        self.sequence += 1
        event = json.loads(raw_event)
        event["sequence"] = self.sequence
        return [self.sequence, json.dumps(event)]


def test_publish_uses_tenant_paper_channel_and_frontend_envelope() -> None:
    org_id = uuid.uuid4()
    paper_id = uuid.uuid4()
    redis = _ScriptRedis()

    event = publish_paper_event(
        redis,
        event_type=PAPER_GENERATION_PROGRESS,
        org_id=org_id,
        paper_id=paper_id,
        payload={"slotId": "A:1", "status": "running", "timestamp": "now"},
        occurred_at="2026-09-21T12:00:00Z",
    )

    assert event.keys() == {
        "eventId",
        "type",
        "orgId",
        "resourceType",
        "resourceId",
        "sequence",
        "occurredAt",
        "payload",
    }
    assert event["sequence"] == 1
    assert event["orgId"] == str(org_id)
    assert event["resourceId"] == str(paper_id)
    assert redis.calls[0][2] == paper_sequence_key(org_id, paper_id)
    assert redis.calls[0][3] == paper_event_channel(org_id, paper_id)


def test_completed_checkpoint_emits_progress_and_question_ready(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    published: list[dict] = []

    class _Client:
        def close(self) -> None:
            pass

    monkeypatch.setattr(tasks, "_paper_events_redis", lambda: _Client())
    monkeypatch.setattr(
        tasks,
        "publish_paper_event",
        lambda client, **kwargs: published.append(kwargs) or kwargs,
    )
    observation = {
        "org_id": uuid.uuid4(),
        "paper_id": uuid.uuid4(),
        "run_id": uuid.uuid4(),
        "slot_id": "A:1",
        "section_id": "A",
        "position": 1,
        "status": "completed",
        "attempt_count": 1,
        "completed_slots": 1,
        "total_slots": 2,
        "percent": 50,
        "question_id": uuid.uuid4(),
        "snapshot": {"id": "question-1", "stem": "Real assembled question"},
        "last_error": None,
    }

    tasks._publish_slot_observation(observation)

    assert [item["event_type"] for item in published] == [
        PAPER_GENERATION_PROGRESS,
        PAPER_QUESTION_READY,
    ]
    progress = published[0]["payload"]
    assert progress == {
        "jobId": str(observation["run_id"]),
        "paperId": str(observation["paper_id"]),
        "runId": str(observation["run_id"]),
        "slotId": "A:1",
        "sectionId": "A",
        "position": 1,
        "status": "completed",
        "timestamp": published[0]["occurred_at"],
        "stage": "assembly",
        "percent": 50,
        "filledSlots": 1,
        "totalSlots": 2,
        "attemptCount": 1,
    }
    assert published[1]["payload"]["question"] == observation["snapshot"]


def test_exhausted_checkpoint_emits_retryable_terminal_failure(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    published: list[dict] = []
    notification_events: list[dict] = []

    class _Client:
        def close(self) -> None:
            pass

    monkeypatch.setattr(tasks, "_paper_events_redis", lambda: _Client())
    monkeypatch.setattr(
        tasks,
        "publish_paper_event",
        lambda client, **kwargs: published.append(kwargs) or kwargs,
    )
    monkeypatch.setattr(
        tasks,
        "_enqueue_notification_event",
        lambda **kwargs: notification_events.append(kwargs),
    )
    observation = {
        "org_id": uuid.uuid4(),
        "paper_id": uuid.uuid4(),
        "run_id": uuid.uuid4(),
        "slot_id": "A:2",
        "section_id": "A",
        "position": 2,
        "status": "pending",
        "attempt_count": 3,
        "completed_slots": 1,
        "total_slots": 4,
        "percent": 25,
        "question_id": None,
        "snapshot": None,
        "last_error": "ProviderQuotaError: quota exhausted",
    }

    tasks._publish_slot_observation(
        observation,
        status="failed",
        terminal_failure=True,
    )

    assert [item["event_type"] for item in published] == [PAPER_GENERATION_FAILED]
    assert published[0]["payload"]["status"] == "failed"
    assert published[0]["payload"]["retryable"] is True
    assert "Retry generation" in published[0]["payload"]["message"]
    assert len(notification_events) == 1
    assert notification_events[0]["event_type"] == PAPER_GENERATION_FAILED
    assert notification_events[0]["event_id"] == published[0]["event_id"]


class _Request:
    def __init__(self) -> None:
        self.checks = 0

    async def is_disconnected(self) -> bool:
        self.checks += 1
        return self.checks > 1


class _PubSub:
    def __init__(self, event: dict) -> None:
        self.messages = deque([{"data": json.dumps(event)}])
        self.unsubscribed = None
        self.closed = False

    async def get_message(self, **kwargs):
        return self.messages.popleft() if self.messages else None

    async def unsubscribe(self, channel: str) -> None:
        self.unsubscribed = channel

    async def aclose(self) -> None:
        self.closed = True


@pytest.mark.asyncio
async def test_sse_relay_forwards_real_envelope_and_cleans_up() -> None:
    event = {
        "eventId": str(uuid.uuid4()),
        "type": PAPER_GENERATION_PROGRESS,
        "orgId": str(uuid.uuid4()),
        "resourceType": "paper",
        "resourceId": str(uuid.uuid4()),
        "sequence": 7,
        "occurredAt": "2026-09-21T12:00:00Z",
        "payload": {"slotId": "A:2", "status": "completed", "timestamp": "now"},
    }
    pubsub = _PubSub(event)
    stream = paper_event_stream(_Request(), pubsub, channel="paper-channel")

    assert await anext(stream) == ": connected\n\n"
    assert await anext(stream) == encode_sse_event(event)
    with pytest.raises(StopAsyncIteration):
        await anext(stream)
    assert pubsub.unsubscribed == "paper-channel"
    assert pubsub.closed is True


@pytest.mark.asyncio
async def test_sse_relay_cleans_up_when_client_leaves_after_connect() -> None:
    pubsub = _PubSub({})
    stream = paper_event_stream(_Request(), pubsub, channel="paper-channel")

    assert await anext(stream) == ": connected\n\n"
    await stream.aclose()

    assert pubsub.unsubscribed == "paper-channel"
    assert pubsub.closed is True


def test_sse_route_is_raw_streaming_response() -> None:
    operation = app.openapi()["paths"]["/api/v1/papers/{paper_id}/events"]["get"]
    assert operation["responses"]["200"]["content"] == {
        "text/event-stream": {"schema": {"type": "string"}}
    }


def test_blueprint_and_solver_snapshots_already_persisted() -> None:
    blueprint = GeneratedPaper.__table__.c.blueprint
    solver_snapshot = PaperAssemblyRun.__table__.c.solver_snapshot
    assert isinstance(blueprint.type, JSONB)
    assert blueprint.nullable is False
    assert isinstance(solver_snapshot.type, JSONB)
    assert solver_snapshot.nullable is False
