"""Middleware: request-id/timing, structured access logging and metrics."""

from __future__ import annotations

import time
import uuid

from starlette.middleware.base import BaseHTTPMiddleware
from starlette.requests import Request
from starlette.responses import Response

from app.core.logging import get_logger, new_request_id, request_id_ctx

logger = get_logger("access")

try:  # pragma: no cover - metrics are optional in minimal installs
    from prometheus_client import Counter, Histogram

    _REQUESTS = Counter(
        "airdesk_http_requests_total",
        "Total HTTP requests",
        ["method", "path", "status"],
    )
    _LATENCY = Histogram(
        "airdesk_http_request_duration_seconds",
        "HTTP request latency",
        ["method", "path"],
    )
except Exception:  # noqa: BLE001
    _REQUESTS = None
    _LATENCY = None


class RequestContextMiddleware(BaseHTTPMiddleware):
    """Attach a request id, emit access logs and record metrics."""

    async def dispatch(self, request: Request, call_next) -> Response:  # type: ignore[no-untyped-def]
        rid = request.headers.get("x-request-id") or new_request_id()
        token = request_id_ctx.set(rid)
        start = time.perf_counter()
        status_code = 500
        try:
            response = await call_next(request)
            status_code = response.status_code
            response.headers["x-request-id"] = rid
            return response
        finally:
            elapsed = time.perf_counter() - start
            route = request.url.path
            logger.info(
                "request",
                method=request.method,
                path=route,
                status=status_code,
                duration_ms=round(elapsed * 1000, 2),
                request_id=rid,
            )
            if _REQUESTS is not None and _LATENCY is not None:
                _REQUESTS.labels(request.method, route, str(status_code)).inc()
                _LATENCY.labels(request.method, route).observe(elapsed)
            request_id_ctx.reset(token)


def _uuid() -> str:  # pragma: no cover - convenience
    return uuid.uuid4().hex
