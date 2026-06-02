"""Proxy health tracking — circuit breaker, dead-proxy eviction, active probing.

State lives in Redis and every operation is best-effort: when Redis is
unavailable, recording is a no-op and ``filter_healthy`` returns the pool
unchanged, so the crawler path never breaks because of health bookkeeping.

Model
-----
- Each proxy is keyed by a short hash of its string.
- ``proxy:health:fail:<hash>`` — a sliding-window failure counter (TTL =
  ``window_seconds``). Reaching ``fail_threshold`` benches the proxy.
- ``proxy:health:bench:<hash>`` — a bench flag with TTL ``bench_seconds``.
  Benched proxies are skipped during selection (the circuit breaker).
- A proxy whose failure counter reaches ``dead_threshold`` is considered dead
  and is eligible for eviction from the pool.

Config (``proxy.health`` in settings.yml, all optional):
    enabled, fail_threshold, bench_seconds, dead_threshold, window_seconds,
    probe_url, probe_timeout
"""
from __future__ import annotations

import asyncio
import hashlib

from loguru import logger

from config import settings

_FAIL_PREFIX = "proxy:health:fail:"
_BENCH_PREFIX = "proxy:health:bench:"

_DEFAULTS = {
    "enabled": True,
    "fail_threshold": 3,
    "bench_seconds": 600,
    "dead_threshold": 10,
    "window_seconds": 1800,
    "probe_url": "https://api.ipify.org",
    "probe_timeout": 10,
}


class ProxyHealth:
    """Redis-backed proxy health bookkeeping. All methods degrade gracefully."""

    # ------------------------------------------------------------------
    # Config / keys
    # ------------------------------------------------------------------

    @staticmethod
    def _cfg() -> dict:
        raw = settings.get("proxy", {}).get("health", {}) or {}
        return {key: raw.get(key, default) for key, default in _DEFAULTS.items()}

    @classmethod
    def enabled(cls) -> bool:
        return bool(cls._cfg()["enabled"])

    @staticmethod
    def _key(proxy: str) -> str:
        return hashlib.sha1(proxy.encode("utf-8")).hexdigest()[:16]

    @staticmethod
    async def _client():
        from services.redis_client import get_redis_client  # noqa: PLC0415
        return get_redis_client()

    # ------------------------------------------------------------------
    # Record outcomes
    # ------------------------------------------------------------------

    @classmethod
    async def record_failure(cls, proxy: str) -> int:
        """Increment the failure counter; bench the proxy past the threshold.

        Returns the new failure count (0 when Redis is unavailable).
        """
        cfg = cls._cfg()
        try:
            client = await cls._client()
            fail_key = _FAIL_PREFIX + cls._key(proxy)
            count = int(await client.incr(fail_key))
            await client.expire(fail_key, int(cfg["window_seconds"]))
            if count >= int(cfg["fail_threshold"]):
                await client.set(
                    _BENCH_PREFIX + cls._key(proxy), "1", ex=int(cfg["bench_seconds"])
                )
                logger.info(f"Proxy benched after {count} failures: {cls._key(proxy)}")
            return count
        except Exception as exc:  # noqa: BLE001
            logger.debug(f"ProxyHealth.record_failure no-op: {exc}")
            return 0

    @classmethod
    async def record_success(cls, proxy: str) -> None:
        """Clear failure counter and bench flag — the proxy is healthy again."""
        try:
            client = await cls._client()
            await client.delete(_FAIL_PREFIX + cls._key(proxy))
            await client.delete(_BENCH_PREFIX + cls._key(proxy))
        except Exception as exc:  # noqa: BLE001
            logger.debug(f"ProxyHealth.record_success no-op: {exc}")

    # ------------------------------------------------------------------
    # Query
    # ------------------------------------------------------------------

    @classmethod
    async def is_benched(cls, proxy: str) -> bool:
        try:
            client = await cls._client()
            return bool(await client.exists(_BENCH_PREFIX + cls._key(proxy)))
        except Exception:  # noqa: BLE001
            return False

    @classmethod
    async def failures(cls, proxy: str) -> int:
        try:
            client = await cls._client()
            value = await client.get(_FAIL_PREFIX + cls._key(proxy))
            return int(value or 0)
        except Exception:  # noqa: BLE001
            return 0

    @classmethod
    async def filter_healthy(cls, proxies: list[str]) -> list[str]:
        """Drop benched proxies from the selection set (the circuit breaker).

        Never returns an empty list for a non-empty input: if every proxy is
        benched, the original pool is returned so the crawler is not stranded.
        """
        if not cls.enabled() or not proxies:
            return proxies
        try:
            healthy = [p for p in proxies if not await cls.is_benched(p)]
            return healthy or proxies
        except Exception:  # noqa: BLE001
            return proxies

    @classmethod
    async def health_map(cls, proxies: list[str]) -> list[dict]:
        """Return per-proxy health rows: index, failures, benched, dead, status."""
        dead_threshold = int(cls._cfg()["dead_threshold"])
        rows: list[dict] = []
        for index, proxy in enumerate(proxies):
            fails = await cls.failures(proxy)
            benched = await cls.is_benched(proxy)
            dead = fails >= dead_threshold
            status = "dead" if dead else ("benched" if benched else "healthy")
            rows.append(
                {"index": index, "failures": fails, "benched": benched, "dead": dead, "status": status}
            )
        return rows

    @classmethod
    async def evict_dead(cls, proxies: list[str]) -> tuple[list[str], list[str]]:
        """Split the pool into (survivors, evicted) by the dead threshold.

        Counters of evicted proxies are cleared so a re-added proxy starts fresh.
        """
        dead_threshold = int(cls._cfg()["dead_threshold"])
        survivors: list[str] = []
        evicted: list[str] = []
        for proxy in proxies:
            if await cls.failures(proxy) >= dead_threshold:
                evicted.append(proxy)
                await cls.record_success(proxy)  # reset its keys
            else:
                survivors.append(proxy)
        return survivors, evicted

    # ------------------------------------------------------------------
    # Active probing
    # ------------------------------------------------------------------

    @classmethod
    async def probe(cls, proxy: str, timeout: int | None = None) -> bool:
        """Actively test a proxy against the probe URL; record the outcome."""
        cfg = cls._cfg()
        probe_timeout = int(timeout or cfg["probe_timeout"])

        test = proxy
        if "://" not in test:
            test = f'{settings.get("proxy", {}).get("type", "http")}://{test}'
        if test.startswith("socks5://"):
            test = "socks5h://" + test[len("socks5://"):]

        ok = False
        try:
            from curl_cffi.requests import AsyncSession  # noqa: PLC0415
            async with AsyncSession() as session:
                resp = await session.get(str(cfg["probe_url"]), proxy=test, timeout=probe_timeout)
            ok = resp.status_code == 200
        except Exception as exc:  # noqa: BLE001
            logger.debug(f"Proxy probe failed for {cls._key(proxy)}: {exc}")
            ok = False

        if ok:
            await cls.record_success(proxy)
        else:
            await cls.record_failure(proxy)
        return ok

    @classmethod
    async def probe_all(cls, proxies: list[str], concurrency: int = 10) -> list[dict]:
        """Probe every proxy concurrently, then return the refreshed health map."""
        semaphore = asyncio.Semaphore(max(1, concurrency))

        async def _one(proxy: str) -> None:
            async with semaphore:
                await cls.probe(proxy)

        await asyncio.gather(*(_one(p) for p in proxies), return_exceptions=True)
        return await cls.health_map(proxies)
