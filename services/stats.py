"""StatsCollector — real-time parsing statistics backed by Redis.

All counters are atomic Redis operations so multiple worker processes share
the same statistics without conflicts.
"""
from __future__ import annotations

import time
from loguru import logger


_KEY_TOTAL_PARSED = "stats:total_parsed"
_KEY_TOTAL_FAILED = "stats:total_failed"
_KEY_START_TIME = "stats:start_time"
_KEY_PROCESSING_TIMES = "stats:processing_times"  # Redis list (ring buffer)
_KEY_MARKETPLACE_PREFIX = "stats:marketplace:"
_PROCESSING_TIMES_MAX = 1000  # ring-buffer size


class StatsCollector:
    """Collect and expose parsing statistics via Redis."""

    # ------------------------------------------------------------------
    # Write
    # ------------------------------------------------------------------

    @classmethod
    async def record_parse(
        cls,
        marketplace: str,
        success: bool,
        processing_time: float,
    ) -> None:
        """Increment counters for one completed parse attempt.

        Parameters
        ----------
        marketplace:
            Marketplace name (e.g. ``"ozon"``).
        success:
            ``True`` when parsing succeeded, ``False`` otherwise.
        processing_time:
            Wall-clock seconds for the attempt.
        """
        try:
            from services.redis_client import get_redis_client  # noqa: PLC0415
            client = get_redis_client()

            if success:
                await client.incr(_KEY_TOTAL_PARSED)
            else:
                await client.incr(_KEY_TOTAL_FAILED)

            # Per-marketplace counter (success only)
            if success:
                await client.incr(f"{_KEY_MARKETPLACE_PREFIX}{marketplace}")

            # Rolling processing-time list
            await client.lpush(_KEY_PROCESSING_TIMES, str(processing_time))  # type: ignore[misc]
            await client.ltrim(_KEY_PROCESSING_TIMES, 0, _PROCESSING_TIMES_MAX - 1)  # type: ignore[misc]

        except Exception as exc:  # noqa: BLE001
            logger.warning(f"StatsCollector.record_parse failed: {exc}")

    # ------------------------------------------------------------------
    # Read
    # ------------------------------------------------------------------

    @classmethod
    async def get_summary(cls) -> dict:
        """Return an aggregated statistics dict."""
        try:
            from services.redis_client import get_redis_client  # noqa: PLC0415
            client = get_redis_client()

            total_parsed = int(await client.get(_KEY_TOTAL_PARSED) or 0)  # type: ignore[misc]
            total_failed = int(await client.get(_KEY_TOTAL_FAILED) or 0)  # type: ignore[misc]

            times_raw: list[str] = await client.lrange(_KEY_PROCESSING_TIMES, 0, -1)  # type: ignore[misc]
            if times_raw:
                floats = [float(t) for t in times_raw]
                avg_processing_time = sum(floats) / len(floats)
            else:
                avg_processing_time = 0.0

            # Per-marketplace breakdown
            by_marketplace: dict[str, int] = {}
            keys = await client.keys(f"{_KEY_MARKETPLACE_PREFIX}*")
            for key in keys:
                name = key.replace(_KEY_MARKETPLACE_PREFIX, "")
                by_marketplace[name] = int(await client.get(key) or 0)

            return {
                "total_parsed": total_parsed,
                "total_failed": total_failed,
                "avg_processing_time": round(avg_processing_time, 4),
                "by_marketplace": by_marketplace,
            }

        except Exception as exc:  # noqa: BLE001
            logger.warning(f"StatsCollector.get_summary failed: {exc}")
            return {
                "total_parsed": 0,
                "total_failed": 0,
                "avg_processing_time": 0.0,
                "by_marketplace": {},
            }

    @classmethod
    async def get_uptime(cls) -> float:
        """Return seconds since the service start key was set in Redis.

        Falls back to ``0.0`` when the key is absent (e.g. first startup).
        """
        try:
            from services.redis_client import get_redis_client  # noqa: PLC0415
            client = get_redis_client()

            start_ts_raw: str | None = await client.get(_KEY_START_TIME)
            if start_ts_raw is None:
                return 0.0
            return round(time.time() - float(start_ts_raw), 2)

        except Exception as exc:  # noqa: BLE001
            logger.warning(f"StatsCollector.get_uptime failed: {exc}")
            return 0.0

    @classmethod
    async def set_start_time(cls) -> None:
        """Record the current epoch timestamp as the service start time."""
        try:
            from services.redis_client import get_redis_client  # noqa: PLC0415
            client = get_redis_client()
            await client.set(_KEY_START_TIME, str(time.time()))
        except Exception as exc:  # noqa: BLE001
            logger.warning(f"StatsCollector.set_start_time failed: {exc}")

    @classmethod
    async def reset(cls) -> None:
        """Delete all statistics keys from Redis."""
        try:
            from services.redis_client import get_redis_client  # noqa: PLC0415
            client = get_redis_client()

            keys_to_delete = [
                _KEY_TOTAL_PARSED,
                _KEY_TOTAL_FAILED,
                _KEY_PROCESSING_TIMES,
            ]

            marketplace_keys = await client.keys(f"{_KEY_MARKETPLACE_PREFIX}*")
            keys_to_delete.extend(marketplace_keys)

            if keys_to_delete:
                await client.delete(*keys_to_delete)

            logger.info("Statistics reset")
        except Exception as exc:  # noqa: BLE001
            logger.warning(f"StatsCollector.reset failed: {exc}")
