import os
import re
import json
import time
import uuid
import random
import asyncio
import aiofiles
from loguru import logger
from decimal import Decimal
from multiprocessing import Process, Queue
from curl_cffi.requests.session import AsyncSession

from .base import BaseCrawler
from models import ProductCard
from utils.helpers import parse_url
from config import settings, data_path
from core.exceptions import ProductCardParsingException


class OzonCrawler(BaseCrawler):
    cookies_file_path: str = os.path.join(data_path, "ozon_cookies.json")

    def __init__(self):
        super().__init__()

        self.cookies_data = None
        self.session = AsyncSession(impersonate="chrome136")

    def is_valid_url(self, url: str) -> bool:
        return "ozon." in url

    @staticmethod
    def _process_save_cookies(url: str, cookies_file_path: str, result_queue: Queue):
        try:
            from patchright.sync_api import sync_playwright

            with sync_playwright() as p:
                context = p.chromium.launch_persistent_context(
                    user_data_dir="",
                    headless=False,
                )

                page = context.new_page()
                page.goto(url)

                try:
                    page.wait_for_selector("#stickyHeader", timeout=15000)
                except:
                    pass

                time.sleep(7)

                cookies = context.cookies()

                if cookies:
                    cookies_data = {
                        item["name"]: item["value"]
                        for item in cookies
                    }

                    with open(cookies_file_path, "w", encoding="utf-8") as f:
                        json.dump(cookies_data, f, indent=2, ensure_ascii=False)

                    result_queue.put({"success": True, "cookies": cookies_data})
                else:
                    result_queue.put({"success": False, "error": "No cookies found"})

        except Exception as e:
            result_queue.put({"success": False, "error": str(e)})

    async def save_cookies(self, url: str) -> bool:
        try:
            result_queue = Queue()

            process = Process(
                target=self._process_save_cookies,
                args=(url, self.cookies_file_path, result_queue)
            )
            process.start()

            while process.is_alive():
                await asyncio.sleep(0.1)

            process.join(timeout=30)

            if not result_queue.empty():
                result = result_queue.get()

                if result["success"]:
                    self.cookies_data = result["cookies"]
                    logger.debug("Cookies saved successfully via process")
                    return True
                else:
                    logger.error(f"Process error: {result['error']}")
                    return False
            else:
                logger.error("Process completed but no result received")
                return False

        except Exception as e:
            logger.error(f"Failed to save cookies via process: {e}")
            return False

    async def load_cookies_from_file(self):
        cookies_data = {}

        if not os.path.exists(self.cookies_file_path):
            logger.debug(f"Cookies file not found: {self.cookies_file_path}")
            return cookies_data

        try:
            async with aiofiles.open(self.cookies_file_path) as f:
                data = await f.read()
                cookies_data = json.loads(data)

                logger.debug(f"Cookies data loaded from file: {self.cookies_file_path}")
                return cookies_data
        except Exception as e:
            logger.error(f"Failed to load cookies from {self.cookies_file_path}: {e}")
            return cookies_data

    def delete_cookies_file(self):
        if os.path.exists(self.cookies_file_path):
            os.remove(self.cookies_file_path)

    async def get_cookies(
            self,
            url: str,
    ):
        self.cookies_data = await self.load_cookies_from_file()

        if not self.cookies_data:
            await self.save_cookies(url)

        return self.cookies_data

    @staticmethod
    def get_headers(url: str):
        headers = {
            'accept': 'application/json',
            'accept-language': 'ru-RU,ru;q=0.9',
            'cache-control': 'no-cache',
            'content-type': 'application/json',
            'pragma': 'no-cache',
            'priority': 'u=1, i',
            'referer': url,
            'sec-ch-ua': '"Not)A;Brand";v="8", "Chromium";v="136", "Google Chrome";v="136"',
            'sec-ch-ua-mobile': '?0',
            'sec-ch-ua-platform': '"Windows"',
            'sec-fetch-dest': 'empty',
            'sec-fetch-mode': 'cors',
            'sec-fetch-site': 'same-origin',
            'user-agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/136.0.0.0 Safari/537.36',
            'x-kl-saas-ajax-request': 'Ajax_Request',
            'x-o3-app-name': 'dweb_client',
            'x-o3-app-version': 'release_5-7-2025_0fc695b9',
            'x-o3-manifest-version': 'frontend-ozon-ru:0fc695b993faec2f0281eab0fb86be894dc01c12,sf-render-api:100a986a9257dfdf22e7c029f43488da3e00e497',
            'x-o3-parent-requestid': 'df7e0bc8f1281748d6bec01234df0fa9',
            'x-page-view-id': str(uuid.uuid4()),
        }
        return headers

    async def parse(
            self,
            url: str,
    ) -> ProductCard:
        product_title = None
        product_sku = None
        product_original_price = 0.0
        product_current_price = 0.0
        product_images = []

        parsed_url = parse_url(url)

        params = {
            "url": f"{parsed_url.get('path')}",
        }

        for attempt in range(settings.get("max_retries", 3)):
            try:
                cookies = await self.get_cookies(url)

                response = await self.session.get(
                    url='https://www.ozon.ru/api/entrypoint-api.bx/page/json/v2',
                    params=params,
                    cookies=cookies,
                    headers=self.get_headers(url)
                )
                result = response.json()

                widget_states = result.get("widgetStates", {})
                if widget_states:
                    for widget_name, widget_data in widget_states.items():
                        try:
                            if "webGallery" in widget_name:
                                web_gallery_data = json.loads(widget_data)
                                product_images_data = web_gallery_data.get("images", [])
                                for image_item in product_images_data:
                                    product_images.append(image_item.get("src"))

                                if not product_sku and "sku" in web_gallery_data:
                                    product_sku = web_gallery_data.get("sku")
                        except Exception as e:
                            logger.warning(f"Error during parsing images and SKU for {url}: {e}")

                        try:
                            if "webProductHeading" in widget_name:
                                web_product_heading_data = json.loads(widget_data)
                                product_title = web_product_heading_data.get("title")
                        except Exception as e:
                            logger.warning(f"Error during parsing title for {url}: {e}")

                        try:
                            if "webPrice-" in widget_name:
                                web_price_data = json.loads(widget_data)
                                product_original_price = web_price_data.get("originalPrice")
                                if product_original_price:
                                    product_original_price = Decimal(re.sub(r'[^\d]', '', re.search(r'[\d\s]+', product_original_price).group()))

                                product_current_price = web_price_data.get("price")
                                if product_current_price:
                                    product_current_price = Decimal(
                                        re.sub(r'[^\d]', '', re.search(r'[\d\s]+', product_current_price).group()))
                        except Exception as e:
                            logger.warning(f"Error during parsing prices for {url}: {e}")

                card = ProductCard(
                    title=product_title,
                    sku=product_sku,
                    original_price=product_original_price,
                    current_price=product_current_price,
                    images=product_images,
                    source_url=url
                )

                return card

            except Exception as e:
                logger.warning(f"Error during parse URL {url}: {e}")
                if "Expecting value" in str(e):
                    self.cookies_data = None
                    self.delete_cookies_file()

                await asyncio.sleep(random.uniform(0.1, 1.5))

        raise ProductCardParsingException(f"Unexpected error during parse URL {url} after {settings['max_retries']} attempts")


async def main():
    crawler = OzonCrawler()
    product_card = await crawler.parse("https://ozon.ru/product/shumin-maska-dlya-sna-chernaya-1563221610")
    print(json.dumps(product_card.to_dict(), indent=2, ensure_ascii=False))


if __name__ == '__main__':
    asyncio.run(main())
