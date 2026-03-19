import os
import re
import json
import time
import uuid
import random
import asyncio
import aiofiles  # type: ignore[import-untyped]
from typing import ClassVar
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
    marketplace_name: ClassVar[str] = "ozon"
    url_pattern: ClassVar[str] = r"ozon\.(ru|by|kz)"
    requires_cookie_lock: ClassVar[bool] = True

    cookies_file_path: str = os.path.join(data_path, "ozon_cookies.json")
    api_headers_file_path: str = os.path.join(data_path, "ozon_api_headers.json")

    def __init__(self):
        super().__init__()

        self.cookies_data = None
        self.api_headers_data = None
        self.session = AsyncSession(impersonate="chrome136")

    def is_valid_url(self, url: str) -> bool:
        return "ozon." in url

    @staticmethod
    def _process_save_cookies(url: str, cookies_file_path: str, api_headers_file_path: str, result_queue: Queue):
        try:
            from patchright.sync_api import sync_playwright

            captured_headers: dict[str, str] = {}

            with sync_playwright() as p:
                context = p.chromium.launch_persistent_context(
                    user_data_dir="",
                    headless=False,
                )

                # Intercept API requests to capture current x-o3-* headers
                def on_request(request):
                    if "entrypoint-api.bx/page/json" in request.url and not captured_headers:
                        for key, value in request.headers.items():
                            if key.startswith("x-o3-") or key == "x-kl-saas-ajax-request":
                                captured_headers[key] = value

                context.on("request", on_request)

                page = context.new_page()
                page.goto(url)

                try:
                    page.wait_for_selector("#stickyHeader", timeout=15000)
                except Exception:
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

                    if captured_headers:
                        with open(api_headers_file_path, "w", encoding="utf-8") as f:
                            json.dump(captured_headers, f, indent=2, ensure_ascii=False)

                    result_queue.put({"success": True, "cookies": cookies_data, "api_headers": captured_headers})
                else:
                    result_queue.put({"success": False, "error": "No cookies found"})

        except Exception as e:
            result_queue.put({"success": False, "error": str(e)})

    async def save_cookies(self, url: str) -> bool:
        try:
            result_queue: Queue[dict] = Queue()

            process = Process(
                target=self._process_save_cookies,
                args=(url, self.cookies_file_path, self.api_headers_file_path, result_queue)
            )
            process.start()

            while process.is_alive():
                await asyncio.sleep(0.1)

            process.join(timeout=30)

            if not result_queue.empty():
                result = result_queue.get()

                if result["success"]:
                    self.cookies_data = result["cookies"]
                    self.api_headers_data = result.get("api_headers") or {}
                    captured = len(self.api_headers_data)
                    logger.debug(f"Cookies saved successfully via process (captured {captured} API headers)")
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

    async def load_api_headers_from_file(self) -> dict:
        if not os.path.exists(self.api_headers_file_path):
            return {}
        try:
            async with aiofiles.open(self.api_headers_file_path) as f:
                return json.loads(await f.read())
        except Exception:
            return {}

    async def get_cookies(
            self,
            url: str,
    ):
        self.cookies_data = await self.load_cookies_from_file()

        if not self.cookies_data:
            await self.save_cookies(url)

        if not self.api_headers_data:
            self.api_headers_data = await self.load_api_headers_from_file()

        return self.cookies_data

    @staticmethod
    def _process_parse_via_browser(url: str, result_queue: Queue):
        """Open the product page in Patchright.
        Extracts data two ways:
          1. API interception → seo.title, pageInfo.analyticsInfo.sku, widgetStates
          2. DOM extraction  → h1 title, price elements, gallery images
        """
        try:
            from patchright.sync_api import sync_playwright

            api_data: dict = {}

            with sync_playwright() as p:
                context = p.chromium.launch_persistent_context(
                    user_data_dir="",
                    headless=False,
                )

                def on_response(response):
                    if "entrypoint-api.bx/page/json" in response.url:
                        try:
                            data = response.json()
                            ws = data.get("widgetStates", {})
                            # Keep the response with the most widgetStates
                            if len(ws) > len(api_data.get("widgetStates", {})):
                                api_data.clear()
                                api_data.update(data)
                        except Exception:
                            pass

                context.on("response", on_response)

                page = context.new_page()
                page.goto(url, wait_until="domcontentloaded", timeout=30000)

                # Wait for actual product page (not bot check)
                try:
                    page.wait_for_function(
                        '() => { const h = document.querySelector("h1"); '
                        'return h && h.textContent.length > 5 && !h.textContent.includes("не бот"); }',
                        timeout=20000,
                    )
                except Exception:
                    pass

                time.sleep(2)

                # DOM extraction
                dom = page.evaluate('''() => {
                    const h1 = document.querySelector("h1");
                    const title = h1 ? h1.textContent.trim() : null;

                    const prices = [];
                    const seen = new Set();
                    document.querySelectorAll('[data-widget="webPrice"] *').forEach(el => {
                        const t = el.textContent.trim();
                        if (t && t.length < 25 && /\\d/.test(t) && !seen.has(t)) { seen.add(t); prices.push(t); }
                    });

                    const imgs = [];
                    const imgSeen = new Set();
                    ['[data-widget="webGallery"] img', '[data-widget="webPhotoGallery"] img'].forEach(sel => {
                        document.querySelectorAll(sel).forEach(img => {
                            const s = img.src || img.dataset.src || "";
                            if (s.startsWith("http") && !imgSeen.has(s)) { imgSeen.add(s); imgs.push(s); }
                        });
                    });

                    return {title, prices, imgs};
                }''')

                context.close()

            result_queue.put({
                "success": True,
                "api_data": api_data,
                "dom": dom,
            })

        except Exception as e:
            result_queue.put({"success": False, "error": str(e)})

    async def parse_via_browser(self, url: str) -> dict | None:
        """Fallback: use Patchright to open the page and extract product data from API + DOM."""
        try:
            result_queue: Queue[dict] = Queue()
            process = Process(
                target=self._process_parse_via_browser,
                args=(url, result_queue),
            )
            process.start()

            while process.is_alive():
                await asyncio.sleep(0.2)
            process.join(timeout=50)

            if not result_queue.empty():
                result = result_queue.get()
                if result["success"]:
                    logger.debug("Ozon: browser extraction complete")
                    return result
                else:
                    logger.warning(f"Ozon browser parse failed: {result['error']}")
        except Exception as e:
            logger.warning(f"Ozon parse_via_browser exception: {e}")

        return None

    def get_headers(self, url: str):
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
            'user-agent': (
                'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 '
                '(KHTML, like Gecko) Chrome/136.0.0.0 Safari/537.36'
            ),
            'x-kl-saas-ajax-request': 'Ajax_Request',
            'x-o3-app-name': 'dweb_client',
            'x-o3-app-version': 'release_5-7-2025_0fc695b9',
            'x-o3-manifest-version': (
                'frontend-ozon-ru:0fc695b993faec2f0281eab0fb86be894dc01c12,'
                'sf-render-api:100a986a9257dfdf22e7c029f43488da3e00e497'
            ),
            'x-o3-parent-requestid': 'df7e0bc8f1281748d6bec01234df0fa9',
            'x-page-view-id': str(uuid.uuid4()),
        }
        # Override with live headers captured from the browser session
        if self.api_headers_data:
            headers.update(self.api_headers_data)
            headers['x-page-view-id'] = str(uuid.uuid4())  # always fresh
        return headers

    def _extract_from_widget_states(self, widget_states: dict, url: str) -> tuple:
        product_title = None
        product_sku = None
        product_original_price: Decimal = Decimal(0)
        product_current_price: Decimal = Decimal(0)
        product_images = []

        for widget_name, widget_data in widget_states.items():
            try:
                if "webGallery" in widget_name:
                    data = json.loads(widget_data)
                    for img in data.get("images", []):
                        product_images.append(img.get("src"))
                    if not product_sku and "sku" in data:
                        product_sku = data.get("sku")
            except Exception as e:
                logger.warning(f"Error parsing images/SKU for {url}: {e}")

            try:
                if "webProductHeading" in widget_name:
                    data = json.loads(widget_data)
                    product_title = data.get("title")
            except Exception as e:
                logger.warning(f"Error parsing title for {url}: {e}")

            try:
                if "webPrice-" in widget_name:
                    data = json.loads(widget_data)
                    orig = data.get("originalPrice")
                    if orig:
                        m_orig = re.search(r'[\d\s]+', orig)
                        product_original_price = Decimal(re.sub(r'[^\d]', '', m_orig.group())) if m_orig else Decimal(0)
                    cur = data.get("price")
                    if cur:
                        m_cur = re.search(r'[\d\s]+', cur)
                        product_current_price = Decimal(re.sub(r'[^\d]', '', m_cur.group())) if m_cur else Decimal(0)
            except Exception as e:
                logger.warning(f"Error parsing prices for {url}: {e}")

        return product_title, product_sku, product_original_price, product_current_price, product_images

    async def get_product_links(self, url: str, max_pages: int = 10) -> list[str]:
        parsed_url = parse_url(url)
        base_path = parsed_url.get("path", "")
        links: list[str] = []

        for page in range(1, max_pages + 1):
            try:
                path = base_path if page == 1 else f"{base_path}{'&' if '?' in base_path else '?'}page={page}"
                cookies = await self.get_cookies(url)
                response = await self.session.get(
                    url="https://www.ozon.ru/api/entrypoint-api.bx/page/json/v2",
                    params={"url": path},
                    cookies=cookies,
                    headers=self.get_headers(url),
                )
                if response.status_code != 200:
                    logger.warning(f"Ozon links API HTTP {response.status_code} page {page}")
                    break
                raw = response.text
                if not raw.lstrip().startswith("{"):
                    break
                data = response.json()
                widget_states = data.get("widgetStates", {})

                page_links: list[str] = []
                for widget_name, widget_data in widget_states.items():
                    lower = widget_name.lower()
                    kws = ("searchresult", "catalog2", "tile", "productsearch", "categorycolumn")
                    if not any(kw in lower for kw in kws):
                        continue
                    try:
                        wd = json.loads(widget_data)
                        for item in wd.get("items", []):
                            link = (
                                item.get("link")
                                or item.get("action", {}).get("link", "")
                                or item.get("mainAction", {}).get("link", "")
                            )
                            if link and ("/product/" in link or "/category/" in link):
                                if not link.startswith("http"):
                                    link = f"https://www.ozon.ru{link}"
                                link = link.split("?")[0]
                                page_links.append(link)
                    except Exception as e:
                        logger.debug(f"Ozon widget '{widget_name}' parse error: {e}")

                if not page_links:
                    logger.debug(f"Ozon: no links on page {page}, stopping")
                    break
                links.extend(page_links)
                logger.debug(f"Ozon: page {page} → {len(page_links)} links")
            except Exception as e:
                logger.warning(f"Ozon link extraction page {page} failed: {e}")
                break

        return list(dict.fromkeys(links))

    async def parse(
            self,
            url: str,
    ) -> ProductCard:
        parsed_url = parse_url(url)
        params = {"url": parsed_url.get("path")}

        # --- Try HTTP API with cookies first ---
        result_data = None
        for attempt in range(settings.get("max_retries", 3)):
            try:
                cookies = await self.get_cookies(url)

                response = await self.session.get(
                    url='https://www.ozon.ru/api/entrypoint-api.bx/page/json/v2',
                    params=params,
                    cookies=cookies,
                    headers=self.get_headers(url),
                )

                if response.status_code != 200:
                    logger.warning(f"Ozon API HTTP {response.status_code} for {url}")
                    raise ValueError(f"HTTP {response.status_code}")

                raw = response.text
                if not raw.lstrip().startswith("{"):
                    logger.warning(f"Ozon API non-JSON response, status={response.status_code}, start={raw[:200]!r}")
                    raise ValueError("Expecting value")

                result_data = response.json()
                logger.debug(f"Ozon API success via HTTP for {url}")
                break

            except Exception as e:
                logger.warning(f"Ozon HTTP attempt {attempt + 1} failed for {url}: {e}")
                err = str(e)
                if "Expecting value" in err or "HTTP 403" in err or "HTTP 401" in err:
                    self.cookies_data = None
                    self.delete_cookies_file()
                await asyncio.sleep(random.uniform(0.1, 1.5))

        # --- Fallback: browser-based extraction ---
        if not result_data:
            logger.debug(f"Ozon HTTP failed, falling back to browser for {url}")
            browser_result = await self.parse_via_browser(url)
        else:
            browser_result = {"success": True, "api_data": result_data, "dom": None}

        if not browser_result:
            raise ProductCardParsingException(f"Failed to get Ozon product data for {url}")

        api_data = browser_result.get("api_data") or {}
        dom = browser_result.get("dom") or {}

        # Extract from widgetStates (old-style API)
        widget_states = api_data.get("widgetStates", {})
        title, sku, orig_price, cur_price, images = self._extract_from_widget_states(widget_states, url)

        # Fill gaps from API top-level fields
        if not title:
            seo_title = api_data.get("seo", {}).get("title", "")
            # Strip trailing SEO boilerplate: "купить c доставкой на OZON..."
            title = re.sub(
                r'\s+купить\s+c?\s+доставкой.*$', '', seo_title, flags=re.IGNORECASE
            ).strip() or None
        if not sku:
            sku = str(api_data.get("pageInfo", {}).get("analyticsInfo", {}).get("sku", "")) or None

        # Fill gaps from DOM extraction
        if dom:
            if not title and dom.get("title") and "не бот" not in dom["title"]:
                title = dom["title"]
            if not images and dom.get("imgs"):
                images = dom["imgs"]
            if (not orig_price or not cur_price) and dom.get("prices"):
                prices = dom["prices"]
                nums = []
                for p in prices:
                    cleaned = re.sub(r'[^\d]', '', p)
                    if cleaned:
                        nums.append(int(cleaned))
                nums = sorted(set(nums))
                if len(nums) >= 2:
                    cur_price = Decimal(min(nums))
                    orig_price = Decimal(max(nums))
                elif len(nums) == 1:
                    cur_price = Decimal(nums[0])

        return ProductCard(
            title=title,
            sku=sku,
            original_price=orig_price,
            current_price=cur_price,
            images=images,
            source_url=url,
        )


async def main():
    crawler = OzonCrawler()
    product_card = await crawler.parse("https://ozon.ru/product/shumin-maska-dlya-sna-chernaya-1563221610")
    print(json.dumps(product_card.to_dict(), indent=2, ensure_ascii=False))


if __name__ == '__main__':
    asyncio.run(main())
