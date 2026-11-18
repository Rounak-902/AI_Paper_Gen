"""Authenticated, tenant-scoped SSE relay for paper assembly events."""

from __future__ import annotations

import asyncio
import json
import logging
import uuid
from dataclasses import dataclass
from typing import AsyncIterator

from fastapi import APIRouter, Depends, Path, Request
from fastapi.responses import StreamingResponse
from sqlalchemy import select

from app.common.deps import OrgContext, get_org_context
from app.common.errors import NotFoundError
from app.common.routing import EnvelopeRoute
from app.db.session import get_tenant_session
from app.infra.redis import get_redis
from app.models.paper import GeneratedPaper
from app.modules.realtime.events import paper_event_channel


logger = logging.getLogger("papergen.api.realtime")

router = APIRouter(
    prefix="/api/v1/papers",
    tags=["Paper generation"],
    route_class=EnvelopeRoute,
)


class EventStreamResponse(StreamingResponse):
    """Streaming response with an OpenAPI-visible SSE media type."""

    media_type = "text/event-stream"


@dataclass(frozen=True, slots=True)
class PaperStreamAccess:
    org_id: uuid.UUID
    paper_id: uuid.UUID


async def authorize_paper_stream(
    paper_id: uuid.UUID = Path(...),
    ctx: OrgContext = Depends(get_org_context),
) -> PaperStreamAccess:
    """Authorize before opening a long-lived stream, without holding a DB lease.

    ``get_db`` intentionally owns a transaction for the full lifetime of a normal
    request.  Using it for SSE would pin a database connection until the browser
    disconnects.  This dependency applies the same verified org context and RLS
    session, completes the paper existence check, then closes that short transaction
    before the Redis subscription is created.
    """

    async with get_tenant_session(ctx.org_id) as session:
        visible = (
            await session.execute(
                select(GeneratedPaper.id).where(
                    GeneratedPaper.id == paper_id,
                    GeneratedPaper.org_id == ctx.org_id,
                    GeneratedPaper.deleted_at.is_(None),
                )
            )
        ).scalar_one_or_none()
    if visible is None:
        raise NotFoundError("Paper not found.")
    return PaperStreamAccess(org_id=ctx.org_id, paper_id=paper_id)


def encode_sse_event(event: dict) -> str:
    """Encode one event using the browser-facing SSE wire format."""

    return (
        f"id: {event['eventId']}\n"
        f"event: {event['type']}\n"
        f"data: {json.dumps(event, separators=(',', ':'), ensure_ascii=False)}\n\n"
    )


async def paper_event_stream(
    request: Request,
    pubsub,
    *,
    channel: str,
    heartbeat_seconds: float = 15.0,
) -> AsyncIterator[str]:
    """Relay live Redis messages; no history or ``Last-Event-ID`` replay."""

    last_activity = asyncio.get_running_loop().time()
    try:
        yield ": connected\n\n"
        while not await request.is_disconnected():
            message = await pubsub.get_message(
                ignore_subscribe_messages=True,
                timeout=min(1.0, heartbeat_seconds),
            )
            now = asyncio.get_running_loop().time()
            if message is not None:
                try:
                    raw = message["data"]
                    if isinstance(raw, bytes):
                        raw = raw.decode("utf-8")
                    event = json.loads(raw)
                    yield encode_sse_event(event)
                    last_activity = now
                except (KeyError, TypeError, UnicodeDecodeError, json.JSONDecodeError):
                    logger.warning("invalid_paper_event_dropped channel=%s", channel)
            elif now - last_activity >= heartbeat_seconds:
                # An SSE comment keeps proxies and the fetch reader alive without
                # creating a domain event or consuming a paper sequence number.
                yield ": heartbeat\n\n"
                last_activity = now
    finally:
        try:
            await pubsub.unsubscribe(channel)
        finally:
            await pubsub.aclose()


@router.get(
    "/{paper_id}/events",
    response_model=None,
    response_class=EventStreamResponse,
    summary="Stream live paper assembly progress",
)
async def stream_paper_events(
    request: Request,
    access: PaperStreamAccess = Depends(authorize_paper_stream),
) -> StreamingResponse:
    channel = paper_event_channel(access.org_id, access.paper_id)
    pubsub = get_redis().pubsub()
    # Subscribe before returning the response so events cannot fall into a gap
    # between authorization and generator startup.
    try:
        await pubsub.subscribe(channel)
    except Exception:
        await pubsub.aclose()
        raise
    return EventStreamResponse(
        paper_event_stream(request, pubsub, channel=channel),
        media_type="text/event-stream",
        headers={
            "Cache-Control": "no-cache, no-transform",
            "Connection": "keep-alive",
            "X-Accel-Buffering": "no",
        },
    )


__all__ = [
    "PaperStreamAccess",
    "EventStreamResponse",
    "authorize_paper_stream",
    "encode_sse_event",
    "paper_event_stream",
    "router",
]
