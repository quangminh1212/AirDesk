"""Shared pytest fixtures.

Tests run against an in-process SQLite database and the ASGI app directly, so
the whole suite is hermetic — no Postgres, no Redis, no network required.

The flight-provider tests are split in two:

* ``mock`` provider tests are fully offline (deterministic).
* ``opensky`` unit tests exercise parsing/pricing *without* hitting the network
  (the live-fetch path is stubbed), so CI never depends on OpenSky uptime.
"""

from __future__ import annotations

import os
import socket

os.environ.setdefault("DATABASE_URL", "sqlite+aiosqlite:////tmp/airdesk_test.db")
os.environ.setdefault("SECRET_KEY", "test-secret-not-for-production")
os.environ.setdefault("FLIGHT_PROVIDERS", "mock,opensky")

import pytest
import pytest_asyncio
from asgi_lifespan import LifespanManager
from httpx import ASGITransport, AsyncClient
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

import app.models  # noqa: F401  (register all tables on Base.metadata)
from app.core import llm as llm_module
from app.core.security import hash_password
from app.db.base import Base
from app.db.session import get_db
from app.main import app
from app.models.user import User, UserRole

TEST_DB_URL = "sqlite+aiosqlite:////tmp/airdesk_test.db"


# --- hermeticity guards ----------------------------------------------------
# Autouse, so they apply to every test with no opt-in. A test that reaches a
# live provider is a bug: it bills a real account and makes the suite depend on
# the network. These make that failure loud and immediate instead of silent.


@pytest.fixture(autouse=True)
def _block_outbound_llm(monkeypatch):
    """Refuse to construct a real LLM gateway during tests.

    Tests must inject a stub: pass ``gateway=...`` explicitly, monkeypatch the
    module-level ``get_llm_gateway`` used by the code under test, or let the
    default ASGI override installed by ``client`` supply one.
    """

    def _explode(*_args, **_kwargs):  # noqa: ANN002, ANN003
        raise AssertionError(
            "A test tried to create a real LiteLLMGateway, which would make a "
            "billed network call. Inject a stub gateway instead (pass "
            "`gateway=...` or monkeypatch `get_llm_gateway`)."
        )

    monkeypatch.setattr(llm_module.LiteLLMGateway, "__init__", _explode)
    yield


@pytest.fixture(autouse=True)
def _block_outbound_network(monkeypatch):
    """Refuse outbound socket connections during tests (loopback allowed)."""
    real_connect = socket.socket.connect

    def _guarded_connect(self, address, *args, **kwargs):  # noqa: ANN001, ANN002, ANN003
        host = address[0] if isinstance(address, tuple) and address else ""
        if host not in ("127.0.0.1", "::1", "localhost", ""):
            raise AssertionError(
                f"Test attempted a real outbound connection to {host!r}. "
                "The test suite must run fully offline."
            )
        return real_connect(self, address, *args, **kwargs)

    monkeypatch.setattr(socket.socket, "connect", _guarded_connect)
    yield


@pytest_asyncio.fixture(scope="function")
async def engine():
    eng = create_async_engine(TEST_DB_URL, future=True)
    async with eng.begin() as conn:
        await conn.run_sync(Base.metadata.drop_all)
        await conn.run_sync(Base.metadata.create_all)
    yield eng
    await eng.dispose()


@pytest_asyncio.fixture(scope="function")
async def db_session(engine):
    maker = async_sessionmaker(engine, expire_on_commit=False, class_=AsyncSession)
    async with maker() as session:
        yield session


@pytest_asyncio.fixture(scope="function")
async def admin_user(db_session) -> User:
    user = User(
        email="admin@airdesk.vn",
        full_name="Test Admin",
        hashed_password=hash_password("secret123"),
        role=UserRole.ADMIN,
        is_active=True,
    )
    db_session.add(user)
    await db_session.commit()
    await db_session.refresh(user)
    return user


@pytest.fixture(scope="session")
def stub_llm_gateway():
    """A deterministic, offline stand-in for the LiteLLM gateway.

    Returns a canned assistant message so any endpoint that *declares* the
    ``Gateway`` dependency resolves without touching the network. Tests that
    care about LLM behaviour should override this with their own stub.
    """
    from app.core.llm import LLMResult

    class _StubGateway:
        """Minimal duck-typed gateway: same surface, no I/O."""

        def __init__(self) -> None:
            self.calls: list[dict] = []

        async def complete(self, **kwargs):  # noqa: ANN003
            self.calls.append(kwargs)
            return LLMResult(
                content="Xin chào, tôi có thể hỗ trợ gì cho bạn?",
                model="stub-model",
                prompt_tokens=1,
                completion_tokens=1,
                cost_usd=0.0,
            )

    return _StubGateway()


@pytest_asyncio.fixture(scope="function")
async def client(engine, db_session, stub_llm_gateway):
    """ASGI client wired to the test DB, with lifespan events run.

    The LLM gateway dependency is overridden with an offline stub so no test
    can accidentally bill a real provider.
    """
    from app.api import deps

    maker = async_sessionmaker(engine, expire_on_commit=False, class_=AsyncSession)

    async def _override_get_db():
        async with maker() as session:
            yield session

    app.dependency_overrides[get_db] = _override_get_db
    app.dependency_overrides[deps.get_agent_gateway] = lambda: stub_llm_gateway
    async with LifespanManager(app):
        transport = ASGITransport(app=app)
        async with AsyncClient(transport=transport, base_url="http://test") as c:
            yield c
    app.dependency_overrides.clear()


@pytest_asyncio.fixture(scope="function")
async def auth_headers(client, admin_user) -> dict[str, str]:
    resp = await client.post(
        "/api/v1/auth/login",
        json={"email": admin_user.email, "password": "secret123"},
    )
    assert resp.status_code == 200, resp.text
    token = resp.json()["access_token"]
    return {"Authorization": f"Bearer {token}"}
