"""Redis client singleton and helper utilities.

Initialisation happens in the FastAPI lifespan.  All other modules
obtain a connection through :func:`get_redis_client`.
"""
from __future__ import annotations

import json
from typing import Any

import redis.asyncio as aioredis
from loguru import logger

from config import settings

_redis_client: aioredis.Redis | None = None


async def init_redis() -> None:
    """Create and cache the global Redis connection.

    Call once during application startup (FastAPI lifespan).
    """
    global _redis_client  # noqa: PLW0603

    redis_cfg = settings.get("redis", {})
    host: str = redis_cfg.get("host", "localhost")
    port: int = int(redis_cfg.get("port", 6379))
    db: int = int(redis_cfg.get("db", 0))
    password: str | None = redis_cfg.get("password") or None

    _redis_client = aioredis.Redis(
        host=host,
        port=port,
        db=db,
        password=password,
        decode_responses=True,
    )
    logger.info(f"Redis client initialised: {host}:{port}/{db}")


async def close_redis() -> None:
    """Close the global Redis connection gracefully."""
    global _redis_client  # noqa: PLW0603
    if _redis_client is not None:
        await _redis_client.aclose()
        _redis_client = None
        logger.info("Redis connection closed")


def get_redis_client() -> aioredis.Redis:
    """Return the active Redis client.

    Raises :class:`RuntimeError` when called before :func:`init_redis`.
    """
    if _redis_client is None:
        raise RuntimeError(
            "Redis client has not been initialised. "
            "Call await init_redis() during application startup."
        )
    return _redis_client


# ---------------------------------------------------------------------------
# Helper functions
# ---------------------------------------------------------------------------


async def set_json(key: str, value: Any, ttl: int | None = None) -> None:
    """Serialise *value* to JSON and store under *key*, with optional TTL (seconds)."""
    client = get_redis_client()
    encoded = json.dumps(value, ensure_ascii=False)
    if ttl is not None:
        await client.setex(key, ttl, encoded)
    else:
        await client.set(key, encoded)


async def get_json(key: str) -> Any | None:
    """Return the JSON-decoded value for *key*, or ``None`` when absent."""
    client = get_redis_client()
    raw = await client.get(key)
    if raw is None:
        return None
    return json.loads(raw)


async def delete(key: str) -> None:
    """Delete *key* from Redis."""
    client = get_redis_client()
    await client.delete(key)


async def ping() -> bool:
    """Return ``True`` when Redis responds to PING."""
    try:
        client = get_redis_client()
        result = await client.ping()  # type: ignore[misc]
        return bool(result)
    except Exception as exc:  # noqa: BLE001
        logger.warning(f"Redis ping failed: {exc}")
        return False
