import re
import json
import asyncio
from loguru import logger
from decimal import Decimal
from bs4 import BeautifulSoup
from curl_cffi.requests.session import AsyncSession

from .base import BaseCrawler
from models import ProductCard
from utils.helpers import parse_url
from core.exceptions import ProductCardParsingException


class YandexMarketCrawler(BaseCrawler):
    def __init__(self):
        super().__init__()

        self.session = AsyncSession(impersonate="chrome136")

    def is_valid_url(self, url: str) -> bool:
        return "market.yandex." in url

    @staticmethod
    def get_headers():
        headers = {
            'accept': 'text/html,application/xhtml+xml,application/xml;q=0.9,image/avif,image/webp,image/apng,*/*;q=0.8,application/signed-exchange;v=b3;q=0.7',
            'accept-language': 'ru-RU,ru;q=0.9,en-US;q=0.8,en;q=0.7',
            'cache-control': 'no-cache',
            'pragma': 'no-cache',
            'priority': 'u=0, i',
            'sec-ch-ua': '"Not)A;Brand";v="8", "Chromium";v="136", "Google Chrome";v="136"',
            'sec-ch-ua-mobile': '?0',
            'sec-ch-ua-platform': '"Windows"',
            'sec-fetch-dest': 'document',
            'sec-fetch-mode': 'navigate',
            'sec-fetch-site': 'same-origin',
            'sec-fetch-user': '?1',
            'upgrade-insecure-requests': '1',
            'user-agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/136.0.0.0 Safari/537.36',
        }
        return headers

    async def get_product_page(self, url: str):
        product_page_content = None

        try:
            response = await self.session.get(
                url=url,
                headers=self.get_headers(),
            )

            product_page_content = response.content
        except Exception as e:
            logger.warning(f"Failed to get product page from YM {url}: {e}")

        return product_page_content

    async def parse(
            self,
            url: str,
    ):
        product_title = None
        product_sku = None
        product_original_price = 0.0
        product_current_price = 0.0
        product_images = []

        if "card/" not in url:
            raise ProductCardParsingException("Bad product URL")

        if "?" in url:
            url = url.split("?")[0]

        product_page_content = await self.get_product_page(url)

        if not product_page_content:
            raise ProductCardParsingException("Failed to get product page")

        soup = BeautifulSoup(product_page_content, "lxml")

        try:
            parsed_url = parse_url(url)
            product_sku = parsed_url["path"].split("/")[-1]
        except Exception as e:
            logger.warning(f"Failed to get product SKU from YM {url}: {e}")

        try:
            title_element = soup.find("h1", {"data-auto": "productCardTitle"})
            product_title = title_element.get_text(strip=True)
        except Exception as e:
            logger.warning(f"Failed to get product title from YM {url}: {e}")

        try:
            original_price_element = soup.find("span", {"data-auto": "snippet-price-old"})
            product_original_price = original_price_element.get_text(strip=True)
            product_original_price = Decimal(re.sub(r'[^\d]', '', re.search(r'[\d\s]+', product_original_price).group()))
        except Exception as e:
            logger.warning(f"Failed to get original price from YM {url}: {e}")

        try:
            current_price_element = soup.find("span", {"data-auto": "snippet-price-current"})
            product_current_price = current_price_element.get_text(strip=True)
            product_current_price = Decimal(re.sub(r'[^\d]', '', re.search(r'[\d\s]+', product_current_price).group()))
        except Exception as e:
            logger.warning(f"Failed to get current price from YM {url}: {e}")

        try:
            gallery_div_element = soup.find("div", {"data-apiary-widget-name": "@card/ShortUrl"})
            if gallery_div_element:
                gallery_noframes_element = gallery_div_element.find("noframes", {'data-apiary': 'patch'})

                if gallery_noframes_element:
                    gallery_json_data = json.loads(gallery_noframes_element.get_text(strip=True))

                    media_items = gallery_json_data.get('collections', {}).get('mediaItem', {})

                    gallery_items = gallery_json_data.get('collections', {}).get('gallery', {})
                    product_id = list(gallery_items.keys())[0]
                    media_ids = gallery_items[product_id].get('mediaItems', [])

                    product_images = []

                    for media_id in media_ids:
                        if media_id in media_items:
                            media_info = media_items[media_id]

                            if media_info.get("entity") == "avatars_picture":
                                width = media_info.get("width", 0)
                                height = media_info.get("height", 0)

                                product_images.append({
                                    "url": media_info.get("origUrl"),
                                    "title": media_info.get("title", ""),
                                    "width": width,
                                    "height": height,
                                    "resolution": width * height
                                })

                    product_images.sort(key=lambda x: x["resolution"], reverse=True)
                    product_images = [product["url"] for product in product_images]
        except Exception as e:
            logger.warning(f"Failed to get product images from YM {url}: {e}")

        card = ProductCard(
            title=product_title,
            sku=product_sku,
            original_price=product_original_price,
            current_price=product_current_price,
            images=product_images,
            source_url=url
        )

        return card


async def main():
    crawler = YandexMarketCrawler()
    product_card = await crawler.parse("https://market.yandex.ru/card/espander-dlya-paltsev-ruk-kistevoy-95-kg-s-krepleniyem-na-zapyastye-tsvet---chernyy/103757317666")
    print(json.dumps(product_card.to_dict(), indent=2, ensure_ascii=False))


if __name__ == '__main__':
    asyncio.run(main())
