"""CrawlerFactory — thin backward-compatibility wrapper over CrawlerRegistry.

.. deprecated::
    Use :class:`~crawlers.registry.CrawlerRegistry` directly.
    ``CrawlerFactory`` is kept only for transitional compatibility with
    existing callers in ``api.py`` and ``services/parsing.py`` until
    TASK-4 is applied.
"""
from __future__ import annotations

import warnings
from typing import List

from crawlers.base import BaseCrawler
from crawlers.registry import CrawlerRegistry


class CrawlerFactory:
    """Deprecated facade around :class:`~crawlers.registry.CrawlerRegistry`."""

    def __init__(self) -> None:
        warnings.warn(
            "CrawlerFactory is deprecated; use CrawlerRegistry directly.",
            DeprecationWarning,
            stacklevel=2,
        )

    def detect_marketplace(self, url: str) -> str:
        return CrawlerRegistry.detect_marketplace(url)

    def create_crawler(self, url: str) -> BaseCrawler:
        marketplace = CrawlerRegistry.detect_marketplace(url)
        crawler_cls = CrawlerRegistry.get(marketplace)
        return crawler_cls()

    def get_supported_marketplaces(self) -> List[str]:
        return list(CrawlerRegistry.all().keys())
