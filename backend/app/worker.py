"""Celery application for background jobs (email sync, report generation).

Kept intentionally thin: the async FastAPI app does request-scoped work, while
long-running/periodic tasks (mailbox polling, nightly reports, offer cache
warming) run here so they never block the API.
"""

from __future__ import annotations

from celery import Celery
from celery.schedules import crontab

from app.core.config import get_settings

settings = get_settings()

celery_app = Celery(
    "airdesk",
    broker=settings.redis_url,
    backend=settings.redis_url,
)

celery_app.conf.update(
    task_serializer="json",
    result_serializer="json",
    accept_content=["json"],
    timezone="Asia/Ho_Chi_Minh",
    enable_utc=True,
    task_track_started=True,
    task_time_limit=600,
    task_soft_time_limit=540,
)

celery_app.conf.beat_schedule = {
    "poll-mailboxes-every-5-min": {
        "task": "app.worker.poll_mailboxes",
        "schedule": crontab(minute="*/5"),
    },
    "warm-offer-cache-hourly": {
        "task": "app.worker.warm_offer_cache",
        "schedule": crontab(minute=0),
    },
}


@celery_app.task(name="app.worker.poll_mailboxes")
def poll_mailboxes() -> dict:
    """Placeholder for the email-sync worker (wired to integrations.email)."""
    from app.core.logging import configure_logging, get_logger

    configure_logging()
    logger = get_logger(__name__)
    logger.info("worker_poll_mailboxes_tick")
    return {"status": "ok", "task": "poll_mailboxes"}


@celery_app.task(name="app.worker.warm_offer_cache")
def warm_offer_cache() -> dict:
    """Placeholder for periodic offer-cache warming."""
    from app.core.logging import configure_logging, get_logger

    configure_logging()
    logger = get_logger(__name__)
    logger.info("worker_warm_offer_cache_tick")
    return {"status": "ok", "task": "warm_offer_cache"}
