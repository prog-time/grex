import os
import re
import json
import time
import asyncio
import aiofiles  # type: ignore[import-untyped]
from typing import ClassVar
from loguru import logger
from decimal import Decimal
from multiprocessing import Process, Queue
from bs4 import BeautifulSoup, Tag
from curl_cffi.requests.session import AsyncSession

from .base import BaseCrawler
from models import ProductCard
from utils.helpers import parse_url
from config import data_path
from core.exceptions import ProductCardParsingException


class YandexMarketCrawler(BaseCrawler):
    marketplace_name: ClassVar[str] = "yandex_market"
    url_pattern: ClassVar[str] = r"market\.yandex\.(ru|by)"
    requires_cookie_lock: ClassVar[bool] = True

    cookies_file_path: str = os.path.join(data_path, "ym_cookies.json")
    browser_profile_path: str = os.path.join(data_path, "ym_browser_profile")

    def __init__(self):
        super().__init__()

        self.cookies_data = None
        self.session = AsyncSession(impersonate="chrome136")

    def is_valid_url(self, url: str) -> bool:
        return "market.yandex." in url

    @staticmethod
    def _process_save_cookies(base_url: str, cookies_file_path: str, profile_path: str, result_queue: Queue):
        try:
            from patchright.sync_api import sync_playwright

            os.makedirs(profile_path, exist_ok=True)

            with sync_playwright() as p:
                context = p.chromium.launch_persistent_context(
                    user_data_dir=profile_path,
                    headless=False,
                )

                page = context.new_page()
                page.goto(base_url, wait_until="domcontentloaded", timeout=30000)

                logger.info(
                    "YM: браузер открыт — реши капчу если появилась, страница закроется автоматически"
                )

                # Wait up to 90 seconds for captcha to be solved and product page to load
                try:
                    page.wait_for_function(
                        '() => !document.title.includes("робот") '
                        '&& !document.title.includes("not a robot") '
                        '&& document.title.length > 5',
                        timeout=90000,
                    )
                except Exception:
                    pass

                time.sleep(3)

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

    async def save_cookies(self, url: str) -> bool:
        base_url = url.split("?")[0]
        try:
            result_queue: Queue[dict] = Queue()
            process = Process(
                target=self._process_save_cookies,
                args=(base_url, self.cookies_file_path, self.browser_profile_path, result_queue),
            )
            process.start()

            while process.is_alive():
                await asyncio.sleep(0.1)

            process.join(timeout=120)

            if not result_queue.empty():
                result = result_queue.get()
                if result["success"]:
                    self.cookies_data = result["cookies"]
                    logger.debug("YM cookies saved successfully")
                    return True
                else:
                    logger.error(f"YM cookies process error: {result['error']}")
                    return False
            else:
                logger.error("YM cookies process returned no result")
                return False

        except Exception as e:
            logger.error(f"Failed to save YM cookies: {e}")
            return False

    async def load_cookies_from_file(self):
        cookies_data = {}

        if not os.path.exists(self.cookies_file_path):
            logger.debug(f"YM cookies file not found: {self.cookies_file_path}")
            return cookies_data

        try:
            async with aiofiles.open(self.cookies_file_path) as f:
                data = await f.read()
                cookies_data = json.loads(data)
                logger.debug("YM cookies loaded from file")
                return cookies_data
        except Exception as e:
            logger.error(f"Failed to load YM cookies: {e}")
            return cookies_data

    def delete_cookies_file(self):
        if os.path.exists(self.cookies_file_path):
            os.remove(self.cookies_file_path)

    async def get_cookies(self, url: str):
        self.cookies_data = await self.load_cookies_from_file()
        if not self.cookies_data:
            await self.save_cookies(url)
        return self.cookies_data

    @staticmethod
    def get_headers():
        return {
            'accept': (
                'text/html,application/xhtml+xml,application/xml;q=0.9,'
                'image/avif,image/webp,image/apng,*/*;q=0.8,application/signed-exchange;v=b3;q=0.7'
            ),
            'accept-language': 'ru-RU,ru;q=0.9',
            'cache-control': 'no-cache',
            'pragma': 'no-cache',
            'sec-ch-ua': '"Not)A;Brand";v="8", "Chromium";v="136", "Google Chrome";v="136"',
            'sec-ch-ua-mobile': '?0',
            'sec-ch-ua-platform': '"Windows"',
            'sec-fetch-dest': 'document',
            'sec-fetch-mode': 'navigate',
            'sec-fetch-site': 'none',
            'upgrade-insecure-requests': '1',
            'user-agent': (
                'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 '
                '(KHTML, like Gecko) Chrome/136.0.0.0 Safari/537.36'
            ),
        }

    @staticmethod
    def _is_captcha_page(content: bytes) -> bool:
        text = content[:2000].decode("utf-8", errors="ignore")
        return "не робот" in text or "SmartCaptcha" in text or "captcha" in text.lower()

    async def get_product_links(self, url: str, max_pages: int = 10) -> list[str]:
        links: list[str] = []

        for page in range(1, max_pages + 1):
            try:
                sep = "&" if "?" in url else "?"
                page_url = url if page == 1 else f"{url}{sep}page={page}"
                cookies = await self.get_cookies(page_url)
                response = await self.session.get(
                    url=page_url,
                    cookies=cookies,
                    headers=self.get_headers(),
                )
                if response.status_code != 200:
                    break
                if self._is_captcha_page(response.content):
                    logger.warning(f"YM captcha during link extraction page {page}, stopping")
                    self.cookies_data = None
                    self.delete_cookies_file()
                    break

                soup = BeautifulSoup(response.content, "lxml")
                page_links: list[str] = []
                seen: set[str] = set()
                for a_tag in soup.find_all("a", href=True):
                    if not isinstance(a_tag, Tag):
                        continue
                    href: str = str(a_tag["href"])
                    if "/product/" in href or "/card/" in href:
                        if not href.startswith("http"):
                            href = f"https://market.yandex.ru{href}"
                        href = href.split("?")[0]
                        if href not in seen:
                            seen.add(href)
                            page_links.append(href)

                if not page_links:
                    break
                links.extend(page_links)
                logger.debug(f"YM: page {page} → {len(page_links)} links")
            except Exception as e:
                logger.warning(f"YM link extraction page {page} failed: {e}")
                break

        return list(dict.fromkeys(links))

    async def parse(self, url: str) -> ProductCard:
        product_title = None
        product_sku = None
        product_original_price: Decimal = Decimal(0)
        product_current_price: Decimal = Decimal(0)
        product_images = []

        if "card/" not in url:
            raise ProductCardParsingException("Bad product URL: expected /card/ in path")

        clean_url = url.split("?")[0]

        try:
            parsed_url = parse_url(clean_url)
            product_sku = parsed_url["path"].split("/")[-1]
        except Exception as e:
            logger.warning(f"Failed to get SKU from YM URL {clean_url}: {e}")

        # Try HTTP request with cookies; if captcha detected → refresh cookies and retry
        from config import settings
        for attempt in range(settings.get("max_retries", 3)):
            try:
                cookies = await self.get_cookies(clean_url)

                response = await self.session.get(
                    url=clean_url,
                    cookies=cookies,
                    headers=self.get_headers(),
                )

                if response.status_code != 200:
                    logger.warning(f"YM HTTP {response.status_code} for {clean_url}")
                    raise ValueError(f"HTTP {response.status_code}")

                if self._is_captcha_page(response.content):
                    logger.warning(f"YM captcha detected for {clean_url}, refreshing cookies (attempt {attempt + 1})")
                    self.cookies_data = None
                    self.delete_cookies_file()
                    raise ValueError("captcha")

                soup = BeautifulSoup(response.content, "lxml")

                try:
                    title_el = soup.find("h1", {"data-auto": "productCardTitle"})
                    if title_el:
                        product_title = title_el.get_text(strip=True)
                except Exception as e:
                    logger.warning(f"Failed to get title from YM {clean_url}: {e}")

                try:
                    old_el = soup.find("span", {"data-auto": "snippet-price-old"})
                    if old_el:
                        raw = old_el.get_text(strip=True)
                        m_old = re.search(r'[\d\s]+', raw)
                        product_original_price = Decimal(re.sub(r'[^\d]', '', m_old.group())) if m_old else Decimal(0)
                except Exception as e:
                    logger.warning(f"Failed to get original price from YM {clean_url}: {e}")

                try:
                    cur_el = soup.find("span", {"data-auto": "snippet-price-current"})
                    if cur_el:
                        raw = cur_el.get_text(strip=True)
                        m_cur = re.search(r'[\d\s]+', raw)
                        product_current_price = Decimal(re.sub(r'[^\d]', '', m_cur.group())) if m_cur else Decimal(0)
                except Exception as e:
                    logger.warning(f"Failed to get current price from YM {clean_url}: {e}")

                try:
                    gallery_div = soup.find("div", {"data-apiary-widget-name": "@card/ShortUrl"})
                    if gallery_div and isinstance(gallery_div, Tag):
                        noframes = gallery_div.find("noframes", {"data-apiary": "patch"})
                        if noframes:
                            gallery_data = json.loads(noframes.get_text(strip=True))
                            media_items = gallery_data.get("collections", {}).get("mediaItem", {})
                            gallery_items = gallery_data.get("collections", {}).get("gallery", {})
                            if gallery_items:
                                product_id = list(gallery_items.keys())[0]
                                media_ids = gallery_items[product_id].get("mediaItems", [])
                                images_with_res = []
                                for media_id in media_ids:
                                    if media_id in media_items:
                                        info = media_items[media_id]
                                        if info.get("entity") == "avatars_picture":
                                            images_with_res.append({
                                                "url": info.get("origUrl"),
                                                "resolution": info.get("width", 0) * info.get("height", 0),
                                            })
                                images_with_res.sort(key=lambda x: x["resolution"], reverse=True)
                                product_images = [i["url"] for i in images_with_res]
                except Exception as e:
                    logger.warning(f"Failed to get images from YM {clean_url}: {e}")

                return ProductCard(
                    title=product_title or "",
                    sku=str(product_sku) if product_sku else "",
                    original_price=product_original_price,
                    current_price=product_current_price,
                    images=product_images,
                    source_url=url,
                )

            except Exception as e:
                logger.warning(f"YM parse attempt {attempt + 1} failed for {clean_url}: {e}")

        raise ProductCardParsingException(
            f"Failed to parse YM product after {settings.get('max_retries', 3)} attempts: {clean_url}"
        )


async def main():
    crawler = YandexMarketCrawler()
    product_card = await crawler.parse(
        "https://market.yandex.ru/card/espander-dlya-paltsev-ruk-kistevoy-95-kg-"
        "s-krepleniyem-na-zapyastye-tsvet---chernyy/103757317666"
    )
    print(json.dumps(product_card.to_dict(), indent=2, ensure_ascii=False))


if __name__ == '__main__':
    asyncio.run(main())
