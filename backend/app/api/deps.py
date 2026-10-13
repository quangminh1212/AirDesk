"""FastAPI dependencies: auth, RBAC, pagination and shared services."""

from __future__ import annotations

from collections.abc import Awaitable, Callable
from typing import Annotated

from fastapi import Depends, Query, Request
from fastapi.security import OAuth2PasswordBearer
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.config import get_settings
from app.core.errors import AuthError, ForbiddenError
from app.core.llm import LiteLLMGateway, get_llm_gateway
from app.core.security import decode_token
from app.db.session import get_db
from app.models.user import UserRole
from app.schemas.auth import CurrentUser

oauth2_scheme = OAuth2PasswordBearer(tokenUrl="/api/v1/auth/token", auto_error=False)

DbSession = Annotated[AsyncSession, Depends(get_db)]

# Role hierarchy: a higher role implicitly satisfies lower-role requirements.
_ROLE_RANK = {
    UserRole.VIEWER: 0,
    UserRole.AGENT: 1,
    UserRole.OPERATOR: 2,
    UserRole.ADMIN: 3,
}


async def get_current_user(
    token: Annotated[str | None, Depends(oauth2_scheme)],
) -> CurrentUser:
    """Decode the bearer token into the current user identity."""
    if not token:
        raise AuthError("Missing bearer token.")
    payload = decode_token(token, expected_type="access")
    return CurrentUser(
        id=payload["sub"],
        email=payload.get("email", ""),
        role=UserRole(payload.get("role", "viewer")),
        scopes=payload.get("scopes", []),
    )


def require_role(minimum: UserRole) -> Callable[[CurrentUser], Awaitable[CurrentUser]]:
    """Dependency factory enforcing a minimum role level."""

    async def _dependency(user: Annotated[CurrentUser, Depends(get_current_user)]) -> CurrentUser:
        if _ROLE_RANK[user.role] < _ROLE_RANK[minimum]:
            raise ForbiddenError(f"Requires at least role '{minimum.value}'.")
        return user

    return _dependency


async def get_agent_gateway() -> LiteLLMGateway:
    return get_llm_gateway()


def get_pagination(
    page: Annotated[int, Query(ge=1)] = 1,
    page_size: Annotated[int, Query(ge=1, le=100)] = 20,
) -> tuple[int, int, int]:
    """Return (page, page_size, offset)."""
    return page, page_size, (page - 1) * page_size


def client_ip(request: Request) -> str | None:
    """Best-effort client IP, honouring a single proxy hop."""
    forwarded = request.headers.get("x-forwarded-for")
    if forwarded:
        return forwarded.split(",")[0].strip()
    return request.client.host if request.client else None


# Convenience aliases used across routers.
CurrentUserDep = Annotated[CurrentUser, Depends(get_current_user)]
AdminUser = Annotated[CurrentUser, Depends(require_role(UserRole.ADMIN))]
OperatorUser = Annotated[CurrentUser, Depends(require_role(UserRole.OPERATOR))]
Pagination = Annotated[tuple[int, int, int], Depends(get_pagination)]
Gateway = Annotated[LiteLLMGateway, Depends(get_agent_gateway)]

__all__ = [
    "AdminUser",
    "CurrentUser",
    "CurrentUserDep",
    "DbSession",
    "Gateway",
    "OperatorUser",
    "Pagination",
    "client_ip",
    "get_current_user",
    "get_settings",
    "require_role",
]
