"""CrawlerRegistry — centralized registry for marketplace crawlers.

Crawlers are auto-registered via BaseCrawler.__init_subclass__ when their
module is imported.  No manual dictionary maintenance required.
"""
from __future__ import annotations

import re
from typing import TYPE_CHECKING

from core.exceptions import CrawlerNotFoundError, UnsupportedMarketplaceError

if TYPE_CHECKING:
    from crawlers.base import BaseCrawler


class CrawlerRegistry:
    """Singleton-style registry that maps marketplace names to crawler classes."""

    _registry: dict[str, type["BaseCrawler"]] = {}

    # ------------------------------------------------------------------
    # Registration (called by BaseCrawler.__init_subclass__)
    # ------------------------------------------------------------------

    @classmethod
    def register(cls, crawler_cls: type["BaseCrawler"]) -> None:
        """Register *crawler_cls* under its ``marketplace_name`` key."""
        name: str = getattr(crawler_cls, "marketplace_name", None)  # type: ignore[assignment]
        if not name:
            raise ValueError(
                f"Crawler {crawler_cls.__name__} must define a non-empty "
                "'marketplace_name' class attribute to be registered."
            )
        cls._registry[name] = crawler_cls

    # ------------------------------------------------------------------
    # Lookup
    # ------------------------------------------------------------------

    @classmethod
    def get(cls, marketplace_name: str) -> type["BaseCrawler"]:
        """Return the crawler class for *marketplace_name*.

        Raises :class:`~core.exceptions.CrawlerNotFoundError` when the
        marketplace is not registered.
        """
        try:
            return cls._registry[marketplace_name]
        except KeyError:
            raise CrawlerNotFoundError(
                f"Crawler not found for marketplace: {marketplace_name}"
            )

    @classmethod
    def all(cls) -> dict[str, type["BaseCrawler"]]:
        """Return a shallow copy of the registry mapping."""
        return dict(cls._registry)

    # ------------------------------------------------------------------
    # URL detection
    # ------------------------------------------------------------------

    @classmethod
    def detect_marketplace(cls, url: str) -> str:
        """Infer marketplace name from *url* using each crawler's ``url_pattern``.

        Raises :class:`~core.exceptions.UnsupportedMarketplaceError` when no
        registered crawler matches the URL.
        """
        url_lower = url.lower()
        for name, crawler_cls in cls._registry.items():
            pattern: str = getattr(crawler_cls, "url_pattern", "")
            if pattern and re.search(pattern, url_lower):
                return name
        raise UnsupportedMarketplaceError(f"Unsupported marketplace URL: {url}")
