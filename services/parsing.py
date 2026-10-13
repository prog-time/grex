from __future__ import annotations

import asyncio
import time
import traceback
from collections.abc import Awaitable, Callable
from datetime import datetime, timezone

from fastapi import HTTPException, status
from loguru import logger

# Import the crawlers package (not just the registry) so that all concrete
# crawler classes are loaded and auto-registered via __init_subclass__.
import crawlers as _crawlers_pkg  # noqa: F401
from config import settings
from core.exceptions import CrawlerNotFoundError, ProductCardParsingException, UnsupportedMarketplaceError
from crawlers.registry import CrawlerRegistry
from models import BatchItemResult, ProductCard


class ParsingService:
    # Class-level state for rate limiting (shared across instances).
    _rate_limit_locks: dict[str, asyncio.Lock] = {}
    _last_parse_time: dict[str, float] = {}

    def __init__(self) -> None:
        self.start_time = datetime.now()

        # Build cookie-lock dict dynamically from crawlers that declare
        # requires_cookie_lock = True.
        self._cookie_locks: dict[str, asyncio.Lock] = {
            name: asyncio.Lock()
            for name, cls in CrawlerRegistry.all().items()
            if getattr(cls, "requires_cookie_lock", False)
        }

    async def parse_product(self, url: str) -> ProductCard:
        t_start = time.monotonic()
        started_at = datetime.now(timezone.utc)
        marketplace: str | None = None
        try:
            marketplace = CrawlerRegistry.detect_marketplace(url)

            # Enforce per-source rate limit from config.
            await self._apply_rate_limit(marketplace)

            crawler_cls = CrawlerRegistry.get(marketplace)
            crawler = crawler_cls()
            # Normalize the URL (strip fragment/query/tab segments per crawler)
            # so variant links collapse to one canonical URL and don't dupe.
            url = crawler.normalize_url(url)
            product_card = await crawler.parse(url)

            finished_at = datetime.now(timezone.utc)
            elapsed = time.monotonic() - t_start
            await self._record_stat(marketplace, success=True, elapsed=elapsed)
            await self._save_job(url, marketplace, started_at, finished_at, success=True)

            # Enqueue image download as a separate background task (fire-and-forget).
            if product_card.images:
                from services.image_queue import enqueue_image_task  # noqa: PLC0415
                await enqueue_image_task(marketplace, product_card.sku, product_card.images)

            return product_card

        except (UnsupportedMarketplaceError, CrawlerNotFoundError) as e:
            logger.error(f"URL validation error: {e}")
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail=str(e),
            )

        except HTTPException as e:
            finished_at = datetime.now(timezone.utc)
            elapsed = time.monotonic() - t_start
            if marketplace:
                await self._record_stat(marketplace, success=False, elapsed=elapsed)
                await self._save_job(
                    url, marketplace, started_at, finished_at,
                    success=False, error=str(e.detail),
                )
            raise

        except ProductCardParsingException as e:
            finished_at = datetime.now(timezone.utc)
            elapsed = time.monotonic() - t_start
            if marketplace:
                await self._record_stat(marketplace, success=False, elapsed=elapsed)
                await self._save_job(
                    url, marketplace, started_at, finished_at,
                    success=False, error=str(e),
                )
            logger.warning(f"Parsing failed for {url}: {e}")
            raise HTTPException(
                status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
                detail=str(e),
            )

        except Exception as e:
            finished_at = datetime.now(timezone.utc)
            elapsed = time.monotonic() - t_start
            if marketplace:
                await self._record_stat(marketplace, success=False, elapsed=elapsed)
                await self._save_job(
                    url, marketplace, started_at, finished_at,
                    success=False, error=str(e),
                )
            logger.error(f"Parsing error for {url}: {e}")
            traceback.print_exc()
            raise HTTPException(
                status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
                detail=f"Failed to parse product: {str(e)}",
            )

    async def parse_products_batch(
        self,
        urls: list[str],
        concurrency: int | None = None,
        on_result: Callable[[int, BatchItemResult], Awaitable[None]] | None = None,
    ) -> list[BatchItemResult]:
        """Parse *urls* concurrently; results keep the order of *urls*.

        *on_result*, if given, is awaited with ``(index, result)`` as soon as
        each URL finishes — used by async API jobs to persist progress.
        """
        # Resolve effective concurrency: request value, else batch.concurrency
        # from settings; clamped to batch.max_concurrency.
        batch_cfg = settings.get("batch", {})
        default_conc = int(batch_cfg.get("concurrency", 10))
        max_conc = int(batch_cfg.get("max_concurrency", 50))
        effective = concurrency if concurrency else default_conc
        effective = max(1, min(effective, max_conc))
        logger.debug(f"Batch parse: concurrency={effective} (cap {max_conc})")
        semaphore = asyncio.Semaphore(effective)

        async def parse_one(index: int, url: str) -> BatchItemResult:
            result = await parse_item(url)
            if on_result is not None:
                await on_result(index, result)
            return result

        async def parse_item(url: str) -> BatchItemResult:
            async with semaphore:
                item_start = datetime.now()
                try:
                    marketplace: str | None = CrawlerRegistry.detect_marketplace(url)
                except Exception:
                    marketplace = None

                try:
                    if marketplace and marketplace in self._cookie_locks:
                        async with self._cookie_locks[marketplace]:
                            product_card = await self.parse_product(url)
                    else:
                        product_card = await self.parse_product(url)

                    processing_time = (datetime.now() - item_start).total_seconds()
                    return BatchItemResult(
                        url=url,
                        success=True,
                        data=product_card.to_dict(),
                        marketplace=marketplace,
                        processing_time=processing_time,
                    )

                except Exception as e:
                    processing_time = (datetime.now() - item_start).total_seconds()
                    logger.error(f"Batch parse error for {url}: {e}")
                    return BatchItemResult(
                        url=url,
                        success=False,
                        error=str(e),
                        marketplace=marketplace,
                        processing_time=processing_time,
                    )

        tasks = [parse_one(i, url) for i, url in enumerate(urls)]
        return await asyncio.gather(*tasks)

    def get_uptime(self) -> float:
        return (datetime.now() - self.start_time).total_seconds()

    # ------------------------------------------------------------------
    # Internal helpers
    # ------------------------------------------------------------------

    @staticmethod
    async def _record_stat(marketplace: str, success: bool, elapsed: float) -> None:
        """Fire-and-forget stats recording — never raises."""
        try:
            from services.stats import StatsCollector  # noqa: PLC0415
            await StatsCollector.record_parse(marketplace, success=success, processing_time=elapsed)
        except Exception:  # noqa: BLE001
            pass

    @staticmethod
    async def _save_job(
        url: str,
        marketplace: str | None,
        started_at: datetime,
        finished_at: datetime,
        success: bool,
        error: str | None = None,
    ) -> None:
        """Persist a :class:`~models.job.Job` record — never raises."""
        try:
            from models.job import Job, JobStatus, async_session  # noqa: PLC0415

            if async_session is None:
                logger.warning(f"_save_job skipped (DB not initialised) for {url}")
                return

            async with async_session() as session:
                job = Job(
                    url=url,
                    marketplace=marketplace,
                    status=JobStatus.success.value if success else JobStatus.failed.value,
                    started_at=started_at,
                    finished_at=finished_at,
                    processing_time=(finished_at - started_at).total_seconds(),
                    error=error,
                )
                session.add(job)
                await session.commit()
        except Exception as _exc:  # noqa: BLE001
            logger.error(f"_save_job failed for {url}: {_exc}")

    async def _apply_rate_limit(self, marketplace: str) -> None:
        """Pace requests to match the configured rate limit (requests/min).

        Reads ``sources.{marketplace}.rate_limit`` from settings.yml (default 60).
        Uses a class-level lock per marketplace so that concurrent requests
        are spaced by ``60 / rate_limit`` seconds.
        """
        from config import settings  # noqa: PLC0415
        rate_limit: int = (
            settings.get("sources", {}).get(marketplace, {}).get("rate_limit", 60)
        )

        if rate_limit <= 0:
            return

        min_interval = 60.0 / rate_limit  # seconds between requests

        if marketplace not in ParsingService._rate_limit_locks:
            ParsingService._rate_limit_locks[marketplace] = asyncio.Lock()

        async with ParsingService._rate_limit_locks[marketplace]:
            now = time.monotonic()
            last = ParsingService._last_parse_time.get(marketplace, 0.0)
            wait = min_interval - (now - last)
            if wait > 0:
                await asyncio.sleep(wait)
            ParsingService._last_parse_time[marketplace] = time.monotonic()
