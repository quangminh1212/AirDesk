"""User administration endpoints (admin-only CRUD + roles).

Hermetic: runs against the in-process SQLite test DB with the stub LLM
gateway. Exercises the whole users contract the frontend depends on:

  * POST /auth/users        -> 201, hashes password, rejects dup email (409)
  * GET  /auth/users        -> list, admin only
  * PATCH /auth/users/{id}  -> update role/active/name
  * GET  /auth/roles        -> string[] of roles
  * role guard: non-admin (AGENT) gets 403 on admin endpoints
"""

from __future__ import annotations

import pytest
from httpx import AsyncClient

from app.core.security import verify_password
from app.models.user import User, UserRole
from tests.conftest import db_session  # noqa: F401  (fixture import)


@pytest.mark.asyncio
async def test_roles_endpoint(client: AsyncClient) -> None:
    r = await client.get("/api/v1/auth/roles")
    assert r.status_code == 200, r.text
    roles = r.json()
    assert set(roles) >= {"viewer", "agent", "operator", "admin"}


@pytest.mark.asyncio
async def test_create_user_success(client: AsyncClient, auth_headers: dict, db_session) -> None:
    r = await client.post(
        "/api/v1/auth/users",
        headers=auth_headers,
        json={
            "email": "agent.new@airdesk.vn",
            "password": "secret123",
            "full_name": "New Agent",
            "role": "agent",
        },
    )
    assert r.status_code == 201, r.text
    body = r.json()
    assert body["email"] == "agent.new@airdesk.vn"
    assert body["role"] == "agent"
    assert body["is_active"] is True
    assert "password" not in body

    # password is actually hashed, not stored in plaintext
    created = await db_session.get(User, body["id"])
    assert created is not None
    assert verify_password("secret123", created.hashed_password)
    assert not verify_password("wrong", created.hashed_password)


@pytest.mark.asyncio
async def test_create_user_rejects_duplicate_email(
    client: AsyncClient, auth_headers: dict, db_session
) -> None:
    await client.post(
        "/api/v1/auth/users",
        headers=auth_headers,
        json={
            "email": "dup@airdesk.vn",
            "password": "secret123",
            "role": "viewer",
        },
    )
    r2 = await client.post(
        "/api/v1/auth/users",
        headers=auth_headers,
        json={
            "email": "dup@airdesk.vn",
            "password": "secret123",
            "role": "viewer",
        },
    )
    assert r2.status_code == 409, r2.text
    assert "already exists" in r2.json()["error"]["message"]


@pytest.mark.asyncio
async def test_create_user_weak_password_rejected(client: AsyncClient, auth_headers: dict) -> None:
    r = await client.post(
        "/api/v1/auth/users",
        headers=auth_headers,
        json={"email": "weak@airdesk.vn", "password": "allletters", "role": "VIEWER"},
    )
    assert r.status_code == 422, r.text


@pytest.mark.asyncio
async def test_list_users(client: AsyncClient, auth_headers: dict) -> None:
    r = await client.get("/api/v1/auth/users", headers=auth_headers)
    assert r.status_code == 200, r.text
    users = r.json()
    assert isinstance(users, list) and users
    assert any(u["role"] == "admin" for u in users)


@pytest.mark.asyncio
async def test_update_user(client: AsyncClient, auth_headers: dict, db_session) -> None:
    created = await client.post(
        "/api/v1/auth/users",
        headers=auth_headers,
        json={"email": "promote@airdesk.vn", "password": "secret123", "role": "viewer"},
    )
    uid = created.json()["id"]

    r = await client.patch(
        f"/api/v1/auth/users/{uid}",
        headers=auth_headers,
        json={"role": "operator", "is_active": False, "full_name": "Promoted"},
    )
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["role"] == "operator"
    assert body["is_active"] is False
    assert body["full_name"] == "Promoted"


@pytest.mark.asyncio
async def test_update_missing_user_404(client: AsyncClient, auth_headers: dict) -> None:
    r = await client.patch(
        "/api/v1/auth/users/does-not-exist",
        headers=auth_headers,
        json={"role": "agent"},
    )
    assert r.status_code == 404, r.text


@pytest.mark.asyncio
async def test_admin_endpoints_forbid_non_admin(client: AsyncClient, db_session) -> None:
    # create an AGENT (non-admin) user directly in the DB
    agent = User(
        email="agent@airdesk.vn",
        full_name="Plain Agent",
        hashed_password=(await _hash("secret123")),
        role=UserRole.AGENT,
        is_active=True,
    )
    db_session.add(agent)
    await db_session.commit()
    await db_session.refresh(agent)

    login = await client.post(
        "/api/v1/auth/login",
        json={"email": "agent@airdesk.vn", "password": "secret123"},
    )
    assert login.status_code == 200, login.text
    agent_headers = {"Authorization": f"Bearer {login.json()['access_token']}"}

    # list users -> forbidden
    r = await client.get("/api/v1/auth/users", headers=agent_headers)
    assert r.status_code == 403, r.text

    # create user -> forbidden
    r = await client.post(
        "/api/v1/auth/users",
        headers=agent_headers,
        json={"email": "x@airdesk.vn", "password": "secret123"},
    )
    assert r.status_code == 403, r.text

    # roles is public (no auth needed)
    r = await client.get("/api/v1/auth/roles")
    assert r.status_code == 200


async def _hash(pw: str) -> str:
    from app.core.security import hash_password

    return hash_password(pw)
