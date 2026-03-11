import os
import json
import time
import asyncio
from loguru import logger
from decimal import Decimal
from curl_cffi.requests.session import AsyncSession

from config import data_path
from .base import BaseCrawler
from models import ProductCard
from utils.helpers import parse_url
from core.exceptions import ProductCardParsingException


class WildberriesCrawler(BaseCrawler):
    upstreams_file_path = os.path.join(data_path, "wb_upstreams.json")

    def __init__(self):
        super().__init__()

        self.upstreams_data = self.load_upstreams_from_file()
        self.session = AsyncSession(impersonate="chrome136")

    def is_valid_url(self, url: str) -> bool:
        return "wildberries." in url

    def load_upstreams_from_file(self):
        upstreams_data = {}

        if not os.path.exists(self.upstreams_file_path):
            logger.debug(f"Upstreams file not found: {self.upstreams_file_path}")
            return upstreams_data

        try:
            with open(self.upstreams_file_path) as f:
                upstreams_data = json.load(f)

                logger.debug(f"WB upstreams data loaded from file: {self.upstreams_file_path}")
                return upstreams_data
        except Exception as e:
            logger.error(f"Failed to load WB upstreams data from {self.upstreams_file_path}: {e}")
            return upstreams_data

    async def save_upstreams_data(self):
        response = await self.session.get(
            url=f"https://cdn.wbbasket.ru/api/v3/upstreams?t={int(time.time() * 1000)}"
        )
        data = response.json()

        if "recommend" in data:
            with open(self.upstreams_file_path, "w", encoding="utf-8") as f:
                json.dump(data, f, ensure_ascii=False, indent=2)

            self.upstreams_data = data
            logger.debug("WB upstreams data saved")

    async def get_upstream_host_by_sku(self, sku: str):
        if not self.upstreams_data:
            await self.save_upstreams_data()

        upstream_host = "basket-26.wbbasket.ru"
        vol_number = int(sku) // 100000
        part_number = int(sku) // 1000

        for upstream_item in self.upstreams_data["recommend"]["mediabasket_route_map"][0]["hosts"]:
            if upstream_item["vol_range_from"] <= vol_number <= upstream_item["vol_range_to"]:
                upstream_host = upstream_item["host"]

        return f"https://{upstream_host}/vol{vol_number}/part{part_number}"

    async def get_product_card(self, sku: str):
        try:
            upstream_host = await self.get_upstream_host_by_sku(sku)

            response = await self.session.get(
                url=f"{upstream_host}/{sku}/info/ru/card.json"
            )
            return response.json()
        except Exception as e:
            logger.warning(f"Failed to get card information from WB #{sku}: {e}")
            return None

    @staticmethod
    def get_headers():
        headers = {
            'accept': 'text/html,application/xhtml+xml,application/xml;q=0.9,image/avif,image/webp,image/apng,*/*;q=0.8,application/signed-exchange;v=b3;q=0.7',
            'accept-language': 'ru-RU,ru;q=0.9',
            'cache-control': 'no-cache',
            'pragma': 'no-cache',
            'priority': 'u=0, i',
            'sec-ch-ua': '"Not)A;Brand";v="8", "Chromium";v="138", "Google Chrome";v="138"',
            'sec-ch-ua-mobile': '?0',
            'sec-ch-ua-platform': '"Windows"',
            'sec-fetch-dest': 'document',
            'sec-fetch-mode': 'navigate',
            'sec-fetch-site': 'none',
            'sec-fetch-user': '?1',
            'upgrade-insecure-requests': '1',
            'user-agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/138.0.0.0 Safari/537.36',
        }
        return headers

    async def get_product_details(self, sku: str):
        product_data = None

        try:
            response = await self.session.get(
                url=f"https://card.wb.ru/cards/v2/detail?&dest=-1257786&nm={sku}",
                headers=self.get_headers(),
            )

            data = response.json()
            if "data" in data:
                products = data["data"]["products"]
                if products:
                    product_data = products[0]
        except Exception as e:
            logger.warning(f"Failed to get details from WB #{sku}: {e}")

        return product_data

    async def parse(
            self,
            url: str,
    ) -> ProductCard:
        product_sku = None
        product_original_price = 0.0
        product_current_price = 0.0
        product_images = []

        parsed_url = parse_url(url)
        if "catalog" in parsed_url["path"] and "detail.aspx" in parsed_url["path"]:
            product_sku = str(parsed_url["path"].split("/")[2])

        if not product_sku:
            raise ProductCardParsingException("Product SKU not found")

        product_card = await self.get_product_card(sku=product_sku)
        if not product_card:
            raise ProductCardParsingException("Product card not found")

        product_title = product_card.get("imt_name")
        upstream_host = await self.get_upstream_host_by_sku(product_sku)
        product_photo_count = product_card.get("media", {}).get("photo_count") or 0

        if product_photo_count:
            for image_index in range(int(product_photo_count)):
                product_images.append(
                    f"{upstream_host}/{product_sku}/images/big/{image_index + 1}.webp"
                )

        product_details = await self.get_product_details(sku=product_sku)

        try:
            product_prices = product_details["sizes"][0]["price"]
            if product_prices:
                product_original_price = Decimal((product_prices.get("basic") or 0) / 100)
                product_current_price = Decimal((product_prices.get("product") or 0) / 100)
        except Exception as e:
            logger.warning(f"Failed to get product prices from WB #{product_sku}: {e}")

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
    crawler = WildberriesCrawler()
    product_card = await crawler.parse("https://www.wildberries.ru/catalog/17386348/detail.aspx")
    print(json.dumps(product_card.to_dict(), indent=2, ensure_ascii=False))


if __name__ == '__main__':
    asyncio.run(main())
