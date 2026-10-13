"""Health, readiness and metrics endpoints."""

from __future__ import annotations

from fastapi import APIRouter
from fastapi.responses import PlainTextResponse

from app.api.deps import DbSession
from app.core.config import get_settings
from app.providers.registry import available_provider_names

router = APIRouter(tags=["system"])


@router.get("/health", summary="Liveness probe")
async def health() -> dict[str, str]:
    return {"status": "ok"}


@router.get("/ready", summary="Readiness probe (checks DB)")
async def ready(db: DbSession) -> dict[str, object]:
    from sqlalchemy import text

    db_ok = True
    try:
        await db.execute(text("SELECT 1"))
    except Exception:  # noqa: BLE001
        db_ok = False
    settings = get_settings()
    return {
        "status": "ok" if db_ok else "degraded",
        "database": db_ok,
        "environment": settings.environment,
        "providers": available_provider_names(),
        "llm_model": settings.llm_model,
    }


@router.get("/metrics", response_class=PlainTextResponse, summary="Prometheus metrics")
async def metrics() -> PlainTextResponse:
    from prometheus_client import CONTENT_TYPE_LATEST, generate_latest

    return PlainTextResponse(generate_latest(), media_type=CONTENT_TYPE_LATEST)
