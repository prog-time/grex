"""Background scheduler that drives product monitoring.

Mirrors the image_worker pattern: a single long-lived asyncio task started in
the app lifespan, cancelled cleanly on shutdown, with exponential backoff on
unexpected errors. Each tick processes the targets that are due (see
services.monitoring.run_due_once) and occasionally purges old history.
"""
from __future__ import annotations

import asyncio

from loguru import logger

_MAX_BACKOFF = 60.0


async def monitor_scheduler() -> None:
    from services.monitoring import cfg, purge_old_snapshots, run_due_once  # noqa: PLC0415

    logger.info("Monitor scheduler started")
    backoff = 1.0
    ticks = 0

    while True:
        try:
            conf = cfg()
            tick_seconds = max(5, int(conf["tick_seconds"]))

            if conf["enabled"]:
                await run_due_once()
                ticks += 1
                # Roughly hourly: trim history beyond the retention window.
                purge_every = max(1, int(3600 / tick_seconds))
                if ticks % purge_every == 0:
                    purged = await purge_old_snapshots()
                    if purged:
                        logger.info(f"Purged {purged} price snapshots past retention")

            backoff = 1.0
            await asyncio.sleep(tick_seconds)

        except asyncio.CancelledError:
            logger.info("Monitor scheduler stopped")
            break
        except Exception as exc:  # noqa: BLE001
            logger.warning(f"Monitor scheduler error: {exc}; retry in {backoff:.0f}s")
            await asyncio.sleep(backoff)
            backoff = min(backoff * 2, _MAX_BACKOFF)
