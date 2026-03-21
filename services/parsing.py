from __future__ import annotations

import asyncio
import time
import traceback
from datetime import datetime, timezone
from loguru import logger
from fastapi import HTTPException, status

from models import ProductCard, BatchItemResult
from core.exceptions import UnsupportedMarketplaceError, CrawlerNotFoundError, ProductCardParsingException
# Import the crawlers package (not just the registry) so that all concrete
# crawler classes are loaded and auto-registered via __init_subclass__.
import crawlers as _crawlers_pkg  # noqa: F401
from crawlers.registry import CrawlerRegistry


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
            product_card = await crawler.parse(url)

            finished_at = datetime.now(timezone.utc)
            elapsed = time.monotonic() - t_start
            await self._record_stat(marketplace, success=True, elapsed=elapsed)
            await self._save_job(url, marketplace, started_at, finished_at, success=True)
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
        self, urls: list[str], concurrency: int = 10
    ) -> list[BatchItemResult]:
        semaphore = asyncio.Semaphore(concurrency)

        async def parse_one(url: str) -> BatchItemResult:
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

        tasks = [parse_one(url) for url in urls]
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
            from models.job import async_session, Job, JobStatus  # noqa: PLC0415

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
