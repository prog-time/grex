"""Crawler base infrastructure.

Architecture overview
---------------------

BaseCrawler
    Abstract base for every marketplace crawler.
    Registers itself in CrawlerRegistry on class definition.
    Provides _run_in_process() — shared subprocess runner for browser tasks.

CookieCrawler(BaseCrawler)
    Abstract base for crawlers that authenticate via browser-captured cookies.
    Provides get_cookies(), load_cookies_from_file(), delete_cookies_file().
    Subclasses must implement save_cookies() and _process_save_cookies().

Adding a new marketplace
------------------------
1. Create crawlers/<marketplace>.py
2. Subclass BaseCrawler (plain HTTP) or CookieCrawler (cookie-based auth)
3. Declare marketplace_name and url_pattern
4. Implement parse() and is_valid_url()
5. Optionally implement get_product_links()
6. Import the module in crawlers/__init__.py

The class auto-registers in CrawlerRegistry via __init_subclass__.
"""
from __future__ import annotations

import asyncio
import json
import logging
import os
from abc import ABC, abstractmethod
from multiprocessing import Process, Queue
from typing import TYPE_CHECKING, Any, Callable, ClassVar

import aiofiles  # type: ignore[import-untyped]

if TYPE_CHECKING:
    from models import ProductCard

logger = logging.getLogger(__name__)


class BaseCrawler(ABC):
    """Abstract base for all marketplace crawlers.

    Required class attributes
    -------------------------
    marketplace_name : str
        Unique registry key (e.g. ``"ozon"``).
    url_pattern : str
        Regex used by CrawlerRegistry.detect_marketplace() to match URLs.

    Optional class attributes
    -------------------------
    requires_cookie_lock : bool = False
        Set True to serialize batch requests with a per-marketplace asyncio.Lock.
        Needed when the crawler's session/cookie state must not be shared concurrently.

    Required methods
    ----------------
    parse(url) -> ProductCard
        Parse one product page and return a populated ProductCard.
        Should extract: title, sku, original_price, current_price, images, source_url.
        Raise ProductCardParsingException on unrecoverable failure.
    is_valid_url(url) -> bool
        Return True when *url* belongs to this marketplace.

    Optional methods
    ----------------
    get_product_links(url, max_pages) -> list[str]
        Extract product URLs from a catalog/seller/brand page.
        Default raises NotImplementedError.
    """

    marketplace_name: ClassVar[str]
    url_pattern: ClassVar[str]
    requires_cookie_lock: ClassVar[bool] = False

    def __init_subclass__(cls, **kwargs: object) -> None:
        super().__init_subclass__(**kwargs)
        if not getattr(cls, "marketplace_name", None):
            return
        from crawlers.registry import CrawlerRegistry  # noqa: PLC0415
        CrawlerRegistry.register(cls)

    def __init__(self) -> None:
        self.proxy: str | None = None

    # ------------------------------------------------------------------
    # Interface — implement in every concrete crawler
    # ------------------------------------------------------------------

    @abstractmethod
    async def parse(self, url: str) -> "ProductCard":
        """Parse the product page at *url* and return a ProductCard."""

    @abstractmethod
    def is_valid_url(self, url: str) -> bool:
        """Return True when *url* belongs to this marketplace."""

    # ------------------------------------------------------------------
    # Optional interface — override to support catalog link extraction
    # ------------------------------------------------------------------

    async def get_product_links(self, url: str, max_pages: int = 10) -> list[str]:
        """Extract product page URLs from a catalog/seller/brand *url*.

        Returns a deduplicated list of absolute product URLs.
        Raises NotImplementedError if not supported by this crawler.
        """
        raise NotImplementedError(
            f"{self.__class__.__name__} does not support link extraction"
        )

    # ------------------------------------------------------------------
    # Shared utility — subprocess runner for browser-based operations
    # ------------------------------------------------------------------

    @staticmethod
    async def _run_in_process(
        target: Callable[..., None],
        args: tuple[Any, ...] = (),
        *,
        timeout: int = 60,
    ) -> dict | None:
        """Run *target*(*args, result_queue) in a subprocess and return the result.

        *target* must accept a multiprocessing.Queue as its **last** positional
        argument and put exactly one dict into it before returning::

            {"success": True,  ...data}
            {"success": False, "error": str}

        Returns the dict on success, or None if the queue is empty or the
        process exceeds *timeout* seconds.
        """
        result_queue: Queue[dict] = Queue()
        process = Process(target=target, args=(*args, result_queue))
        process.start()
        deadline = asyncio.get_event_loop().time() + timeout
        while process.is_alive():
            if asyncio.get_event_loop().time() >= deadline:
                process.terminate()
                process.join()
                logger.warning(
                    f"_run_in_process: {getattr(target, '__name__', target)!r} "
                    f"killed after {timeout}s timeout"
                )
                return None
            await asyncio.sleep(0.2)
        process.join()
        if not result_queue.empty():
            return result_queue.get()
        logger.warning(
            f"_run_in_process: {getattr(target, '__name__', target)!r} "
            f"returned no result (timeout={timeout}s)"
        )
        return None


class CookieCrawler(BaseCrawler, ABC):
    """Abstract base for crawlers that authenticate via browser-captured cookies.

    Required class attribute
    ------------------------
    cookies_file_path : str
        Absolute path to the JSON file where cookies are persisted.

    Required methods
    ----------------
    save_cookies(url) -> bool
        Open a browser subprocess, capture cookies, persist to cookies_file_path,
        store in self.cookies_data.  Return True on success.
        Typical implementation calls _run_in_process(_process_save_cookies, ...).

    _process_save_cookies(..., result_queue) — @staticmethod
        Subprocess target.  Must put one of the following into result_queue::

            {"success": True, "cookies": dict, ...optional_extras}
            {"success": False, "error": str}

    Provided methods (override only when behaviour must differ)
    -----------------------------------------------------------
    get_cookies(url) -> dict
        Return cached cookies; load from file on first call; refresh via
        browser when file is missing or empty.
    load_cookies_from_file() -> dict
        Read and parse cookies_file_path; return {} on any error.
    delete_cookies_file() -> None
        Remove cookies_file_path (call when cookies appear invalid/expired).
    """

    cookies_file_path: ClassVar[str]

    def __init__(self) -> None:
        super().__init__()
        self.cookies_data: dict | None = None

    # ------------------------------------------------------------------
    # Public cookie API
    # ------------------------------------------------------------------

    async def get_cookies(self, url: str) -> dict:
        """Return valid cookies, refreshing via browser when cache/file is empty."""
        if not self.cookies_data:
            self.cookies_data = await self.load_cookies_from_file()
        if not self.cookies_data:
            await self.save_cookies(url)
        return self.cookies_data or {}

    @abstractmethod
    async def save_cookies(self, url: str) -> bool:
        """Capture cookies via browser subprocess and persist to cookies_file_path.

        Returns True on success.
        """

    async def load_cookies_from_file(self) -> dict:
        """Load cookies from cookies_file_path JSON.  Returns {} on any error."""
        if not os.path.exists(self.cookies_file_path):
            logger.debug(f"Cookies file not found: {self.cookies_file_path}")
            return {}
        try:
            async with aiofiles.open(self.cookies_file_path) as f:
                cookies = json.loads(await f.read())
                logger.debug(f"Cookies loaded from {self.cookies_file_path}")
                return cookies
        except Exception as exc:
            logger.error(f"Failed to load cookies from {self.cookies_file_path}: {exc}")
            return {}

    def delete_cookies_file(self) -> None:
        """Delete cookies_file_path (call when cookies are expired or invalid)."""
        if os.path.exists(self.cookies_file_path):
            os.remove(self.cookies_file_path)
            logger.debug(f"Deleted cookies file: {self.cookies_file_path}")
