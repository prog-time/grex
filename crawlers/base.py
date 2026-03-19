from __future__ import annotations

import logging
from abc import ABC, abstractmethod
from typing import TYPE_CHECKING, ClassVar

if TYPE_CHECKING:
    from models import ProductCard

logger = logging.getLogger(__name__)


class BaseCrawler(ABC):
    """Abstract base for all marketplace crawlers.

    Subclasses **must** declare:
    - ``marketplace_name: str``  — registry key (e.g. ``"ozon"``)
    - ``url_pattern: str``       — regex pattern used by
      :meth:`~crawlers.registry.CrawlerRegistry.detect_marketplace`

    Optionally declare:
    - ``requires_cookie_lock: bool = False`` — set to ``True`` for crawlers
      that need a per-marketplace asyncio.Lock during batch parsing
      (currently ozon and megamarket).

    On class definition :meth:`__init_subclass__` automatically registers
    the concrete crawler into
    :class:`~crawlers.registry.CrawlerRegistry`.
    """

    # Subclasses must override these two class-level attributes.
    marketplace_name: ClassVar[str]
    url_pattern: ClassVar[str]

    # Opt-in to cookie locking during batch parsing.
    requires_cookie_lock: ClassVar[bool] = False

    def __init_subclass__(cls, **kwargs: object) -> None:
        super().__init_subclass__(**kwargs)

        # Skip abstract intermediate classes that don't set marketplace_name.
        if not getattr(cls, "marketplace_name", None):
            return

        # Lazy import to avoid circular dependency at module load time.
        from crawlers.registry import CrawlerRegistry  # noqa: PLC0415

        CrawlerRegistry.register(cls)

    def __init__(self) -> None:
        self.proxy: str | None = None

    @abstractmethod
    async def parse(self, url: str) -> "ProductCard":
        """Parse the product page at *url* and return a :class:`~models.ProductCard`."""

    @abstractmethod
    def is_valid_url(self, url: str) -> bool:
        """Return ``True`` when *url* belongs to this marketplace."""

    async def get_product_links(self, url: str, max_pages: int = 10) -> list[str]:
        """Extract all product card URLs from a seller/catalog page.

        Override in concrete crawlers that support link extraction.
        Raises :class:`NotImplementedError` by default.
        """
        raise NotImplementedError(
            f"{self.__class__.__name__} does not support link extraction"
        )
