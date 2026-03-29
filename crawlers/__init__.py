from .base import BaseCrawler, CookieCrawler
from .registry import CrawlerRegistry

# Import all concrete crawlers so they self-register into CrawlerRegistry
# via BaseCrawler.__init_subclass__ at module load time.
from .wildberries import WildberriesCrawler
from .ozon import OzonCrawler
from .yandex_market import YandexMarketCrawler
from .megamarket import MegaMarketCrawler
from .vseinstrumenti import VseInstrumentiCrawler
from .bigam import BigamCrawler

__all__ = [
    "BaseCrawler",
    "CookieCrawler",
    "CrawlerRegistry",
    "WildberriesCrawler",
    "OzonCrawler",
    "YandexMarketCrawler",
    "MegaMarketCrawler",
    "VseInstrumentiCrawler",
    "BigamCrawler",
]
