import os
import json
import time
import random
import asyncio
from typing import ClassVar
from loguru import logger
from decimal import Decimal
from multiprocessing import Queue
from curl_cffi.requests.session import AsyncSession
from patchright._impl._api_structures import ProxySettings

from .base import CookieCrawler
from models import ProductCard
from utils.helpers import parse_url
from core.managers import ProxyManager
from config import settings, data_path
from core.exceptions import ProductCardParsingException


class MegaMarketCrawler(CookieCrawler):
    marketplace_name: ClassVar[str] = "megamarket"
    url_pattern: ClassVar[str] = r"megamarket\.ru"
    requires_cookie_lock: ClassVar[bool] = True

    cookies_file_path: str = os.path.join(data_path, "mega_market_cookies.json")

    def __init__(self) -> None:
        super().__init__()
        self.session = AsyncSession(impersonate="chrome136", verify=False)

    def is_valid_url(self, url: str) -> bool:
        return "megamarket." in url

    # ------------------------------------------------------------------
    # Cookie management (overrides get_cookies to inject adult disclaimer)
    # ------------------------------------------------------------------

    async def get_cookies(self, url: str) -> dict:
        if not self.cookies_data:
            self.cookies_data = await self.load_cookies_from_file()
        if not self.cookies_data:
            await self.save_cookies(url)
        if self.cookies_data:
            self.cookies_data.update({"adult_disclaimer_confirmed": "1"})
        return self.cookies_data or {}

    async def save_cookies(self, url: str) -> bool:
        result = await self._run_in_process(
            self._process_save_cookies,
            (url, self.proxy, self.cookies_file_path),
            timeout=60,
        )
        if result and result.get("success"):
            self.cookies_data = result["cookies"]
            logger.info("MegaMarket: cookies saved successfully")
            return True
        error = result.get("error", "no result") if result else "process returned nothing"
        logger.error(f"MegaMarket: cookie save failed — {error}")
        return False

    # ------------------------------------------------------------------
    # Subprocess target
    # ------------------------------------------------------------------

    @staticmethod
    def _process_save_cookies(
        url: str, proxy, cookies_file_path: str, result_queue: Queue
    ) -> None:
        """Open MegaMarket in a browser and capture session cookies."""
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
                    user_data_dir="", headless=False, proxy=proxy_settings,
                )
                page = context.new_page()
                page.goto(url)

                try:
                    page.wait_for_selector(".catalog-buy-button__button", timeout=15000)
                except Exception:
                    pass

                time.sleep(15)
                cookies = context.cookies()

                if cookies:
                    cookies_data = {item["name"]: item["value"] for item in cookies}
                    with open(cookies_file_path, "w", encoding="utf-8") as f:
                        json.dump(cookies_data, f, indent=2, ensure_ascii=False)
                    result_queue.put({"success": True, "cookies": cookies_data})
                else:
                    result_queue.put({"success": False, "error": "No cookies found"})

        except Exception as e:
            result_queue.put({"success": False, "error": str(e)})

    # ------------------------------------------------------------------
    # HTTP API helpers
    # ------------------------------------------------------------------

    @staticmethod
    def _build_headers(url: str) -> dict:
        return {
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
            'user-agent': (
                'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 '
                '(KHTML, like Gecko) Chrome/136.0.0.0 Safari/537.36'
            ),
            'x-kl-saas-ajax-request': 'Ajax_Request',
            'x-requested-with': 'XMLHttpRequest',
        }

    async def _fetch_product_offers(self, url: str, sku: str) -> list:
        """Fetch available offers (prices) for *sku* from the MegaMarket API."""
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
            headers=self._build_headers(url),
        )
        result = response.json()
        if not result.get("success"):
            raise ProductCardParsingException(
                f"productOffers error: {result.get('error', 'unexpected error')}"
            )
        offers: list = result.get("offers", [])
        offers.sort(key=lambda o: o.get("price"))
        return offers

    # ------------------------------------------------------------------
    # Link extraction
    # ------------------------------------------------------------------

    async def get_product_links(self, url: str, max_pages: int = 10) -> list[str]:
        parsed_url = parse_url(url)
        page_path = parsed_url.get("path", "")
        links: list[str] = []
        limit = 44

        for page in range(1, max_pages + 1):
            try:
                json_data = {
                    "requestVersion": 10,
                    "limit": limit,
                    "offset": (page - 1) * limit,
                    "searchByOriginalQuery": False,
                    "selectedSuggestParams": [],
                    "pageInfo": {
                        "pageUrl": page_path,
                        "pageType": "catalog",
                    },
                    "auth": {
                        "locationId": "50",
                        "appPlatform": "WEB",
                        "appVersion": 0,
                        "os": "UNKNOWN_OS",
                    },
                }
                cookies = await self.get_cookies(url)
                response = await self.session.post(
                    url="https://megamarket.ru/api/mobile/v1/catalogService/catalog/search",
                    json=json_data,
                    cookies=cookies,
                    headers=self._build_headers(url),
                )
                result = response.json()
                if not result.get("success"):
                    logger.warning(f"MegaMarket links API error: {result.get('error')}")
                    break
                items = result.get("items", [])
                if not items:
                    break
                for item in items:
                    goods = item.get("goods", {})
                    slug = goods.get("slug") or ""
                    goods_id = str(goods.get("id") or "")
                    if slug:
                        links.append(f"https://megamarket.ru/catalog/details/{slug}/")
                    elif goods_id:
                        links.append(f"https://megamarket.ru/catalog/details/{goods_id}/")
                logger.debug(f"MegaMarket: page {page} → {len(items)} items")
                if len(items) < limit:
                    break
            except Exception as e:
                logger.warning(f"MegaMarket link extraction page {page} failed: {e}")
                break

        return links

    # ------------------------------------------------------------------
    # Main parse entry point
    # ------------------------------------------------------------------

    async def parse(self, url: str) -> ProductCard:
        product_title = None
        product_original_price: Decimal = Decimal(0)
        product_current_price: Decimal = Decimal(0)
        product_images: list = []
        product_description: str = ""
        product_characteristics: dict = {}

        parsed_url = parse_url(url)
        product_sku = (
            parsed_url["path"].split("/")[-2].split("-")[-1].replace("/", "").strip()
        )

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
                self.proxy = await self.get_proxy(self.marketplace_name)
                if self.proxy:
                    self.session.proxies = {
                        "http": self.proxy,
                        "https": self.proxy,
                    }

                response = await self.session.post(
                    url="https://megamarket.ru/api/mobile/v1/catalogService/productCardMainInfo/get",
                    json=json_data,
                    cookies=await self.get_cookies(url),
                    headers=self._build_headers(url),
                )
                result = response.json()

                if "error" in result and "Попробуйте отключить VPN" in result["error"]:
                    logger.warning(f"MegaMarket blocked request (IP flagged as VPN/proxy): {url}")
                    self.cookies_data = None
                    self.delete_cookies_file()
                    continue

                goods = result.get("goods") or {}

                if not goods:
                    logger.warning(f"MegaMarket: empty goods in response for {url}")
                else:
                    logger.debug(f"MegaMarket: goods keys = {list(goods.keys())}")

                try:
                    product_title = goods.get("title") or goods.get("name") or ""
                except Exception as e:
                    logger.warning(f"Error parsing MegaMarket title for {url}: {e}")

                try:
                    raw_images = goods.get("images") or goods.get("photos") or goods.get("gallery") or []
                    for img in raw_images:
                        if isinstance(img, str) and img.startswith("http"):
                            product_images.append(img)
                        elif isinstance(img, dict):
                            src = (
                                img.get("url") or img.get("src")
                                or img.get("original") or img.get("full")
                                or img.get("zoom") or ""
                            )
                            if src and src.startswith("http"):
                                product_images.append(src)
                except Exception as e:
                    logger.warning(f"Error parsing MegaMarket images for {url}: {e}")

                try:
                    desc = (
                        goods.get("description")
                        or goods.get("fullDescription")
                        or goods.get("shortDescription")
                        or ""
                    )
                    if isinstance(desc, dict):
                        desc = desc.get("text") or desc.get("content") or desc.get("html") or ""
                    product_description = str(desc)
                except Exception as e:
                    logger.warning(f"Error parsing MegaMarket description for {url}: {e}")

                try:
                    for attr in goods.get("attributes") or goods.get("characteristics") or []:
                        name = attr.get("title") or attr.get("name")
                        value = attr.get("value") or attr.get("values")
                        if isinstance(value, list):
                            value = ", ".join(str(v) for v in value)
                        if name and value is not None:
                            product_characteristics[name] = str(value)
                except Exception as e:
                    logger.warning(f"Error parsing MegaMarket characteristics for {url}: {e}")

                try:
                    offers = await self._fetch_product_offers(url, product_sku)
                    if offers:
                        best_offer = offers[0]
                        logger.debug(f"MegaMarket: offer keys = {list(best_offer.keys())}")
                        cur = (
                            best_offer.get("finalPrice")
                            or best_offer.get("price")
                            or best_offer.get("salePrice")
                            or best_offer.get("discountPrice")
                            or 0
                        )
                        if cur:
                            product_current_price = Decimal(str(cur))
                        base = (
                            best_offer.get("basePrice")
                            or best_offer.get("originalPrice")
                            or best_offer.get("regularPrice")
                            or best_offer.get("listPrice")
                            or 0
                        )
                        if base and Decimal(str(base)) > product_current_price:
                            product_original_price = Decimal(str(base))
                except Exception as e:
                    logger.warning(f"Error parsing MegaMarket price for {url}: {e}")

                card = ProductCard(
                    title=product_title or "",
                    sku=product_sku,
                    original_price=product_original_price,
                    current_price=product_current_price,
                    images=product_images,
                    source_url=url,
                    description=product_description,
                    characteristics=product_characteristics,
                )
                logger.debug(card.to_dict())
                return card

            except Exception as e:
                logger.warning(f"Error parsing {url} (attempt {attempt + 1}): {e}")
                if "Expecting value" in str(e):
                    self.cookies_data = None
                    self.delete_cookies_file()
                await asyncio.sleep(random.uniform(0.1, 1.5))

        raise ProductCardParsingException(
            f"Failed to parse MegaMarket {url} after {settings['max_retries']} attempts"
        )


async def main():
    import json as _json
    card_url = (
        "https://megamarket.ru/catalog/details/"
        "puhovik-detskiy-poivre-blanc-w21-1201-jrgl-2122-mega-pink-128-600011555038/"
    )
    crawler = MegaMarketCrawler()
    product_card = await crawler.parse(url=card_url)
    print(_json.dumps(product_card.to_dict(), indent=2, ensure_ascii=False))


if __name__ == '__main__':
    asyncio.run(main())
