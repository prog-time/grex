"""Tests for services/proxy_health.py — the proxy circuit breaker and eviction.

Redis is replaced with an in-memory fake so the health logic is exercised with
no external dependency. The graceful-degradation paths (Redis raising) are
asserted explicitly, since the crawler hot path relies on them never throwing.
"""
from __future__ import annotations

from unittest.mock import patch

from services.proxy_health import ProxyHealth

_HEALTH_CFG = {
    "proxy": {
        "type": "http",
        "active": True,
        "health": {
            "enabled": True,
            "fail_threshold": 3,
            "bench_seconds": 600,
            "dead_threshold": 5,
            "window_seconds": 1800,
        },
    }
}


class FakeRedis:
    """Minimal async Redis stand-in covering the calls ProxyHealth makes."""

    def __init__(self) -> None:
        self.store: dict[str, str] = {}

    async def incr(self, key: str) -> int:
        self.store[key] = str(int(self.store.get(key, "0")) + 1)
        return int(self.store[key])

    async def expire(self, key: str, ttl: int) -> bool:
        return key in self.store

    async def set(self, key: str, value, ex=None) -> None:
        self.store[key] = str(value)

    async def get(self, key: str):
        return self.store.get(key)

    async def delete(self, *keys: str) -> None:
        for key in keys:
            self.store.pop(key, None)

    async def exists(self, key: str) -> int:
        return 1 if key in self.store else 0


def _with_fake(fake: FakeRedis):
    return patch("services.redis_client.get_redis_client", return_value=fake)


def _with_cfg():
    return patch.dict("config.settings", _HEALTH_CFG, clear=False)


# ---------------------------------------------------------------------------
# record_failure / bench / record_success
# ---------------------------------------------------------------------------

class TestRecordAndBench:
    async def test_failures_accumulate(self):
        fake = FakeRedis()
        with _with_cfg(), _with_fake(fake):
            assert await ProxyHealth.record_failure("http://p1:1") == 1
            assert await ProxyHealth.record_failure("http://p1:1") == 2
            assert await ProxyHealth.failures("http://p1:1") == 2

    async def test_benched_after_threshold(self):
        fake = FakeRedis()
        with _with_cfg(), _with_fake(fake):
            for _ in range(3):  # fail_threshold = 3
                await ProxyHealth.record_failure("http://p1:1")
            assert await ProxyHealth.is_benched("http://p1:1") is True

    async def test_not_benched_below_threshold(self):
        fake = FakeRedis()
        with _with_cfg(), _with_fake(fake):
            await ProxyHealth.record_failure("http://p1:1")
            await ProxyHealth.record_failure("http://p1:1")
            assert await ProxyHealth.is_benched("http://p1:1") is False

    async def test_success_clears_failures_and_bench(self):
        fake = FakeRedis()
        with _with_cfg(), _with_fake(fake):
            for _ in range(3):
                await ProxyHealth.record_failure("http://p1:1")
            await ProxyHealth.record_success("http://p1:1")
            assert await ProxyHealth.failures("http://p1:1") == 0
            assert await ProxyHealth.is_benched("http://p1:1") is False


# ---------------------------------------------------------------------------
# filter_healthy — the circuit breaker
# ---------------------------------------------------------------------------

class TestFilterHealthy:
    async def test_drops_benched_proxy(self):
        fake = FakeRedis()
        with _with_cfg(), _with_fake(fake):
            for _ in range(3):
                await ProxyHealth.record_failure("http://bad:1")
            healthy = await ProxyHealth.filter_healthy(["http://bad:1", "http://good:2"])
            assert healthy == ["http://good:2"]

    async def test_never_strands_when_all_benched(self):
        fake = FakeRedis()
        pool = ["http://a:1", "http://b:2"]
        with _with_cfg(), _with_fake(fake):
            for p in pool:
                for _ in range(3):
                    await ProxyHealth.record_failure(p)
            # Every proxy is benched → the original pool is returned, not [].
            assert await ProxyHealth.filter_healthy(pool) == pool

    async def test_disabled_returns_pool_unchanged(self):
        fake = FakeRedis()
        disabled = {"proxy": {"health": {"enabled": False}}}
        with patch.dict("config.settings", disabled, clear=False), _with_fake(fake):
            for _ in range(5):
                await ProxyHealth.record_failure("http://x:1")
            assert await ProxyHealth.filter_healthy(["http://x:1"]) == ["http://x:1"]


# ---------------------------------------------------------------------------
# health_map / evict_dead
# ---------------------------------------------------------------------------

class TestHealthMapAndEvict:
    async def test_health_map_statuses(self):
        fake = FakeRedis()
        pool = ["http://healthy:1", "http://benched:2", "http://dead:3"]
        with _with_cfg(), _with_fake(fake):
            for _ in range(3):  # benched (>= fail_threshold, < dead_threshold)
                await ProxyHealth.record_failure("http://benched:2")
            for _ in range(5):  # dead (>= dead_threshold = 5)
                await ProxyHealth.record_failure("http://dead:3")
            rows = await ProxyHealth.health_map(pool)
        by_proxy = {r["index"]: r["status"] for r in rows}
        assert by_proxy[0] == "healthy"
        assert by_proxy[1] == "benched"
        assert by_proxy[2] == "dead"

    async def test_evict_dead_splits_pool(self):
        fake = FakeRedis()
        pool = ["http://keep:1", "http://dead:2"]
        with _with_cfg(), _with_fake(fake):
            for _ in range(5):  # dead_threshold = 5
                await ProxyHealth.record_failure("http://dead:2")
            survivors, evicted = await ProxyHealth.evict_dead(pool)
        assert survivors == ["http://keep:1"]
        assert evicted == ["http://dead:2"]


# ---------------------------------------------------------------------------
# Graceful degradation — Redis unavailable must never raise
# ---------------------------------------------------------------------------

class TestGracefulDegradation:
    async def test_no_redis_record_failure_returns_zero(self):
        with _with_cfg(), patch("services.redis_client.get_redis_client", side_effect=RuntimeError("down")):
            assert await ProxyHealth.record_failure("http://p:1") == 0

    async def test_no_redis_filter_returns_input(self):
        pool = ["http://p:1", "http://p:2"]
        with _with_cfg(), patch("services.redis_client.get_redis_client", side_effect=RuntimeError("down")):
            assert await ProxyHealth.filter_healthy(pool) == pool

    async def test_no_redis_is_benched_false(self):
        with _with_cfg(), patch("services.redis_client.get_redis_client", side_effect=RuntimeError("down")):
            assert await ProxyHealth.is_benched("http://p:1") is False
