"""Password hashing and JWT access/refresh token helpers.

Passwords are hashed with bcrypt directly (not via passlib): passlib 1.7.4 is
unmaintained and its backend-detection probe breaks against bcrypt >= 4.1
("password cannot be longer than 72 bytes"). Using the `bcrypt` package
directly avoids that incompatibility entirely.
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from typing import Any, Literal

import bcrypt
from jose import JWTError, jwt

from app.core.config import get_settings
from app.core.errors import AuthError

ALGORITHM = "HS256"
TokenType = Literal["access", "refresh"]

# bcrypt only uses the first 72 bytes of the password; truncate explicitly and
# consistently so long passwords do not raise.
_BCRYPT_MAX_BYTES = 72


def _prepare(password: str) -> bytes:
    return password.encode("utf-8")[:_BCRYPT_MAX_BYTES]


def hash_password(password: str) -> str:
    """Hash a plaintext password with bcrypt."""
    return bcrypt.hashpw(_prepare(password), bcrypt.gensalt()).decode("utf-8")


def verify_password(plain: str, hashed: str) -> bool:
    """Constant-time verify of a plaintext password against a hash."""
    try:
        return bcrypt.checkpw(_prepare(plain), hashed.encode("utf-8"))
    except (ValueError, TypeError):
        return False


def create_token(
    *,
    subject: str,
    token_type: TokenType = "access",
    scopes: list[str] | None = None,
    extra: dict[str, Any] | None = None,
) -> str:
    """Create a signed JWT for the given subject."""
    settings = get_settings()
    now = datetime.now(UTC)
    if token_type == "access":
        expires = now + timedelta(minutes=settings.access_token_expire_minutes)
    else:
        expires = now + timedelta(days=settings.refresh_token_expire_days)

    payload: dict[str, Any] = {
        "sub": subject,
        "type": token_type,
        "iat": int(now.timestamp()),
        "exp": int(expires.timestamp()),
        "scopes": scopes or [],
    }
    if extra:
        payload.update(extra)
    return jwt.encode(payload, settings.secret_key, algorithm=ALGORITHM)


def decode_token(token: str, *, expected_type: TokenType | None = None) -> dict[str, Any]:
    """Decode and validate a JWT, raising AuthError on any problem."""
    settings = get_settings()
    try:
        payload = jwt.decode(token, settings.secret_key, algorithms=[ALGORITHM])
    except JWTError as exc:  # expired, bad signature, malformed
        raise AuthError("Invalid or expired token.") from exc
    if expected_type and payload.get("type") != expected_type:
        raise AuthError(f"Expected a {expected_type} token.")
    return payload
