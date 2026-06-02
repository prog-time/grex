"""Redis-backed image download queue and background worker.

Architecture
------------
* Producer  — ``enqueue_image_task()`` pushes a JSON task via ``LPUSH``.
* Consumer  — ``image_worker()`` loops on ``BRPOP`` (blocking pop, 5-second
  timeout) and calls :func:`~services.image_downloader.download_images` for
  each task.

Queue key:  ``grex:image_queue``

Task schema (JSON):
    {
        "marketplace": "bigam",
        "sku":         "О00000180421",
        "images":      ["https://...", ...]
    }

The worker is designed to run as a single long-lived asyncio Task started
during FastAPI lifespan.  It handles ``asyncio.CancelledError`` gracefully so
shutdown is clean.
"""
from __future__ import annotations

import asyncio
import json

from loguru import logger

QUEUE_KEY = "grex:image_queue"


async def enqueue_image_task(
    marketplace: str,
    sku: str,
    images: list[str],
) -> None:
    """Push an image-download task onto the Redis queue.

    Silently skips when Redis is unavailable or the image list is empty,
    so the calling code never needs to handle queue errors.
    """
    if not images or not marketplace or not sku:
        return

    try:
        from services.redis_client import get_redis_client  # noqa: PLC0415

        client = get_redis_client()
        payload = json.dumps(
            {"marketplace": marketplace, "sku": sku, "images": images},
            ensure_ascii=False,
        )
        await client.lpush(QUEUE_KEY, payload)
        logger.debug(
            f"Image task enqueued: {marketplace}/{sku} — {len(images)} image(s)"
        )
    except Exception as exc:  # noqa: BLE001
        logger.warning(f"enqueue_image_task failed (task dropped): {exc}")


async def image_worker() -> None:
    """Background worker that drains the image-download queue.

    Runs indefinitely until cancelled (e.g. on application shutdown).
    Uses exponential backoff (up to 60 s) when Redis is unavailable.
    """
    from services.image_downloader import download_images  # noqa: PLC0415

    logger.info("Image download worker started")

    _retry_delay = 1.0
    _MAX_DELAY = 60.0

    while True:
        try:
            from services.redis_client import get_redis_client  # noqa: PLC0415

            client = get_redis_client()
            # Non-blocking RPOP + poll. We deliberately avoid BRPOP here: the
            # shared client is configured with a small socket_timeout (0.3s) so
            # the rest of the app fails fast, but a blocking BRPOP would exceed
            # that read timeout on an empty queue and be misreported as
            # "Redis unavailable". RPOP returns immediately, so a fast poll
            # gives near-instant processing without the timeout conflict.
            raw = await client.rpop(QUEUE_KEY)

            # Successful Redis contact — reset backoff.
            _retry_delay = 1.0

            if raw is None:
                # Queue empty — wait briefly, then poll again.
                await asyncio.sleep(1.0)
                continue

            task: dict = json.loads(raw)
            marketplace: str = task.get("marketplace", "unknown")
            sku: str = task.get("sku", "unknown")
            images: list[str] = task.get("images", [])

            if not images:
                continue

            logger.info(
                f"Image worker: processing {marketplace}/{sku} "
                f"({len(images)} image(s))"
            )
            await download_images(marketplace, sku, images)

        except asyncio.CancelledError:
            logger.info("Image download worker stopped")
            break

        except Exception as exc:  # noqa: BLE001
            logger.warning(f"Image worker: Redis unavailable ({exc}), retry in {_retry_delay:.0f}s")
            await asyncio.sleep(_retry_delay)
            _retry_delay = min(_retry_delay * 2, _MAX_DELAY)
