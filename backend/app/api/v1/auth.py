"""Authentication endpoints: token, refresh, me, and user administration."""

from __future__ import annotations

import hashlib
from datetime import UTC, datetime, timedelta

from fastapi import APIRouter, Depends, Request, status
from fastapi.security import OAuth2PasswordRequestForm
from sqlalchemy import select

from app.api.deps import AdminUser, CurrentUserDep, DbSession
from app.core.config import get_settings
from app.core.errors import AuthError, ConflictError, NotFoundError
from app.core.logging import get_logger
from app.core.security import create_token, hash_password, verify_password
from app.models.user import RefreshToken, User, UserRole
from app.schemas.auth import (
    LoginRequest,
    RefreshRequest,
    TokenPair,
    UserCreate,
    UserRead,
    UserUpdate,
)
from app.services.audit import record_audit

router = APIRouter(prefix="/auth", tags=["auth"])
logger = get_logger(__name__)


def _hash_token(token: str) -> str:
    return hashlib.sha256(token.encode()).hexdigest()


async def _issue_tokens(db: DbSession, user: User, request: Request) -> TokenPair:
    settings = get_settings()
    access = create_token(
        subject=user.id,
        token_type="access",
        scopes=[user.role.value],
        extra={"email": user.email, "role": user.role.value},
    )
    refresh = create_token(subject=user.id, token_type="refresh")
    db.add(
        RefreshToken(
            user_id=user.id,
            token_hash=_hash_token(refresh),
            expires_at=datetime.now(UTC) + timedelta(days=settings.refresh_token_expire_days),
            user_agent=request.headers.get("user-agent"),
            ip_address=request.client.host if request.client else None,
        )
    )
    user.last_login_at = datetime.now(UTC)
    await db.commit()
    return TokenPair(
        access_token=access,
        refresh_token=refresh,
        expires_in=settings.access_token_expire_minutes * 60,
    )


@router.post("/token", response_model=TokenPair, summary="OAuth2 password grant (form)")
async def login_form(
    db: DbSession,
    request: Request,
    form: OAuth2PasswordRequestForm = Depends(),  # noqa: B008
) -> TokenPair:
    """Standard OAuth2 password flow, used by the Swagger 'Authorize' button."""
    user = await db.scalar(select(User).where(User.email == form.username.lower()))
    if not user or not verify_password(form.password, user.hashed_password):
        raise AuthError("Incorrect email or password.")
    if not user.is_active:
        raise AuthError("Account is disabled.")
    return await _issue_tokens(db, user, request)


@router.post("/login", response_model=TokenPair, summary="Login with JSON body")
async def login_json(db: DbSession, request: Request, payload: LoginRequest) -> TokenPair:
    user = await db.scalar(select(User).where(User.email == payload.email.lower()))
    if not user or not verify_password(payload.password, user.hashed_password):
        raise AuthError("Incorrect email or password.")
    if not user.is_active:
        raise AuthError("Account is disabled.")
    return await _issue_tokens(db, user, request)


@router.post("/refresh", response_model=TokenPair, summary="Rotate access token")
async def refresh(db: DbSession, request: Request, payload: RefreshRequest) -> TokenPair:
    from app.core.security import decode_token

    claims = decode_token(payload.refresh_token, expected_type="refresh")
    stored = await db.scalar(
        select(RefreshToken).where(RefreshToken.token_hash == _hash_token(payload.refresh_token))
    )
    if not stored or stored.revoked or stored.expires_at < datetime.now(UTC):
        raise AuthError("Refresh token is invalid or revoked.")
    user = await db.get(User, claims["sub"])
    if not user or not user.is_active:
        raise AuthError("Account is disabled.")
    stored.revoked = True  # rotation
    return await _issue_tokens(db, user, request)


@router.post("/logout", status_code=status.HTTP_204_NO_CONTENT)
async def logout(db: DbSession, payload: RefreshRequest) -> None:
    stored = await db.scalar(
        select(RefreshToken).where(RefreshToken.token_hash == _hash_token(payload.refresh_token))
    )
    if stored:
        stored.revoked = True
        await db.commit()


@router.get("/me", response_model=UserRead, summary="Current user profile")
async def me(db: DbSession, user: CurrentUserDep) -> User:
    obj = await db.get(User, user.id)
    if obj is None:
        raise NotFoundError("User not found.")
    return obj


@router.post(
    "/users",
    response_model=UserRead,
    status_code=status.HTTP_201_CREATED,
    summary="Create a user (admin)",
)
async def create_user(db: DbSession, admin: AdminUser, payload: UserCreate) -> User:
    exists = await db.scalar(select(User).where(User.email == payload.email.lower()))
    if exists:
        raise ConflictError("A user with this email already exists.")
    user = User(
        email=payload.email.lower(),
        full_name=payload.full_name,
        hashed_password=hash_password(payload.password),
        role=payload.role,
    )
    db.add(user)
    await db.flush()
    await record_audit(
        db,
        action="user.created",
        entity_type="user",
        entity_id=user.id,
        actor_id=admin.id,
        after={"email": user.email, "role": user.role.value},
    )
    await db.commit()
    await db.refresh(user)
    return user


@router.get("/users", response_model=list[UserRead], summary="List users (admin)")
async def list_users(db: DbSession, _admin: AdminUser) -> list[User]:
    return list((await db.scalars(select(User).order_by(User.created_at.desc()))).all())


@router.patch("/users/{user_id}", response_model=UserRead, summary="Update a user (admin)")
async def update_user(db: DbSession, admin: AdminUser, user_id: str, payload: UserUpdate) -> User:
    user = await db.get(User, user_id)
    if user is None:
        raise NotFoundError("User not found.")
    changes = payload.model_dump(exclude_unset=True)
    for field, value in changes.items():
        setattr(user, field, value)
    await record_audit(
        db,
        action="user.updated",
        entity_type="user",
        entity_id=user.id,
        actor_id=admin.id,
        after={k: str(v) for k, v in changes.items()},
    )
    await db.commit()
    await db.refresh(user)
    return user


@router.get("/roles", response_model=list[str], summary="Available roles")
async def roles() -> list[str]:
    return [r.value for r in UserRole]
