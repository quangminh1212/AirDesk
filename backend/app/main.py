"""AirDesk FastAPI application factory.

Wires configuration, logging, middleware, error handlers, rate limiting,
prometheus metrics and the versioned API router into a single ASGI app.
"""

from __future__ import annotations

from collections.abc import AsyncIterator
from contextlib import asynccontextmanager

from fastapi import FastAPI, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.openapi.utils import get_openapi
from fastapi.responses import JSONResponse, Response
from slowapi import Limiter, _rate_limit_exceeded_handler
from slowapi.errors import RateLimitExceeded
from slowapi.util import get_remote_address

from app import __version__
from app.api.v1 import api_router
from app.core.config import get_settings
from app.core.errors import register_exception_handlers
from app.core.logging import configure_logging, get_logger
from app.core.middleware import RequestContextMiddleware

logger = get_logger(__name__)


async def _rate_limit_handler(request: Request, exc: Exception) -> Response:
    """Adapt slowapi's handler to Starlette's wider exception signature.

    Starlette types exception handlers as accepting any ``Exception``, while
    slowapi's takes ``RateLimitExceeded``. The narrower type is the true one -
    this handler is only ever registered for that class - so re-raise anything
    else rather than silently swallowing an unrelated error.
    """
    if not isinstance(exc, RateLimitExceeded):
        raise exc
    return _rate_limit_exceeded_handler(request, exc)


DESCRIPTION = """
**AirDesk** is an AI-agent platform for travel agencies and logistics operators.

* Conversational flight agent (search → price → book) with human-in-the-loop
* Pluggable flight inventory: Amadeus, Duffel, and an offline mock provider
* Vietnamese carriers: Vietnam Airlines, VietJet, Bamboo, Vietravel, Pacific
* Customer-care channels: Web, Zalo OA, Facebook Messenger, email
* Audit logging, RBAC, escalations and Prometheus metrics
"""


@asynccontextmanager
async def lifespan(app: FastAPI) -> AsyncIterator[None]:
    settings = get_settings()
    configure_logging()
    resolved_model = settings.llm_model
    if (resolved_model or "").strip().lower() == "auto":
        resolved_model = "auto (discovered from gateway /v1/models)"
    logger.info(
        "startup",
        environment=settings.environment,
        version=__version__,
        llm_model=resolved_model,
    )
    yield
    logger.info("shutdown")


def create_app() -> FastAPI:
    settings = get_settings()
    configure_logging()

    app = FastAPI(
        title="AirDesk API",
        description=DESCRIPTION,
        version=__version__,
        lifespan=lifespan,
        docs_url="/docs",
        redoc_url="/redoc",
        openapi_url="/openapi.json",
        contact={"name": "AirDesk", "url": "https://github.com/quangminh1212/AirDesk"},
        license_info={"name": "MIT"},
    )

    # --- middleware -------------------------------------------------------
    app.add_middleware(RequestContextMiddleware)
    app.add_middleware(
        CORSMiddleware,
        allow_origins=settings.cors_origins_list,
        allow_credentials=True,
        allow_methods=["*"],
        allow_headers=["*"],
        expose_headers=["x-request-id"],
    )

    # --- rate limiting ----------------------------------------------------
    limiter = Limiter(key_func=get_remote_address, default_limits=[settings.rate_limit_default])
    app.state.limiter = limiter
    app.add_exception_handler(RateLimitExceeded, _rate_limit_handler)

    # --- error handling ---------------------------------------------------
    register_exception_handlers(app)

    # --- routes -----------------------------------------------------------
    app.include_router(api_router, prefix=settings.api_v1_prefix)

    @app.get("/", include_in_schema=False)
    async def root() -> JSONResponse:
        return JSONResponse(
            {
                "name": "AirDesk API",
                "version": __version__,
                "docs": "/docs",
                "api": settings.api_v1_prefix,
            }
        )

    def custom_openapi() -> dict:
        if app.openapi_schema:
            return app.openapi_schema
        schema = get_openapi(
            title=app.title,
            version=app.version,
            description=app.description,
            routes=app.routes,
        )
        schema.setdefault("components", {}).setdefault("securitySchemes", {})["BearerAuth"] = {
            "type": "http",
            "scheme": "bearer",
            "bearerFormat": "JWT",
        }
        app.openapi_schema = schema
        return schema

    app.openapi = custom_openapi  # type: ignore[method-assign]
    return app


app = create_app()
