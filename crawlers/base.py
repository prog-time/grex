import logging
from typing import TYPE_CHECKING
from abc import ABC, abstractmethod

if TYPE_CHECKING:
    from models import ProductCard

logger = logging.getLogger(__name__)


class BaseCrawler(ABC):
    def __init__(self):
        self.proxy = None
        self.marketplace_name = self.__class__.__name__.replace('Crawler', '')

    @abstractmethod
    async def parse(self, url: str) -> 'ProductCard':
        pass

    @abstractmethod
    def is_valid_url(self, url: str) -> bool:
        pass
