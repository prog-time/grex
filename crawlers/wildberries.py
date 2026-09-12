import asyncio
import json
import os
import re
import time
from decimal import Decimal
from multiprocessing import Queue
from typing import ClassVar

from curl_cffi.requests.session import AsyncSession
from loguru import logger

from config import data_path
from core.exceptions import ProductCardParsingException
from models import ProductCard
from utils.helpers import parse_url

from .base import BaseCrawler


class WildberriesCrawler(BaseCrawler):
    marketplace_name: ClassVar[str] = "wildberries"
    url_pattern: ClassVar[str] = r"wildberries\.(ru|by|kz)"

    upstreams_file_path = os.path.join(data_path, "wb_upstreams.json")

    def __init__(self) -> None:
        super().__init__()
        self.upstreams_data = self._load_upstreams_from_file()
        self.session = AsyncSession(impersonate="chrome136")

    def is_valid_url(self, url: str) -> bool:
        return "wildberries." in url

    # ------------------------------------------------------------------
    # CDN upstreams — needed to construct image URLs
    # ------------------------------------------------------------------

    # Refresh the cached CDN upstreams map at least this often — WB rotates
    # basket hosts, and a stale map points at dead hosts (proxy 503 / 404).
    _UPSTREAMS_TTL_SECONDS = 6 * 3600

    def _load_upstreams_from_file(self) -> dict:
        if not os.path.exists(self.upstreams_file_path):
            logger.debug(f"Upstreams file not found: {self.upstreams_file_path}")
            return {}
        try:
            age = time.time() - os.path.getmtime(self.upstreams_file_path)
            if age > self._UPSTREAMS_TTL_SECONDS:
                logger.debug(f"WB upstreams cache is stale ({age / 3600:.1f}h); will refetch")
                return {}
            with open(self.upstreams_file_path) as f:
                data = json.load(f)
                logger.debug(f"WB upstreams loaded from {self.upstreams_file_path}")
                return data
        except Exception as e:
            logger.error(f"Failed to load WB upstreams from {self.upstreams_file_path}: {e}")
            return {}

    async def _save_upstreams_data(self) -> None:
        response = await self.session.get(
            url=f"https://cdn.wbbasket.ru/api/v3/upstreams?t={int(time.time() * 1000)}",
            proxy=self.proxy,
        )
        data = response.json()
        if "recommend" in data:
            with open(self.upstreams_file_path, "w", encoding="utf-8") as f:
                json.dump(data, f, ensure_ascii=False, indent=2)
            self.upstreams_data = data
            logger.debug("WB upstreams saved")

    async def _get_upstream_host_by_sku(self, sku: str) -> str:
        if not self.upstreams_data:
            await self._save_upstreams_data()

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

    # ------------------------------------------------------------------
    # HTTP API helpers
    # ------------------------------------------------------------------

    @staticmethod
    def _build_headers() -> dict:
        return {
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

    async def _refresh_upstreams(self) -> None:
        """Force a re-fetch of the CDN upstreams map (current cache may be stale)."""
        self.upstreams_data = {}
        try:
            await self._save_upstreams_data()
        except Exception as e:  # noqa: BLE001
            logger.warning(f"WB upstreams refresh failed: {e}")

    async def _fetch_product_card(self, sku: str) -> dict | None:
        """Fetch card.json from the WB CDN, retrying with a fresh proxy on failure.

        Pool proxies intermittently return ``503 CONNECT tunnel failed``; a single
        attempt then fails the whole parse with "Product card not found". So retry
        with a re-picked proxy on any proxy/connection/5xx error. A 404 usually
        means a stale or wrong-geo upstreams host, so refresh the upstreams map
        once and retry before giving up.
        """
        from config import settings  # noqa: PLC0415

        attempts = max(3, int(settings.get("max_retries", 3)) + 2)
        refreshed = False
        for i in range(attempts):
            try:
                upstream_host = await self._get_upstream_host_by_sku(sku)
                response = await self.session.get(
                    url=f"{upstream_host}/{sku}/info/ru/card.json",
                    proxy=self.proxy,
                    timeout=20,
                )
                if response.status_code == 200:
                    return response.json()
                logger.warning(f"WB card.json HTTP {response.status_code} for #{sku} (attempt {i + 1})")
            except Exception as e:  # noqa: BLE001
                logger.warning(f"WB card.json attempt {i + 1} failed for #{sku}: {e}")

            # A failure is most often a stale/dead upstreams host (the cached CDN
            # host no longer resolves, so the proxy returns 503 CONNECT). Refresh
            # the upstreams map once; after that, rotate the proxy.
            if not refreshed:
                refreshed = True
                logger.debug(f"WB card.json failed for #{sku}; refreshing upstreams")
                await self._refresh_upstreams()
            else:
                self.proxy = await self.get_proxy(self.marketplace_name)
        logger.warning(f"Failed to get card.json from WB #{sku} after {attempts} attempts")
        return None

    async def _fetch_product_details(self, sku: str) -> dict | None:
        """Fetch price/size details from card.wb.ru (tries multiple API versions)."""
        base_params = f"appType=1&curr=rub&dest=-1257786&spp=30&nm={sku}"
        slim_params = f"dest=-1257786&nm={sku}"
        # v4 is WB's current version (v2/v3/v1 now return 404). v4 also changed
        # the response shape: products are top-level, not under a "data" key.
        attempts = [
            ("v4", base_params), ("v4", slim_params),
            ("v2", base_params), ("v3", base_params), ("v1", base_params),
            (None, base_params),
        ]
        for version, params in attempts:
            path = f"cards/{version}/detail" if version else "cards/detail"
            url = f"https://card.wb.ru/{path}?{params}"
            try:
                response = await self.session.get(url=url, headers=self._build_headers(), proxy=self.proxy)
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
                # v4: {"products": [...]}; older: {"data": {"products": [...]}}
                products = data.get("products")
                if products is None and isinstance(data.get("data"), dict):
                    products = data["data"].get("products")
                if products:
                    logger.debug(f"WB details fetched via {path} for #{sku}")
                    return products[0]
            except Exception as e:
                logger.warning(f"WB details {path} failed for #{sku}: {e}")

        return None

    async def _fetch_prices_from_search_api(self, sku: str) -> tuple[Decimal, Decimal]:
        """Fallback: WB search API returns priceU/salePriceU in kopecks."""
        try:
            params = (
                f"appType=1&curr=rub&dest=-1257786&query={sku}"
                f"&resultset=catalog&limit=1&spp=30"
            )
            response = await self.session.get(
                url=f"https://search.wb.ru/exactmatch/ru/common/v9/search?{params}",
                headers=self._build_headers(),
                proxy=self.proxy,
            )
            if response.status_code != 200:
                logger.debug(f"WB search API HTTP {response.status_code} for #{sku}")
                return Decimal(0), Decimal(0)

            data = response.json()
            products = data.get("data", {}).get("products", [])
            if not products:
                logger.debug(f"WB search API: no products for #{sku}")
                return Decimal(0), Decimal(0)

            product = products[0]
            original_price = Decimal((product.get("priceU") or 0) / 100)
            current_price = Decimal((product.get("salePriceU") or 0) / 100)
            if current_price:
                logger.debug(f"WB search API prices for #{sku}: current={current_price}")
            return original_price, current_price
        except Exception as e:
            logger.warning(f"WB search API failed for #{sku}: {e}")
            return Decimal(0), Decimal(0)

    # ------------------------------------------------------------------
    # Subprocess targets (run in a separate process via _run_in_process)
    # ------------------------------------------------------------------

    @staticmethod
    def _process_get_prices(url: str, result_queue: Queue) -> None:
        """Open the WB product page in a browser and read prices from the DOM."""
        try:
            import time as _time

            from patchright.sync_api import sync_playwright

            with sync_playwright() as p:
                context = p.chromium.launch_persistent_context(
                    user_data_dir="", headless=False,
                )
                page = context.new_page()
                page.goto(url, wait_until="domcontentloaded", timeout=30000)

                try:
                    page.wait_for_selector(
                        '[class*="priceBlock"], [class*="productPage"], h1', timeout=20000
                    )
                except Exception:
                    pass

                _time.sleep(2)

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

    @staticmethod
    def _process_get_links(url: str, result_queue: Queue) -> None:
        """Browser-based product link extraction for catalog/brand/search pages."""
        try:
            import time as _time

            from patchright.sync_api import sync_playwright

            with sync_playwright() as p:
                context = p.chromium.launch_persistent_context(
                    user_data_dir="", headless=False,
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
                    _time.sleep(2)
                    if len(all_links) == prev_count:
                        break

                context.close()
            result_queue.put({"success": True, "links": list(all_links)})
        except Exception as e:
            result_queue.put({"success": False, "error": str(e)})

    # ------------------------------------------------------------------
    # Browser fallback wrappers
    # ------------------------------------------------------------------

    async def _get_prices_via_browser(self, url: str) -> tuple[Decimal, Decimal]:
        """Last-resort price fallback: render the WB page and read prices from DOM."""
        def _parse_price(text) -> Decimal:
            if not text:
                return Decimal(0)
            cleaned = re.sub(r"[^\d]", "", text)
            return Decimal(cleaned) if cleaned else Decimal(0)

        result = await self._run_in_process(
            self._process_get_prices, (url.split("?")[0],), timeout=40,
        )
        if result and result.get("success"):
            current = _parse_price(result["current"])
            original = _parse_price(result["original"])
            if current:
                logger.debug(f"WB browser prices: current={current} original={original}")
            else:
                logger.warning(f"WB browser: price elements not found in DOM for {url}")
            return original, current
        if result:
            logger.warning(f"WB browser process error: {result.get('error')}")
        return Decimal(0), Decimal(0)

    async def _get_links_via_browser(self, url: str) -> list[str]:
        result = await self._run_in_process(
            self._process_get_links, (url,), timeout=120,
        )
        if result and result.get("success"):
            return result.get("links", [])
        if result:
            logger.warning(f"WB browser link extraction failed: {result.get('error')}")
        return []

    # ------------------------------------------------------------------
    # Link extraction
    # ------------------------------------------------------------------

    async def get_product_links(self, url: str, max_pages: int = 10) -> list[str]:
        self.proxy = await self.get_proxy(self.marketplace_name)
        links: list[str] = []

        seller_match = re.search(r"/seller/(\d+)", url)
        if seller_match:
            supplier_id = seller_match.group(1)
            for page in range(1, max_pages + 1):
                try:
                    api_url = (
                        f"https://catalog.wb.ru/sellers/catalog"
                        f"?appType=1&curr=rub&dest=-1257786"
                        f"&supplier={supplier_id}&limit=100&page={page}&sort=popular"
                    )
                    response = await self.session.get(api_url, headers=self._build_headers(), proxy=self.proxy)
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

    # ------------------------------------------------------------------
    # Main parse entry point
    # ------------------------------------------------------------------

    async def parse(self, url: str) -> ProductCard:
        self.proxy = await self.get_proxy(self.marketplace_name)
        product_original_price: Decimal = Decimal(0)
        product_current_price: Decimal = Decimal(0)
        product_images: list[str] = []

        parsed_url = parse_url(url)
        if "catalog" in parsed_url["path"] and "detail.aspx" in parsed_url["path"]:
            product_sku = str(parsed_url["path"].split("/")[2])
        else:
            raise ProductCardParsingException("Product SKU not found")

        product_card = await self._fetch_product_card(sku=product_sku)
        if not product_card:
            raise ProductCardParsingException("Product card not found")

        product_title = product_card.get("imt_name")
        product_description: str = product_card.get("description") or ""
        product_characteristics: dict = {}
        for group in product_card.get("grouped_options", []):
            for opt in group.get("options", []):
                name = opt.get("name")
                value = opt.get("value")
                if name and value is not None:
                    product_characteristics[name] = value
        upstream_host = await self._get_upstream_host_by_sku(product_sku)
        product_photo_count = product_card.get("media", {}).get("photo_count") or 0

        if product_photo_count:
            product_images = [
                f"{upstream_host}/{product_sku}/images/big/{i + 1}.webp"
                for i in range(int(product_photo_count))
            ]

        # Prices: card API → search API → browser
        product_details = await self._fetch_product_details(sku=product_sku)
        prices_extracted = False

        if product_details:
            try:
                product_prices = product_details["sizes"][0]["price"]
                if product_prices:
                    product_original_price = Decimal((product_prices.get("basic") or 0) / 100)
                    product_current_price = Decimal((product_prices.get("product") or 0) / 100)
                    prices_extracted = bool(product_current_price)
            except Exception as e:
                logger.warning(f"Failed to get prices from WB card #{product_sku}: {e}")

        if not prices_extracted:
            logger.debug(f"WB card API prices unavailable for #{product_sku}, trying search API")
            product_original_price, product_current_price = await self._fetch_prices_from_search_api(
                product_sku
            )
            prices_extracted = bool(product_current_price)

        if not prices_extracted:
            logger.debug(f"WB search API prices unavailable for #{product_sku}, falling back to browser")
            product_original_price, product_current_price = await self._get_prices_via_browser(url)

        return ProductCard(
            title=product_title or "",
            sku=product_sku,
            original_price=product_original_price,
            current_price=product_current_price,
            images=product_images,
            source_url=url,
            description=product_description,
            characteristics=product_characteristics,
        )


async def main():
    import json as _json
    crawler = WildberriesCrawler()
    product_card = await crawler.parse("https://www.wildberries.ru/catalog/17386348/detail.aspx")
    print(_json.dumps(product_card.to_dict(), indent=2, ensure_ascii=False))


if __name__ == '__main__':
    asyncio.run(main())
