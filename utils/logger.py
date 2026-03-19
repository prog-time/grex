from __future__ import annotations

import asyncio
import os
import sys
from loguru import logger

from config import settings


# ---------------------------------------------------------------------------
# Global async queue for SSE log streaming (TASK-12)
# ---------------------------------------------------------------------------
log_queue: asyncio.Queue[str] = asyncio.Queue(maxsize=500)


def sse_sink(message: "logger.Message") -> None:  # type: ignore[name-defined]
    """Loguru sink that feeds formatted log lines into *log_queue*.

    When the queue is full the oldest entry is evicted before inserting the
    new one so the producer never blocks.
    """
    formatted: str = message.strip()  # type: ignore[attr-defined]
    try:
        log_queue.put_nowait(formatted)
    except asyncio.QueueFull:
        # Evict the oldest entry, then insert the new one.
        try:
            log_queue.get_nowait()
        except asyncio.QueueEmpty:
            pass
        try:
            log_queue.put_nowait(formatted)
        except asyncio.QueueFull:
            pass  # Give up silently — the sink must never raise.


def setup_logger() -> None:
    logger.opt(depth=1)
    logger.remove()

    logger_format = (
        "<cyan>{time:HH:mm:ss}</cyan> | "
        f"<cyan>{settings['soft_version']}</cyan> | "
        "<level>{level: ^8}</level> | "
        "<cyan>{extra[info]}</cyan> | "
        "<level>{message}</level>"
    )

    if settings.get("debug", {}).get("active", False):
        log_level = "DEBUG"
    else:
        log_level = "INFO"

    def add_default_extra(record):  # type: ignore[no-untyped-def]
        if settings.get("is_demo", False):
            record["extra"]["info"] = "DEMO MODE"
        elif "info" not in record["extra"]:
            record["extra"]["info"] = "SYSTEM"
        return True

    logger.add(
        sys.stderr,
        format=logger_format,
        enqueue=True,
        level=log_level,
        filter=add_default_extra,
    )

    # SSE sink — raw text (no ANSI colours)
    plain_format = (
        "{time:HH:mm:ss} | {level: ^8} | {extra[info]} | {message}"
    )
    logger.add(
        sse_sink,
        format=plain_format,
        level=log_level,
        filter=add_default_extra,
        colorize=False,
    )

    if settings.get("debug", {}).get("logging_in_file"):
        path_pattern = os.path.join("logs", "{time:YYYY-MM-DD}.log")
        os.makedirs("logs", exist_ok=True)

        logger.add(
            path_pattern,
            rotation="00:00",
            retention="7 days",
            level=log_level,
            format=logger_format,
            enqueue=True,
            filter=add_default_extra,
        )
