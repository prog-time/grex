import re
from typing import List
from loguru import logger

from crawlers import (
    BaseCrawler,
    OzonCrawler,
    MegaMarketCrawler,
    WildberriesCrawler,
    YandexMarketCrawler
)
from core.exceptions import UnsupportedMarketplaceError, CrawlerNotFoundError


class CrawlerFactory:
    def __init__(self):
        self.crawlers = {
            "wildberries": WildberriesCrawler,
            "ozon": OzonCrawler,
            "yandex_market": YandexMarketCrawler,
            "megamarket": MegaMarketCrawler
        }

        self.url_patterns = {
            r'wildberries\.(ru|by|kz)': "wildberries",
            r'ozon\.(ru|by|kz)': "ozon",
            r'market\.yandex\.(ru|by)': "yandex_market",
            r'megamarket\.ru': "megamarket"
        }

    def detect_marketplace(self, url: str) -> str:
        url_lower = url.lower()

        for pattern, marketplace in self.url_patterns.items():
            if re.search(pattern, url_lower):
                logger.debug(f"Detected marketplace: {marketplace} for URL: {url}")
                return marketplace

        raise UnsupportedMarketplaceError(f"Unsupported marketplace URL: {url}")

    def create_crawler(self, url: str) -> BaseCrawler:
        marketplace = self.detect_marketplace(url)

        if marketplace not in self.crawlers:
            raise CrawlerNotFoundError(f"Crawler not found for marketplace: {marketplace}")

        crawler_class = self.crawlers[marketplace]
        return crawler_class()

    def get_supported_marketplaces(self) -> List[str]:
        return list(self.crawlers.keys())
