"""API-level tests: health, auth, and the flight search pipeline."""

from __future__ import annotations

import pytest


@pytest.mark.asyncio
async def test_health(client) -> None:
    resp = await client.get("/api/v1/health")
    assert resp.status_code == 200
    body = resp.json()
    assert body.get("status") in {"ok", "healthy", "up"}


@pytest.mark.asyncio
async def test_login_and_me(client, admin_user) -> None:
    resp = await client.post(
        "/api/v1/auth/login",
        json={"email": admin_user.email, "password": "secret123"},
    )
    assert resp.status_code == 200, resp.text
    tokens = resp.json()
    assert "access_token" in tokens

    me = await client.get(
        "/api/v1/auth/me",
        headers={"Authorization": f"Bearer {tokens['access_token']}"},
    )
    assert me.status_code == 200
    assert me.json()["email"] == admin_user.email


@pytest.mark.asyncio
async def test_login_rejects_bad_password(client, admin_user) -> None:
    resp = await client.post(
        "/api/v1/auth/login",
        json={"email": admin_user.email, "password": "wrong-password"},
    )
    assert resp.status_code == 401


@pytest.mark.asyncio
async def test_search_requires_auth(client) -> None:
    resp = await client.post(
        "/api/v1/flights/search",
        json={
            "origin": "SGN",
            "destination": "HAN",
            "departure_date": "2026-05-01",
            "adults": 1,
        },
    )
    assert resp.status_code == 401


@pytest.mark.asyncio
async def test_mock_search_via_api(client, auth_headers) -> None:
    resp = await client.post(
        "/api/v1/flights/search",
        headers=auth_headers,
        json={
            "origin": "SGN",
            "destination": "HAN",
            "departure_date": "2026-05-01",
            "adults": 1,
            "provider": "mock",
            "max_results": 5,
        },
    )
    assert resp.status_code == 200, resp.text
    offers = resp.json()
    assert offers, "expected offers"
    first = offers[0]
    assert first["provider"] == "mock"
    assert first["segments"], "offer must contain segments"
    assert first["currency"] == "VND"


@pytest.mark.asyncio
async def test_providers_endpoint_lists_keyless(client, auth_headers) -> None:
    resp = await client.get("/api/v1/flights/providers", headers=auth_headers)
    assert resp.status_code == 200
    providers = resp.json()["providers"]
    assert "mock" in providers
    assert "opensky" in providers


@pytest.mark.asyncio
async def test_offer_cache_roundtrip(client, auth_headers) -> None:
    """Search caches offers; they can then be fetched by id."""
    resp = await client.post(
        "/api/v1/flights/search",
        headers=auth_headers,
        json={
            "origin": "SGN",
            "destination": "DAD",
            "departure_date": "2026-05-01",
            "adults": 1,
            "provider": "mock",
            "max_results": 3,
        },
    )
    assert resp.status_code == 200
    offers = resp.json()
    assert offers
    offer = offers[0]

    got = await client.get(
        f"/api/v1/flights/offers/{offer['provider']}/{offer['offer_id']}",
        headers=auth_headers,
    )
    assert got.status_code == 200, got.text
    assert got.json()["offer_id"] == offer["offer_id"]
