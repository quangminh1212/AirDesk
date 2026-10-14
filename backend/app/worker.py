"""Celery application for background jobs (email sync, report generation).

Kept intentionally thin: the async FastAPI app does request-scoped work, while
long-running/periodic tasks (mailbox polling, nightly reports, offer cache
warming) run here so they never block the API.
"""

from __future__ import annotations

import asyncio

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
    """Poll every configured mailbox, ingest and auto-reply to customer emails.

    The actual IMAP fetch, classification and reply logic lives in
    :class:`app.services.email_ingest.EmailIngestService`; this task only owns
    the lifecycle (a DB session + an LLM gateway) around one ``poll_and_ingest``
    call. When IMAP is unconfigured the service short-circuits to a no-op so the
    beat still reports healthy rather than erroring every 5 minutes.
    """
    from app.core.logging import configure_logging, get_logger
    from app.db.session import SessionLocal
    from app.services.email_ingest import EmailIngestService

    configure_logging()
    logger = get_logger(__name__)

    # Honour the documented EMAIL_POLL_ENABLED flag. The beat still ticks every
    # 5 minutes, but when the operator has explicitly disabled polling we stay
    # out of the mailbox instead of running classify/send work for nothing.
    if not settings.email_poll_enabled:
        logger.info("worker_poll_mailboxes_disabled")
        return {"status": "skipped", "reason": "EMAIL_POLL_ENABLED=false"}

    limit = settings.imap_max_messages
    summary = asyncio.run(_poll_cycle(limit, logger))
    return summary.model_dump()


async def _poll_cycle(limit: int, logger) -> "object":
    from app.core.llm import get_llm_gateway

    async with SessionLocal() as db:
        service = EmailIngestService(db, gateway=get_llm_gateway())
        try:
            summary = await service.poll_and_ingest(limit=limit)
        except Exception as exc:  # noqa: BLE001 - one failed cycle must not kill the beat
            logger.exception("worker_poll_mailboxes_failed", error=str(exc))
            # Return a structured, serialisable record so the failure is visible
            # in Flower/result backend instead of a raw traceback string.
            from app.schemas.inbox import PollSummary

            return PollSummary(configured=True, errors=[f"worker: {exc}"])
        return summary


@celery_app.task(name="app.worker.warm_offer_cache")
def warm_offer_cache() -> dict:
    """Periodic cache-backend health beat.

    Offers are cached lazily on real user searches (see
    :mod:`app.api.v1.flights`), so there is nothing to proactively pre-warm.
    This beat instead verifies the cache backend is reachable and surfaces a
    silent fallback to the in-process store (e.g. a dead Redis) in the logs,
    which is the only proactive safety net worth running on a timer.
    """
    from app.core.logging import configure_logging, get_logger
    from app.services.offer_cache import offer_cache_backend

    configure_logging()
    logger = get_logger(__name__)

    backend = asyncio.run(offer_cache_backend())
    logger.info("worker_offer_cache_beat", backend=backend)
    return {"status": "ok", "task": "warm_offer_cache", "backend": backend}
