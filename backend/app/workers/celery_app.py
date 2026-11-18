"""PaperGen Celery application backed by Redis.

The broker and result backend intentionally share ``QUEUE_REDIS_URL``. Redis DB 1
is already reserved for queue traffic while DB 0 remains available to request-path
cache/rate-limit/progress concerns.
"""

from __future__ import annotations

from celery import Celery

from app.core.config import get_settings

settings = get_settings()

celery_app = Celery(
    "papergen",
    broker=settings.QUEUE_REDIS_URL,
    backend=settings.QUEUE_REDIS_URL,
    include=["app.workers.tasks", "app.workers.notification_tasks"],
)

_visibility_timeout = settings.CELERY_VISIBILITY_TIMEOUT_SECONDS
celery_app.conf.update(
    broker_transport_options={"visibility_timeout": _visibility_timeout},
    result_backend_transport_options={"visibility_timeout": _visibility_timeout},
    task_acks_late=True,
    # A child/worker disappearing is a broker-redelivery event, not a success.
    task_reject_on_worker_lost=True,
    task_acks_on_failure_or_timeout=True,
    task_track_started=True,
    worker_prefetch_multiplier=1,
    broker_connection_retry_on_startup=True,
    result_expires=settings.TASK_IDEMPOTENCY_TTL_SECONDS,
    task_default_queue="generation",
    task_routes={
        "papergen.workers.notification.*": {"queue": "notifications"},
        "papergen.workers.*": {"queue": "generation"},
    },
    accept_content=["json"],
    task_serializer="json",
    result_serializer="json",
    timezone="UTC",
    enable_utc=True,
    beat_schedule={
        "generation-queue-keepalive": {
            "task": "papergen.workers.queue_keepalive",
            "schedule": settings.CELERY_QUEUE_KEEPALIVE_SECONDS,
            # Pulses are disposable. Expiry prevents a stopped worker from
            # accumulating a keepalive backlog that delays the real recovery.
            "options": {
                "queue": "generation",
                "expires": max(1.0, settings.CELERY_QUEUE_KEEPALIVE_SECONDS * 2),
            },
        },
        "generation-outbox-reconcile": {
            "task": "papergen.workers.dispatch_outbox",
            "schedule": settings.CELERY_OUTBOX_POLL_SECONDS,
            "options": {
                "queue": "generation",
                "expires": max(1.0, settings.CELERY_OUTBOX_POLL_SECONDS * 2),
            },
        },
        "notification-event-dispatch": {
            "task": "papergen.workers.notification.dispatch",
            "schedule": settings.NOTIFICATION_EVENT_POLL_SECONDS,
            "options": {
                "queue": "notifications",
                "expires": max(1.0, settings.NOTIFICATION_EVENT_POLL_SECONDS * 2),
            },
        },
    },
)

__all__ = ["celery_app"]
