import os
import re
import json
import time
import asyncio
from typing import ClassVar
from loguru import logger
from decimal import Decimal
from multiprocessing import Process, Queue
from curl_cffi.requests.session import AsyncSession

from config import data_path
from .base import BaseCrawler
from models import ProductCard
from utils.helpers import parse_url
from core.exceptions import ProductCardParsingException


class WildberriesCrawler(BaseCrawler):
    marketplace_name: ClassVar[str] = "wildberries"
    url_pattern: ClassVar[str] = r"wildberries\.(ru|by|kz)"

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

        vol_number = int(sku) // 100000
        part_number = int(sku) // 1000

        route_map = self.upstreams_data["recommend"]["mediabasket_route_map"][0]
        hosts = route_map["hosts"]
        method = route_map.get("method", "mod")

        if method == "mod":
            host_entry = hosts[vol_number % len(hosts)]
            upstream_host = host_entry["host"]
        else:
            upstream_host = hosts[0]["host"]

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
            'accept': (
                'text/html,application/xhtml+xml,application/xml;q=0.9,'
                'image/avif,image/webp,image/apng,*/*;q=0.8,application/signed-exchange;v=b3;q=0.7'
            ),
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
            'user-agent': (
                'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 '
                '(KHTML, like Gecko) Chrome/138.0.0.0 Safari/537.36'
            ),
        }
        return headers

    async def get_product_details(self, sku: str):
        product_data = None

        # Try multiple API versions/param combos — WB migrates endpoints frequently
        base_params = f"appType=1&curr=rub&dest=-1257786&spp=30&nm={sku}"
        slim_params = f"dest=-1257786&nm={sku}"
        attempts = [
            ("v2", base_params),
            ("v3", base_params),
            ("v1", base_params),
            ("v2", slim_params),
            ("v3", slim_params),
            # no-version fallback
            (None, base_params),
        ]
        for version, params in attempts:
            path = f"cards/{version}/detail" if version else "cards/detail"
            url = f"https://card.wb.ru/{path}?{params}"
            try:
                response = await self.session.get(url=url, headers=self.get_headers())

                if response.status_code == 404:
                    logger.debug(f"WB details {path} → 404 for #{sku}, trying next")
                    continue

                if response.status_code != 200:
                    logger.warning(f"WB details {path} HTTP {response.status_code} for #{sku}")
                    continue

                raw = response.text
                if not raw:
                    logger.warning(f"WB details {path} empty body for #{sku}")
                    continue

                data = response.json()
                if "data" in data:
                    products = data["data"]["products"]
                    if products:
                        product_data = products[0]
                        logger.debug(f"WB details fetched via {path} for #{sku}")
                        break
            except Exception as e:
                logger.warning(f"WB details {path} failed for #{sku}: {e}")

        return product_data

    async def get_prices_from_search_api(self, sku: str) -> tuple[Decimal, Decimal]:
        """Fallback: use WB search API which returns priceU/salePriceU in kopecks."""
        try:
            params = (
                f"appType=1&curr=rub&dest=-1257786&query={sku}"
                f"&resultset=catalog&limit=1&spp=30"
            )
            response = await self.session.get(
                url=f"https://search.wb.ru/exactmatch/ru/common/v9/search?{params}",
                headers=self.get_headers(),
            )
            if response.status_code != 200:
                logger.debug(f"WB search API HTTP {response.status_code} for #{sku}")
                return Decimal(0), Decimal(0)

            data = response.json()
            products = data.get("data", {}).get("products", [])
            if not products:
                logger.debug(f"WB search API: no products found for #{sku}")
                return Decimal(0), Decimal(0)

            product = products[0]
            # priceU / salePriceU are in kopecks (× 100)
            original_price = Decimal((product.get("priceU") or 0) / 100)
            current_price = Decimal((product.get("salePriceU") or 0) / 100)
            if current_price:
                logger.debug(f"WB search API prices for #{sku}: current={current_price} original={original_price}")
            else:
                logger.debug(f"WB search API: prices empty for #{sku}")
            return original_price, current_price
        except Exception as e:
            logger.warning(f"WB search API failed for #{sku}: {e}")
            return Decimal(0), Decimal(0)

    @staticmethod
    def _process_get_prices(url: str, result_queue: Queue):
        """Run in a separate process: open WB product page via Patchright, extract prices from DOM.
        Uses headed browser (headless=False) to pass WB's JS challenge detection.
        """
        try:
            import time
            from patchright.sync_api import sync_playwright

            with sync_playwright() as p:
                context = p.chromium.launch_persistent_context(
                    user_data_dir="",
                    headless=False,
                )
                page = context.new_page()
                page.goto(url, wait_until="domcontentloaded", timeout=30000)

                # WB shows "Почти готово..." JS challenge, then loads the real product page.
                # Wait for the price block or product title to appear.
                try:
                    page.wait_for_selector('[class*="priceBlock"], [class*="productPage"], h1', timeout=20000)
                except Exception:
                    pass

                # Extra settle time for price JS to render
                time.sleep(2)

                # Current price: <ins> inside the price block
                # Original (old) price: element with class containing "strikethrough" or "OldPrice"
                current_text = page.evaluate('''() => {
                    const block = document.querySelector('[class*="priceBlock--"]');
                    if (block) {
                        const ins = block.querySelector('ins');
                        if (ins) return ins.textContent;
                    }
                    const sel = '[class*="priceBlockPrice"] ins, [class*="priceBlockFinalPrice"]';
                    const ins = document.querySelector(sel);
                    return ins ? ins.textContent : null;
                }''')
                original_text = page.evaluate('''() => {
                    const oldSel = '[class*="body-strikethrough"], [class*="OldPrice--"], [class*="oldPrice--"]';
                    const el = document.querySelector(oldSel);
                    return el ? el.textContent : null;
                }''')
                context.close()

            result_queue.put({"success": True, "current": current_text, "original": original_text})
        except Exception as e:
            result_queue.put({"success": False, "error": str(e)})

    async def get_prices_via_browser(self, url: str) -> tuple[Decimal, Decimal]:
        """Last-resort fallback: use Patchright to render the WB page and read prices from DOM.
        Necessary because card.wb.ru requires a JavaScript Proof-of-Work challenge.
        """
        def _parse_price(text) -> Decimal:
            if not text:
                return Decimal(0)
            cleaned = re.sub(r"[^\d]", "", text)
            return Decimal(cleaned) if cleaned else Decimal(0)

        try:
            result_queue: Queue[dict] = Queue()
            process = Process(
                target=self._process_get_prices,
                args=(url.split("?")[0], result_queue),
            )
            process.start()

            while process.is_alive():
                await asyncio.sleep(0.2)
            process.join(timeout=40)

            if not result_queue.empty():
                result = result_queue.get()
                if result["success"]:
                    current = _parse_price(result["current"])
                    original = _parse_price(result["original"])
                    if current:
                        logger.debug(f"WB browser prices: current={current} original={original}")
                    else:
                        logger.warning(f"WB browser: price elements not found in DOM for {url}")
                    return original, current
                else:
                    logger.warning(f"WB browser process error: {result['error']}")
            else:
                logger.warning("WB browser process returned no result")
        except Exception as e:
            logger.warning(f"WB browser price extraction failed: {e}")

        return Decimal(0), Decimal(0)

    @staticmethod
    def _process_get_links(url: str, result_queue: Queue):
        """Browser-based product link extraction for catalog/brand/search pages."""
        try:
            import time
            from patchright.sync_api import sync_playwright

            with sync_playwright() as p:
                context = p.chromium.launch_persistent_context(
                    user_data_dir="",
                    headless=False,
                )
                page = context.new_page()
                page.goto(url, wait_until="domcontentloaded", timeout=30000)

                try:
                    page.wait_for_selector('[class*="product-card"]', timeout=15000)
                except Exception:
                    pass

                all_links: set = set()
                for _ in range(20):
                    hrefs = page.evaluate('''() => {
                        const links = [];
                        document.querySelectorAll('a[href*="/catalog/"][href*="/detail.aspx"]').forEach(el => {
                            if (el.href) links.push(el.href.split("?")[0]);
                        });
                        return [...new Set(links)];
                    }''')
                    prev_count = len(all_links)
                    all_links.update(hrefs)
                    page.evaluate("window.scrollTo(0, document.body.scrollHeight)")
                    time.sleep(2)
                    if len(all_links) == prev_count:
                        break

                context.close()
            result_queue.put({"success": True, "links": list(all_links)})
        except Exception as e:
            result_queue.put({"success": False, "error": str(e)})

    async def _get_links_via_browser(self, url: str) -> list[str]:
        result_queue: Queue[dict] = Queue()
        process = Process(target=self._process_get_links, args=(url, result_queue))
        process.start()
        while process.is_alive():
            await asyncio.sleep(0.5)
        process.join(timeout=120)
        if not result_queue.empty():
            result = result_queue.get()
            if result.get("success"):
                return result.get("links", [])
            logger.warning(f"WB browser link extraction failed: {result.get('error')}")
        return []

    async def get_product_links(self, url: str, max_pages: int = 10) -> list[str]:
        import re as _re
        links: list[str] = []

        seller_match = _re.search(r"/seller/(\d+)", url)
        if seller_match:
            supplier_id = seller_match.group(1)
            for page in range(1, max_pages + 1):
                try:
                    api_url = (
                        f"https://catalog.wb.ru/sellers/catalog"
                        f"?appType=1&curr=rub&dest=-1257786"
                        f"&supplier={supplier_id}&limit=100&page={page}&sort=popular"
                    )
                    response = await self.session.get(api_url, headers=self.get_headers())
                    if response.status_code != 200:
                        break
                    data = response.json()
                    products = data.get("data", {}).get("products", [])
                    if not products:
                        break
                    for p in products:
                        nm_id = p.get("id")
                        if nm_id:
                            links.append(f"https://www.wildberries.ru/catalog/{nm_id}/detail.aspx")
                    logger.debug(f"WB seller {supplier_id}: page {page}, {len(products)} products")
                except Exception as e:
                    logger.warning(f"WB seller links page {page} failed: {e}")
                    break
            return links

        # Fallback: browser-based extraction for brand/catalog/search pages
        return await self._get_links_via_browser(url)

    async def parse(
            self,
            url: str,
    ) -> ProductCard:
        product_sku = None
        product_original_price: Decimal = Decimal(0)
        product_current_price: Decimal = Decimal(0)
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

        prices_extracted = False
        if product_details:
            try:
                product_prices = product_details["sizes"][0]["price"]
                if product_prices:
                    product_original_price = Decimal((product_prices.get("basic") or 0) / 100)
                    product_current_price = Decimal((product_prices.get("product") or 0) / 100)
                    prices_extracted = bool(product_current_price)
            except Exception as e:
                logger.warning(f"Failed to get product prices from WB #{product_sku}: {e}")

        if not prices_extracted:
            logger.debug(f"WB card API prices unavailable for #{product_sku}, trying search API")
            product_original_price, product_current_price = await self.get_prices_from_search_api(product_sku)
            prices_extracted = bool(product_current_price)

        if not prices_extracted:
            logger.debug(f"WB search API prices unavailable for #{product_sku}, falling back to browser")
            product_original_price, product_current_price = await self.get_prices_via_browser(url)

        card = ProductCard(
            title=product_title or "",
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
