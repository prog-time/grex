from .base import BaseCrawler, CookieCrawler
from .bigam import BigamCrawler
from .cdek import CdekCrawler
from .citilink import CitilinkCrawler
from .megamarket import MegaMarketCrawler
from .mm import MmCrawler
from .ozon import OzonCrawler
from .registry import CrawlerRegistry
from .vseinstrumenti import VseInstrumentiCrawler

# Import all concrete crawlers so they self-register into CrawlerRegistry
# via BaseCrawler.__init_subclass__ at module load time.
from .wildberries import WildberriesCrawler
from .yandex_market import YandexMarketCrawler

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
    "MmCrawler",
    "CdekCrawler",
    "CitilinkCrawler",
]
