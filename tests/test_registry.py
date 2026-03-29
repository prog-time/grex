"""Tests for CrawlerRegistry (crawlers/registry.py).

Crawlers self-register when their modules are imported (via BaseCrawler.__init_subclass__).
Importing crawlers/__init__.py is sufficient to ensure all four crawlers are registered.
No external services or network calls are involved.
"""
from __future__ import annotations

import pytest

# Import the package so all concrete crawlers self-register.
import crawlers  # noqa: F401
from crawlers.registry import CrawlerRegistry
from core.exceptions import UnsupportedMarketplaceError


# ---------------------------------------------------------------------------
# detect_marketplace — known marketplaces
# ---------------------------------------------------------------------------

class TestDetectMarketplaceKnownURLs:
    """detect_marketplace must return the correct name for every supported domain."""

    @pytest.mark.parametrize("url", [
        "https://www.wildberries.ru/catalog/17386348/detail.aspx",
        "https://wildberries.ru/catalog/12345/detail.aspx",
        "https://wildberries.by/catalog/99999/detail.aspx",
        "https://wildberries.kz/catalog/11111/detail.aspx",
        "https://WILDBERRIES.RU/catalog/1/detail.aspx",  # uppercase must also match
    ])
    def test_detects_wildberries(self, url):
        assert CrawlerRegistry.detect_marketplace(url) == "wildberries"

    @pytest.mark.parametrize("url", [
        "https://ozon.ru/product/shumin-maska-1563221610",
        "https://ozon.by/product/test-item",
        "https://ozon.kz/product/test-item",
        "https://OZON.RU/product/uppercase",
    ])
    def test_detects_ozon(self, url):
        assert CrawlerRegistry.detect_marketplace(url) == "ozon"

    @pytest.mark.parametrize("url", [
        "https://market.yandex.ru/card/espander/103757317666",
        "https://market.yandex.by/card/something/123456",
        "https://MARKET.YANDEX.RU/card/uppercase/999",
    ])
    def test_detects_yandex_market(self, url):
        assert CrawlerRegistry.detect_marketplace(url) == "yandex_market"

    @pytest.mark.parametrize("url", [
        "https://megamarket.ru/catalog/details/puhovik-600011555038",
        "https://www.megamarket.ru/catalog/details/some-product-123",
        "https://MEGAMARKET.RU/catalog/details/uppercase",
    ])
    def test_detects_megamarket(self, url):
        assert CrawlerRegistry.detect_marketplace(url) == "megamarket"

    @pytest.mark.parametrize("url", [
        "https://www.vseinstrumenti.ru/product/drel-shurupovert-aeg-bs18g4-202c-4935478630-1760653/",
        "https://vseinstrumenti.ru/product/some-tool-12345/",
        "https://VSEINSTRUMENTI.RU/product/uppercase-999/",
        "https://www.vseinstrumenti.ru/category/shurupoverty-1774/",
    ])
    def test_detects_vseinstrumenti(self, url):
        assert CrawlerRegistry.detect_marketplace(url) == "vseinstrumenti"

    @pytest.mark.parametrize("url", [
        "https://nizhniy-novgorod.bigam.ru/product/motoblok-2064429/",
        "https://moskva.bigam.ru/product/some-product-1234567/",
        "https://bigam.ru/product/some-product-1234567/",
        "https://NIZHNIY-NOVGOROD.BIGAM.RU/product/uppercase-999/",
        "https://spb.bigam.ru/catalog/motobloki/",
    ])
    def test_detects_bigam(self, url):
        assert CrawlerRegistry.detect_marketplace(url) == "bigam"


# ---------------------------------------------------------------------------
# detect_marketplace — unsupported URL
# ---------------------------------------------------------------------------

class TestDetectMarketplaceUnsupportedURL:
    """detect_marketplace must raise UnsupportedMarketplaceError for unknown URLs."""

    @pytest.mark.parametrize("url", [
        "https://amazon.com/dp/B08N5WRWNW",
        "https://ebay.com/itm/123456789",
        "https://aliexpress.ru/item/1234567890.html",
        "https://google.com",
        "https://wildberries.com/fake",       # .com is not a supported TLD
        "https://nomarket.example.org/foo",
        "",
    ])
    def test_raises_for_unsupported_url(self, url):
        with pytest.raises((UnsupportedMarketplaceError, ValueError)):
            CrawlerRegistry.detect_marketplace(url)


# ---------------------------------------------------------------------------
# CrawlerRegistry.all() — registry completeness
# ---------------------------------------------------------------------------

class TestRegistryAll:
    """all() must contain exactly the six registered marketplaces."""

    def test_contains_wildberries(self):
        assert "wildberries" in CrawlerRegistry.all()

    def test_contains_ozon(self):
        assert "ozon" in CrawlerRegistry.all()

    def test_contains_yandex_market(self):
        assert "yandex_market" in CrawlerRegistry.all()

    def test_contains_megamarket(self):
        assert "megamarket" in CrawlerRegistry.all()

    def test_contains_vseinstrumenti(self):
        assert "vseinstrumenti" in CrawlerRegistry.all()

    def test_contains_bigam(self):
        assert "bigam" in CrawlerRegistry.all()

    def test_returns_six_entries(self):
        assert len(CrawlerRegistry.all()) == 6

    def test_returns_shallow_copy(self):
        """Mutating the returned dict must not affect the registry."""
        registry_copy = CrawlerRegistry.all()
        registry_copy["intruder"] = object()  # type: ignore[assignment]
        assert "intruder" not in CrawlerRegistry.all()


# ---------------------------------------------------------------------------
# CrawlerRegistry.get()
# ---------------------------------------------------------------------------

class TestRegistryGet:
    def test_get_returns_crawler_class_for_wildberries(self):
        from crawlers.wildberries import WildberriesCrawler
        assert CrawlerRegistry.get("wildberries") is WildberriesCrawler

    def test_get_returns_crawler_class_for_ozon(self):
        from crawlers.ozon import OzonCrawler
        assert CrawlerRegistry.get("ozon") is OzonCrawler

    def test_get_returns_crawler_class_for_yandex_market(self):
        from crawlers.yandex_market import YandexMarketCrawler
        assert CrawlerRegistry.get("yandex_market") is YandexMarketCrawler

    def test_get_returns_crawler_class_for_megamarket(self):
        from crawlers.megamarket import MegaMarketCrawler
        assert CrawlerRegistry.get("megamarket") is MegaMarketCrawler

    def test_get_returns_crawler_class_for_vseinstrumenti(self):
        from crawlers.vseinstrumenti import VseInstrumentiCrawler
        assert CrawlerRegistry.get("vseinstrumenti") is VseInstrumentiCrawler

    def test_get_returns_crawler_class_for_bigam(self):
        from crawlers.bigam import BigamCrawler
        assert CrawlerRegistry.get("bigam") is BigamCrawler

    def test_get_raises_for_unknown_marketplace(self):
        from core.exceptions import CrawlerNotFoundError
        with pytest.raises(CrawlerNotFoundError):
            CrawlerRegistry.get("unknown_marketplace_xyz")


# ---------------------------------------------------------------------------
# BaseCrawler registration guard
# ---------------------------------------------------------------------------

class TestRegistrationGuard:
    def test_crawler_without_marketplace_name_is_not_registered(self):
        """A subclass that does not set marketplace_name must not appear in the registry."""
        from crawlers.base import BaseCrawler
        from models import ProductCard

        class _NoNameCrawler(BaseCrawler):
            # deliberately omits marketplace_name
            async def parse(self, url: str) -> ProductCard: ...   # type: ignore[return]
            def is_valid_url(self, url: str) -> bool: return False

        assert "_NoNameCrawler" not in CrawlerRegistry.all()
        assert "_nononame" not in CrawlerRegistry.all()
