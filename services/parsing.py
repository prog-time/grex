import traceback
from loguru import logger
from datetime import datetime
from fastapi import HTTPException, status

from models import ProductCard
from core import CrawlerFactory, UnsupportedMarketplaceError, CrawlerNotFoundError


class ParsingService:
    def __init__(self):
        self.factory = CrawlerFactory()
        self.start_time = datetime.now()

    async def parse_product(self, url: str) -> ProductCard:
        try:
            crawler = self.factory.create_crawler(url)
            product_card = await crawler.parse(url)
            return product_card

        except (UnsupportedMarketplaceError, CrawlerNotFoundError) as e:
            logger.error(f"URL validation error: {e}")
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail=str(e)
            )

        except Exception as e:
            logger.error(f"Parsing error for {url}: {e}")
            traceback.print_exc()
            raise HTTPException(
                status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
                detail=f"Failed to parse product: {str(e)}"
            )

    def get_uptime(self) -> float:
        return (datetime.now() - self.start_time).total_seconds()
