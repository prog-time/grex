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
import os
from abc import ABC, abstractmethod
from multiprocessing import Process, Queue
from typing import TYPE_CHECKING, Any, Callable, ClassVar

import aiofiles  # type: ignore[import-untyped]
from loguru import logger

if TYPE_CHECKING:
    from models import ProductCard


# Global cap on concurrent browser subprocesses, keyed by event loop so a
# single process shares one semaphore across every crawler and request.
_BROWSER_SEMAPHORES: "dict[Any, asyncio.Semaphore]" = {}


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
    license_source_name: ClassVar[str] = ""  # override when JWT source name differs from marketplace_name

    # --- URL normalization (override per crawler when needed) ---
    # Regex whose group(1) is the canonical product-URL prefix. When set,
    # normalize_url() trims any trailing tab segments (e.g. "/reviews/").
    canonical_url_pattern: ClassVar[str] = ""
    # Whether to drop the query string ("?...") during normalization. Leave
    # False for marketplaces where query params carry the offer/sku id.
    strip_query_params: ClassVar[bool] = False

    def __init_subclass__(cls, **kwargs: object) -> None:
        super().__init_subclass__(**kwargs)
        if not getattr(cls, "marketplace_name", None):
            return
        from crawlers.registry import CrawlerRegistry  # noqa: PLC0415
        CrawlerRegistry.register(cls)

    def __init__(self) -> None:
        self.proxy: str | None = None

    @staticmethod
    async def get_proxy(marketplace: str | None = None) -> str | None:
        """Return a random proxy string if proxy is active for this marketplace, else None."""
        import re  # noqa: PLC0415

        import yaml  # noqa: PLC0415

        from config import data_path  # noqa: PLC0415

        try:
            settings_file = __import__("os").path.join(data_path, "settings.yml")
            with open(settings_file, encoding="utf-8") as f:
                live_settings = yaml.safe_load(f) or {}
        except Exception:
            from config import settings as live_settings  # noqa: PLC0415

        proxy_cfg = live_settings.get("proxy", {})
        if not proxy_cfg.get("active", False):
            return None

        try:
            import random  # noqa: PLC0415

            from admin.resources.proxies import _get_proxies  # noqa: PLC0415

            proxies = await _get_proxies()
            if not proxies:
                logger.warning("Proxy is enabled but proxy pool is empty")
                return None

            proxy = random.choice(proxies)
            proxy_type = proxy_cfg.get("type", "http")
            if "://" not in proxy:
                proxy = f"{proxy_type}://{proxy}"

            # Force remote DNS for SOCKS5 (socks5h): the exit node resolves the
            # target host instead of curl resolving it locally. Local resolution
            # can hand the proxy an IP its exit can't reach, surfacing as
            # curl (97) "cannot complete SOCKS5 connection … (4) Host unreachable".
            if proxy.startswith("socks5://"):
                proxy = "socks5h://" + proxy[len("socks5://"):]

            masked = re.sub(r"(://[^:]+):([^@]+)@", r"\1:***@", proxy)
            logger.info(f"Using proxy: {masked}" + (f" [{marketplace}]" if marketplace else ""))
            return proxy
        except Exception as exc:
            logger.warning(f"Failed to get proxy: {exc}")
            return None

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
    # Shared URL normalization — applied to every source before parse()
    # ------------------------------------------------------------------

    @classmethod
    def normalize_url(cls, url: str) -> str:
        """Return a canonical product URL for *url*.

        Always strips the fragment (``#reviews``) — fragments are never sent to
        the server, so this is safe for every marketplace. When the crawler sets
        :attr:`strip_query_params` and/or :attr:`canonical_url_pattern`, the
        query string and trailing tab segments (``/reviews/``, ``/otzyvy/`` …)
        are also removed, so variant URLs collapse to one canonical URL and
        stop producing duplicate results.
        """
        import re  # noqa: PLC0415

        normalized = url.strip().split("#", 1)[0]
        if cls.strip_query_params:
            normalized = normalized.split("?", 1)[0]
        if cls.canonical_url_pattern:
            m = re.match(cls.canonical_url_pattern, normalized)
            if m:
                return m.group(1).rstrip("/") + "/"
        return normalized

    @staticmethod
    def _playwright_proxy(proxy: str | None) -> dict | None:
        """Convert a proxy string to a Playwright/Chromium proxy dict, or None.

        Chromium supports HTTP/HTTPS proxy authentication but NOT SOCKS5 auth,
        so an authenticated socks5 proxy returns None and the browser runs
        directly (no regression). Add HTTP proxies to the pool to route a
        crawler's browser fallback through a residential IP.
        """
        import re  # noqa: PLC0415

        if not proxy:
            return None
        m = re.match(
            r"^(?P<scheme>\w+)://"
            r"(?:(?P<user>[^:@/]+):(?P<pwd>[^@/]+)@)?"
            r"(?P<host>[^:/@]+):(?P<port>\d+)",
            proxy,
        )
        if not m:
            return None
        scheme = m.group("scheme").lower()
        host, port = m.group("host"), m.group("port")
        user, pwd = m.group("user"), m.group("pwd")

        if scheme in ("http", "https"):
            server = f"{scheme}://{host}:{port}"
        elif scheme.startswith("socks"):
            if user:
                return None  # Chromium cannot authenticate SOCKS5 → run direct
            server = f"socks5://{host}:{port}"
        else:
            return None

        out: dict = {"server": server}
        if user:
            out["username"] = user
            out["password"] = pwd
        return out

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
    def _browser_semaphore() -> "asyncio.Semaphore":
        """Return the global semaphore capping concurrent browser subprocesses.

        Sized from ``browser.max_concurrent`` in settings (default 4). One
        semaphore per running event loop, so a single process never spawns more
        than N Chromium instances at once across all crawlers, batch requests
        and the admin background parser combined.
        """
        loop = asyncio.get_event_loop()
        sem = _BROWSER_SEMAPHORES.get(loop)
        if sem is None:
            from config import settings  # noqa: PLC0415
            try:
                limit = int(settings.get("browser", {}).get("max_concurrent", 4))
            except (TypeError, ValueError):
                limit = 4
            sem = asyncio.Semaphore(max(1, limit))
            _BROWSER_SEMAPHORES[loop] = sem
        return sem

    @staticmethod
    async def _run_in_process(
        target: Callable[..., None],
        args: tuple[Any, ...] = (),
        *,
        timeout: int | None = None,
    ) -> dict | None:
        """Run *target*(*args, result_queue) in a subprocess and return the result.

        *target* must accept a multiprocessing.Queue as its **last** positional
        argument and put exactly one dict into it before returning::

            {"success": True,  ...data}
            {"success": False, "error": str}

        Returns the dict on success, or None if the queue is empty or the
        process exceeds *timeout* seconds. When *timeout* is None it defaults to
        ``browser.timeout_seconds`` from settings (60s), so the browser
        kill-timeout is configurable without code changes.

        Held under a global semaphore so the number of concurrent browser
        subprocesses never exceeds ``browser.max_concurrent`` — excess callers
        wait here rather than spawning unbounded Chromium instances.
        """
        import queue as _queue  # noqa: PLC0415

        if timeout is None:
            from config import settings  # noqa: PLC0415
            try:
                timeout = int(settings.get("browser", {}).get("timeout_seconds", 60))
            except (TypeError, ValueError):
                timeout = 60

        async with BaseCrawler._browser_semaphore():
            result_queue: Queue[dict] = Queue()
            process = Process(target=target, args=(*args, result_queue))
            process.start()
            deadline = asyncio.get_event_loop().time() + timeout

            # Drain the queue WHILE the child is alive. A child that puts a large
            # payload (e.g. full page HTML) won't exit until the queue's feeder
            # thread has flushed it to the pipe; if we join() before reading, the
            # full pipe blocks the child and join() deadlocks. So read first.
            result: dict | None = None
            while True:
                try:
                    result = result_queue.get_nowait()
                    break
                except _queue.Empty:
                    pass
                if not process.is_alive():
                    try:
                        result = result_queue.get_nowait()
                    except _queue.Empty:
                        result = None
                    break
                if asyncio.get_event_loop().time() >= deadline:
                    process.terminate()
                    process.join(timeout=5)
                    logger.warning(
                        f"_run_in_process: {getattr(target, '__name__', target)!r} "
                        f"killed after {timeout}s timeout"
                    )
                    return None
                await asyncio.sleep(0.2)

            process.join(timeout=5)
            if process.is_alive():
                process.terminate()
            if result is None:
                logger.warning(
                    f"_run_in_process: {getattr(target, '__name__', target)!r} "
                    f"returned no result (timeout={timeout}s)"
                )
            return result


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
