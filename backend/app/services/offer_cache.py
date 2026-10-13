"""Offer cache: persists provider offers so they can be re-priced and ordered.

Mirrors the NDC distributed Offer cache (see Condor NDC 21.3 "Caching +
Shopping Cart"): offers are stored by ID with a TTL so that subsequent calls
only need to reference IDs rather than re-send the whole payload. Two sections
are emulated here:

* **Shopping** — offers returned by ``search()`` (shopped, reference-only).
* **Offer**    — offers returned by ``price()`` (priced, bookable).

We also keep the ``shopped -> priced`` mapping so a priced offer can always be
traced back to what the traveller selected.

Backed by Redis when reachable, with an in-process TTL dict fallback so the app
still works with no Redis. Keyed by ``offer:{provider}:{offer_id}``.
"""

from __future__ import annotations

import json
import time
from typing import Any

from app.core.config import get_settings
from app.core.logging import get_logger

logger = get_logger(__name__)

# NDC default cache times: Shopping 20 min, Offer 30 min.
SHOPPED_TTL_SECONDS = 20 * 60
PRICED_TTL_SECONDS = 30 * 60


class _MemoryCache:
    """Tiny TTL dict used when Redis is not configured/reachable."""

    def __init__(self) -> None:
        self._store: dict[str, tuple[float, str]] = {}

    def get(self, key: str) -> str | None:
        item = self._store.get(key)
        if not item:
            return None
        expires, value = item
        if expires < time.time():
            self._store.pop(key, None)
            return None
        return value

    def set(self, key: str, value: str, ttl: int) -> None:
        self._store[key] = (time.time() + ttl, value)


_memory = _MemoryCache()
_redis: Any = None
_REDIS_PROBE_TIMEOUT = 0.4


async def _get_redis() -> Any:
    global _redis
    if _redis is not None:
        return _redis
    try:
        import redis.asyncio as aioredis

        client = aioredis.from_url(
            get_settings().redis_url,
            decode_responses=True,
            socket_connect_timeout=_REDIS_PROBE_TIMEOUT,
            socket_timeout=_REDIS_PROBE_TIMEOUT,
        )
        await client.ping()
        _redis = client
        return _redis
    except Exception as exc:  # noqa: BLE001 - Redis optional
        logger.info("redis_unavailable_using_memory_cache", error=str(exc))
        # Negative cache: remember Redis is down so we never re-probe per request.
        _redis = False
        return _redis


def _key(provider: str, offer_id: str) -> str:
    return f"offer:{provider}:{offer_id}"


async def _set(key: str, value: str, ttl: int) -> None:
    redis = await _get_redis()
    if redis:
        await redis.set(key, value, ex=ttl)
    else:
        _memory.set(key, value, ttl)


async def _get(key: str) -> str | None:
    redis = await _get_redis()
    return await redis.get(key) if redis else _memory.get(key)


async def cache_offer(
    provider: str,
    offer_id: str,
    payload: dict[str, Any],
    *,
    kind: str = "shopped",
) -> None:
    """Store an offer. ``kind`` selects the TTL (shopped vs priced)."""
    ttl = PRICED_TTL_SECONDS if kind == "priced" else SHOPPED_TTL_SECONDS
    await _set(_key(provider, offer_id), json.dumps(payload, default=str), ttl)


async def get_cached_offer(provider: str, offer_id: str) -> dict[str, Any] | None:
    blob = await _get(_key(provider, offer_id))
    if not blob:
        return None
    try:
        return json.loads(blob)
    except json.JSONDecodeError:
        return None


async def cache_priced_offer(
    provider: str,
    priced_offer_id: str,
    shopped_offer_id: str,
    payload: dict[str, Any],
) -> None:
    """Store a priced offer and remember which shopped offer it came from."""
    await cache_offer(provider, priced_offer_id, payload, kind="priced")
    await _set(
        f"offer-link:{provider}:{priced_offer_id}",
        shopped_offer_id,
        PRICED_TTL_SECONDS,
    )


async def get_shopped_offer_id(provider: str, priced_offer_id: str) -> str | None:
    """Return the shopped offer id a priced offer was derived from."""
    return await _get(f"offer-link:{provider}:{priced_offer_id}")
