import os
import json
import time
import random
import asyncio
import aiofiles
from loguru import logger
from decimal import Decimal
from multiprocessing import Process, Queue
from curl_cffi.requests.session import AsyncSession
from playwright._impl._api_structures import ProxySettings

from .base import BaseCrawler
from models import ProductCard
from utils.helpers import parse_url
from core.managers import ProxyManager
from config import settings, data_path
from core.exceptions import ProductCardParsingException


class MegaMarketCrawler(BaseCrawler):
    cookies_file_path: str = os.path.join(data_path, "mega_market_cookies.json")

    def __init__(self):
        super().__init__()

        self.cookies_data = None
        self.session = AsyncSession(impersonate="chrome136", verify=False)

    def is_valid_url(self, url: str) -> bool:
        return "megamarket." in url

    @staticmethod
    def _process_save_cookies(url: str, proxy, cookies_file_path: str, result_queue: Queue):
        try:
            from patchright.sync_api import sync_playwright

            proxy_settings = None

            if proxy:
                parsed_proxy = ProxyManager.split_proxy_string(proxy)
                proxy_settings = ProxySettings(
                    server=f"{parsed_proxy['proxy_type']}://{parsed_proxy['server']}:{parsed_proxy['port']}",
                    username=parsed_proxy["login"],
                    password=parsed_proxy["password"],
                )

            with sync_playwright() as p:
                context = p.chromium.launch_persistent_context(
                    user_data_dir="",
                    headless=False,
                    proxy=proxy_settings
                )

                page = context.new_page()
                page.goto(url)

                try:
                    page.wait_for_selector(".catalog-buy-button__button", timeout=15000)
                except:
                    pass

                time.sleep(15)

                cookies = context.cookies()

                if cookies:
                    cookies_data = {
                        item["name"]: item["value"]
                        for item in cookies
                    }

                    with open(cookies_file_path, "w", encoding='utf-8') as f:
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
                args=(url, self.proxy, self.cookies_file_path, result_queue)
            )
            process.start()

            while process.is_alive():
                await asyncio.sleep(0.1)

            process.join(timeout=30)

            if not result_queue.empty():
                result = result_queue.get()
                if result["success"]:
                    self.cookies_data = result["cookies"]
                    logger.info("Cookies saved successfully via process")
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
        if not self.cookies_data:
            self.cookies_data = await self.load_cookies_from_file()

        if not self.cookies_data:
            await self.save_cookies(url)

        self.cookies_data.update({
            "adult_disclaimer_confirmed": "1"
        })

        return self.cookies_data

    @staticmethod
    def get_headers(url: str):
        headers = {
            'accept': 'application/json',
            'accept-language': 'ru-RU,ru;q=0.9,en-US;q=0.8,en;q=0.7',
            'cache-control': 'no-cache',
            'content-type': 'application/json',
            'origin': 'https://megamarket.ru',
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
            'x-requested-with': 'XMLHttpRequest',
        }
        return headers

    async def get_product_offers(self, url: str, sku: str):
        json_data = {
            'goodsId': sku,
            'shopInfo': {},
            'merchantId': '0',
            'addressId': None,
            'listingParams': {
                'priorDueDate': 'UNKNOWN_OFFER_DUE_DATE',
                'selectedFilters': [],
            },
            'collectionId': None,
            'requestVersion': 11,
            'deliveryFromDarkstore': False,
            'exclusiveWarehouseId': None,
            'exclusiveMerchantId': None,
            'auth': {
                'locationId': '50',
                'appPlatform': 'WEB',
                'appVersion': 0,
                'os': 'UNKNOWN_OS',
            },
        }

        response = await self.session.post(
            url="https://megamarket.ru/api/mobile/v1/catalogService/productOffers/get",
            json=json_data,
            cookies=await self.get_cookies(url),
            headers=self.get_headers(url),
        )
        result = response.json()

        if not result.get("success"):
            raise ProductCardParsingException(f"Error during get product offers: {result.get('error', 'unexpected error')}")

        offers: list = result.get("offers", [])
        offers.sort(key=lambda offer: offer.get("price"))

        return offers

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
        product_sku = parsed_url["path"].split("/")[-2].split("-")[-1].replace("/", "").strip()

        json_data = {
            "goodsId": product_sku,
            "merchantId": "0",
            "auth": {
                "locationId": "50",
                "appPlatform": "WEB",
                "appVersion": 0,
                "os": "UNKNOWN_OS",
            },
        }

        for attempt in range(settings.get("max_retries", 3)):
            try:
                if settings.get("proxy", {}).get("active", False):
                    self.proxy = await ProxyManager.get_random_proxy()

                    if self.proxy:
                        self.session.proxies = {
                            "http": self.proxy,
                            "https": self.proxy,
                        }

                response = await self.session.post(
                    url="https://megamarket.ru/api/mobile/v1/catalogService/productCardMainInfo/get",
                    json=json_data,
                    cookies=await self.get_cookies(url),
                    headers=self.get_headers(url),
                )
                result = response.json()

                if "error" in result and "Попробуйте отключить VPN" in result["error"]:
                    logger.warning(f"Bad proxy for parse MegaMarket {url}")
                    self.cookies_data = None
                    self.delete_cookies_file()
                    continue

                try:
                    if result["goods"]["images"]:
                        product_images += result["goods"]["images"]
                except Exception as e:
                    logger.warning(f"Error during parsing MegaMarket images for {url}: {e}")

                try:
                    product_title = result["goods"]["title"]
                except Exception as e:
                    logger.warning(f"Error during parsing MegaMarket title for {url}: {e}")

                try:
                    offers = await self.get_product_offers(url, product_sku)
                    if offers:
                        best_offer = offers[0]
                        product_current_price = Decimal(best_offer.get("finalPrice"))
                except Exception as e:
                    logger.warning(f"Error during parsing MegaMarket price for {url}: {e}")

                card = ProductCard(
                    title=product_title,
                    sku=product_sku,
                    original_price=product_original_price,
                    current_price=product_current_price,
                    images=product_images,
                    source_url=url
                )
                logger.debug(card.to_dict())

                return card

            except Exception as e:
                logger.warning(f"Error during parse URL {url}: {e}")
                if "Expecting value" in str(e):
                    self.cookies_data = None
                    self.delete_cookies_file()

                await asyncio.sleep(random.uniform(0.1, 1.5))

        raise ProductCardParsingException(f"Unexpected error during parse URL {url} after {settings['max_retries']} attempts")


async def main():
    card_url = "https://megamarket.ru/catalog/details/puhovik-detskiy-poivre-blanc-w21-1201-jrgl-2122-mega-pink-128-600011555038/"

    crawler = MegaMarketCrawler()
    tasks = []

    for _ in range(1):
        tasks.append(asyncio.create_task(crawler.parse(url=card_url)))

    await asyncio.gather(*tasks)
    # product_card = await crawler.parse()
    # print(json.dumps(product_card.to_dict(), indent=2, ensure_ascii=False))


if __name__ == '__main__':
    asyncio.run(main())
