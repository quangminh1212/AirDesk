"""Domain exception hierarchy and FastAPI exception handlers.

Errors are modelled explicitly so the API can return stable, machine-readable
error codes (RFC 7807-style payloads) instead of leaking stack traces.
"""

from __future__ import annotations

from fastapi import FastAPI, Request, status
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse

from app.core.logging import get_logger, request_id_ctx

logger = get_logger(__name__)


class AppError(Exception):
    """Base class for all handled application errors."""

    status_code: int = status.HTTP_500_INTERNAL_SERVER_ERROR
    code: str = "internal_error"
    message: str = "An unexpected error occurred."

    def __init__(self, message: str | None = None, *, details: dict | None = None) -> None:
        super().__init__(message or self.message)
        if message:
            self.message = message
        self.details = details or {}


class NotFoundError(AppError):
    status_code = status.HTTP_404_NOT_FOUND
    code = "not_found"
    message = "Resource not found."


class ConflictError(AppError):
    status_code = status.HTTP_409_CONFLICT
    code = "conflict"
    message = "Resource conflict."


class ValidationError(AppError):
    status_code = 422  # Unprocessable Content (RFC 9110)
    code = "validation_error"
    message = "Invalid input."


class AuthError(AppError):
    status_code = status.HTTP_401_UNAUTHORIZED
    code = "unauthorized"
    message = "Authentication required."


class ForbiddenError(AppError):
    status_code = status.HTTP_403_FORBIDDEN
    code = "forbidden"
    message = "You do not have permission to perform this action."


class RateLimitError(AppError):
    status_code = status.HTTP_429_TOO_MANY_REQUESTS
    code = "rate_limited"
    message = "Too many requests."


class ProviderError(AppError):
    """Raised when an upstream flight/LLM provider fails."""

    status_code = status.HTTP_502_BAD_GATEWAY
    code = "provider_error"
    message = "Upstream provider error."


def _problem(
    *,
    status_code: int,
    code: str,
    message: str,
    details: dict | None = None,
) -> JSONResponse:
    return JSONResponse(
        status_code=status_code,
        content={
            "error": {
                "code": code,
                "message": message,
                "details": details or {},
                "request_id": request_id_ctx.get(),
            }
        },
    )


def _safe_errors(errors: list[dict]) -> list[dict]:
    """Sanitize pydantic v2 error dicts so they are JSON-serializable.

    Custom field validators raise ``ValueError`` whose message ends up inside
    the error ``ctx``. ``JSONResponse`` cannot serialize exception objects, so a
    weak-password rejection (or any validator failure) would otherwise crash the
    handler and return a 500 instead of a clean 422.
    """

    def _clean(value):  # noqa: ANN001
        if isinstance(value, Exception):
            return str(value)
        if isinstance(value, dict):
            return {k: _clean(v) for k, v in value.items()}
        if isinstance(value, (list, tuple)):
            return [_clean(v) for v in value]
        return value

    return [_clean(err) for err in errors]


def register_exception_handlers(app: FastAPI) -> None:
    """Attach all exception handlers to the FastAPI app."""

    @app.exception_handler(AppError)
    async def _handle_app_error(_request: Request, exc: AppError) -> JSONResponse:
        if exc.status_code >= 500:
            logger.error("app_error", code=exc.code, message=exc.message, details=exc.details)
        else:
            logger.info("app_error", code=exc.code, message=exc.message)
        return _problem(
            status_code=exc.status_code,
            code=exc.code,
            message=exc.message,
            details=exc.details,
        )

    @app.exception_handler(RequestValidationError)
    async def _handle_validation(_request: Request, exc: RequestValidationError) -> JSONResponse:
        return _problem(
            status_code=422,
            code="validation_error",
            message="Request payload failed validation.",
            details={"errors": _safe_errors(exc.errors())},
        )

    @app.exception_handler(Exception)
    async def _handle_unexpected(_request: Request, exc: Exception) -> JSONResponse:
        logger.exception("unhandled_exception", error=str(exc))
        return _problem(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            code="internal_error",
            message="An unexpected error occurred.",
        )
