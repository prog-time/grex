"""Crawler for bigam.ru (Бигам).

Strategy
--------
1. Fetch the product page HTML via HTTP (curl_cffi with Chrome impersonation).
2. Parse the JSON-LD ``application/ld+json`` script block for the main product
   data: name, sku, current price, images.
3. Extract the old/original price from the Nuxt serialised state embedded in
   the page HTML (``fn.old=<int>`` pattern).
4. Extract characteristics from the Nuxt serialised properties object
   (``"all"`` subset, pattern: ``"<name>":{value:[...],...}``).
5. Fall back to a browser subprocess when the HTTP request is blocked.

URL patterns
------------
Product pages use regional subdomains:
    https://<region>.bigam.ru/product/<slug>-<numeric-id>/
    e.g. https://nizhniy-novgorod.bigam.ru/product/agat-l7-lyuks-...-2064429/

Category / catalog pages:
    https://<region>.bigam.ru/catalog/<category>/
    e.g. https://nizhniy-novgorod.bigam.ru/catalog/motobloki/
"""
from __future__ import annotations

import json
import re
import asyncio
import time
from typing import ClassVar
from decimal import Decimal
from multiprocessing import Queue

from bs4 import BeautifulSoup, Tag
from curl_cffi.requests.session import AsyncSession
from loguru import logger

from .base import BaseCrawler
from models import ProductCard
from config import settings
from core.exceptions import ProductCardParsingException


class BigamCrawler(BaseCrawler):
    """Crawler for bigam.ru (all regional subdomains, e.g. nizhniy-novgorod.bigam.ru)."""

    marketplace_name: ClassVar[str] = "bigam"
    url_pattern: ClassVar[str] = r"bigam\.ru"

    def __init__(self) -> None:
        super().__init__()
        self.session = AsyncSession(impersonate="chrome136")

    def is_valid_url(self, url: str) -> bool:
        return "bigam.ru" in url.lower()

    # ------------------------------------------------------------------
    # HTTP helpers
    # ------------------------------------------------------------------

    @staticmethod
    def _build_headers(url: str) -> dict:
        # Derive referer from the regional subdomain root
        m = re.match(r"(https?://[^/]+)", url)
        referer = m.group(1) + "/" if m else "https://bigam.ru/"
        return {
            "accept": (
                "text/html,application/xhtml+xml,application/xml;q=0.9,"
                "image/avif,image/webp,image/apng,*/*;q=0.8,"
                "application/signed-exchange;v=b3;q=0.7"
            ),
            "accept-language": "ru-RU,ru;q=0.9,en-US;q=0.8,en;q=0.7",
            "cache-control": "no-cache",
            "pragma": "no-cache",
            "referer": referer,
            "sec-ch-ua": '"Not)A;Brand";v="8", "Chromium";v="136", "Google Chrome";v="136"',
            "sec-ch-ua-mobile": "?0",
            "sec-ch-ua-platform": '"Windows"',
            "sec-fetch-dest": "document",
            "sec-fetch-mode": "navigate",
            "sec-fetch-site": "same-origin",
            "upgrade-insecure-requests": "1",
            "user-agent": (
                "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
                "(KHTML, like Gecko) Chrome/136.0.0.0 Safari/537.36"
            ),
        }

    # ------------------------------------------------------------------
    # JSON-LD extraction
    # ------------------------------------------------------------------

    @staticmethod
    def _parse_jsonld(html: str) -> dict:
        """Return the first JSON-LD block with ``@type == "Product"``, or ``{}``."""
        soup = BeautifulSoup(html, "html.parser")
        for script in soup.find_all("script", {"type": "application/ld+json"}):
            if not isinstance(script, Tag):
                continue
            raw = script.get_text(strip=True)
            if not raw:
                continue
            try:
                data = json.loads(raw)
            except Exception:
                continue
            if isinstance(data, dict) and data.get("@type") == "Product":
                return data
        return {}

    # ------------------------------------------------------------------
    # Price extraction helpers
    # ------------------------------------------------------------------

    @staticmethod
    def _parse_price_text(text: str) -> Decimal:
        """Extract a Decimal value from a price string like ``'44 663 ₽'``."""
        cleaned = re.sub(r"[^\d]", "", text)
        return Decimal(cleaned) if cleaned else Decimal(0)

    @staticmethod
    def _extract_old_price(html: str, current_price: Decimal) -> Decimal:
        """Extract the strikethrough/retail price from the Nuxt serialised state.

        bigam.ru embeds prices in the Nuxt state as ``fn.old=<int>`` or inside
        a price object ``{current:<n>,old:<n>,...}``.  We also fall back to
        scanning visible price text on the page.
        """
        current_int = int(current_price)

        # Strategy 1 — Nuxt state: fn.old=<price>
        m = re.search(r"\bold\s*[:=]\s*(\d{4,7})\b", html)
        if m:
            candidate = int(m.group(1))
            if candidate > current_int:
                return Decimal(candidate)

        # Strategy 2 — Nuxt state: retail=<price>
        m = re.search(r"\bretail\s*[:=]\s*(\d{4,7})\b", html)
        if m:
            candidate = int(m.group(1))
            if candidate > current_int:
                return Decimal(candidate)

        # Strategy 3 — scan visible price spans for a larger price value
        soup = BeautifulSoup(html, "html.parser")
        prices: list[int] = []
        for el in soup.find_all(string=re.compile(r"\d[\d\s]*₽")):
            cleaned = re.sub(r"[^\d]", "", str(el))
            if cleaned and len(cleaned) <= 10:
                prices.append(int(cleaned))

        prices_above = sorted({p for p in prices if p > current_int})
        if prices_above:
            return Decimal(prices_above[0])

        return Decimal(0)

    # ------------------------------------------------------------------
    # Image extraction helpers
    # ------------------------------------------------------------------

    @staticmethod
    def _extract_images(html: str, jsonld: dict) -> list[str]:
        """Extract product image URLs using multiple strategies.

        Priority:
        1. JSON-LD ``image`` field
        2. ``og:image`` meta tag
        3. Image URLs from Nuxt state (CDN pattern matching)
        4. ``<img>`` tags inside product gallery containers
        """
        images: list[str] = []
        seen: set[str] = set()

        def add(url: str) -> None:
            url = url.strip()
            if url.startswith("http") and url not in seen:
                seen.add(url)
                images.append(url)

        # Strategy 1 — JSON-LD image field
        raw_images = jsonld.get("image") or []
        if isinstance(raw_images, str):
            raw_images = [raw_images]
        for img in raw_images:
            if isinstance(img, str):
                add(img)

        # Strategy 2 — og:image meta tag
        soup = BeautifulSoup(html, "html.parser")
        for meta in soup.find_all("meta", property="og:image"):
            content = meta.get("content", "")
            if content:
                add(content)

        # Strategy 3 — image URLs in Nuxt state / inline scripts
        # bigam.ru CDN typically uses patterns like: https://...bigam.ru/.../filename.jpg
        cdn_pattern = re.compile(
            r'"(https://[^"]*bigam\.ru[^"]*\.(?:jpg|jpeg|png|webp)(?:\?[^"]*)?)"',
            re.IGNORECASE,
        )
        for m in cdn_pattern.finditer(html):
            add(m.group(1))

        # Strategy 4 — <img> tags in gallery/slider containers
        gallery_selectors = [
            {"class": re.compile(r"(gallery|slider|swiper|product.?image|photo)", re.I)},
        ]
        for selector in gallery_selectors:
            for container in soup.find_all(True, selector):
                for img_tag in container.find_all("img"):
                    for attr in ("src", "data-src", "data-lazy-src", "data-original"):
                        src = img_tag.get(attr, "")
                        if src:
                            add(src)

        return images

    # ------------------------------------------------------------------
    # Characteristics extraction
    # ------------------------------------------------------------------

    @staticmethod
    def _extract_characteristics(html: str) -> dict[str, str]:
        """Extract product characteristics from the Nuxt serialised state.

        bigam.ru stores characteristics in the Nuxt state as a JSON object
        where keys are characteristic names and values are dicts with a
        ``value`` array, e.g.::

            "Мощность, л.с.":{value:[7],description:"",important:true}

        We look for those patterns and build a flat name → value mapping.
        """
        characteristics: dict[str, str] = {}

        # Pattern: "Name":{value:[<val>],...}
        # The value array may contain strings or numbers.
        pattern = re.compile(
            r'"([^"]{2,80})":\s*\{[^{}]*"value"\s*:\s*\[([^\]]*)\]',
        )
        for match in pattern.finditer(html):
            name = match.group(1)
            raw_val = match.group(2).strip()
            if not raw_val:
                continue
            # Skip non-characteristic keys (internal Nuxt fields)
            if name in {
                "value", "description", "important", "sort", "id",
                "name", "code", "type", "url", "image", "title",
            }:
                continue
            # Decode JSON string value or number
            try:
                val = json.loads(raw_val)
                str_value = str(val) if not isinstance(val, str) else val
            except Exception:
                # Strip surrounding quotes if present
                str_value = raw_val.strip('"').strip("'")
            if str_value:
                characteristics[name] = str_value

        return characteristics

    # ------------------------------------------------------------------
    # SKU helpers
    # ------------------------------------------------------------------

    @staticmethod
    def _extract_sku_from_url(url: str) -> str | None:
        """Extract the trailing numeric ID from a product URL slug.

        URL format: /product/<slug>-<numeric-id>/
        e.g. /product/agat-l7-lyuks-...-2064429/ → "2064429"
        """
        path = url.rstrip("/").split("/")[-1]
        parts = path.split("-")
        for part in reversed(parts):
            if part.isdigit():
                return part
        return None

    # ------------------------------------------------------------------
    # Subprocess target — browser fallback
    # ------------------------------------------------------------------

    @staticmethod
    def _process_parse_via_browser(url: str, result_queue: Queue) -> None:
        """Open the product page in a browser and return page HTML."""
        try:
            from patchright.sync_api import sync_playwright  # noqa: PLC0415

            with sync_playwright() as p:
                context = p.chromium.launch_persistent_context(
                    user_data_dir="", headless=False,
                )
                page = context.new_page()
                page.goto(url, wait_until="domcontentloaded", timeout=30000)

                try:
                    page.wait_for_selector(
                        "script[type='application/ld+json']", timeout=15000
                    )
                except Exception:
                    pass

                time.sleep(2)
                html = page.content()
                context.close()

            result_queue.put({"success": True, "html": html})
        except Exception as exc:
            result_queue.put({"success": False, "error": str(exc)})

    async def _fetch_html(self, url: str) -> str | None:
        """Fetch the product page HTML. Returns ``None`` on failure."""
        proxy = await self.get_proxy(self.marketplace_name)
        max_retries = settings.get("max_retries", 3)
        for attempt in range(max_retries):
            try:
                response = await self.session.get(
                    url=url,
                    headers=self._build_headers(url),
                    proxy=proxy,
                )
                if response.status_code == 200:
                    return response.text
                logger.warning(
                    f"Bigam HTTP {response.status_code} for {url} "
                    f"(attempt {attempt + 1})"
                )
            except Exception as exc:
                logger.warning(
                    f"Bigam HTTP attempt {attempt + 1} failed for {url}: {exc}"
                )
            await asyncio.sleep(0.5 * (attempt + 1))
        return None

    async def _fetch_html_via_browser(self, url: str) -> str | None:
        """Browser fallback: renders the page and returns its HTML."""
        result = await self._run_in_process(
            self._process_parse_via_browser, (url,), timeout=50,
        )
        if result and result.get("success"):
            logger.debug("Bigam: browser extraction complete")
            return result["html"]
        error = (result or {}).get("error", "")
        if error:
            logger.warning(f"Bigam: browser parse failed — {error}")
        return None

    # ------------------------------------------------------------------
    # Link extraction — category / catalog pages
    # ------------------------------------------------------------------

    async def get_product_links(self, url: str, max_pages: int = 10) -> list[str]:
        """Extract product page links from a bigam.ru category URL.

        Builds the base URL from the regional subdomain and iterates pages
        using ``?page=<n>`` query parameter.
        """
        proxy = await self.get_proxy(self.marketplace_name)
        links: list[str] = []
        base_url = url.rstrip("/")

        # Derive the scheme+host for building absolute product URLs
        m = re.match(r"(https?://[^/]+)", url)
        host_root = m.group(1) if m else "https://bigam.ru"

        for page in range(1, max_pages + 1):
            page_url = base_url if page == 1 else f"{base_url}?page={page}"
            try:
                response = await self.session.get(
                    url=page_url,
                    headers=self._build_headers(page_url),
                    proxy=proxy,
                )
                if response.status_code != 200:
                    logger.warning(
                        f"Bigam links HTTP {response.status_code} page {page}"
                    )
                    break

                soup = BeautifulSoup(response.text, "html.parser")
                page_links: list[str] = []
                seen: set[str] = set()
                for a_tag in soup.find_all("a", href=True):
                    if not isinstance(a_tag, Tag):
                        continue
                    href: str = str(a_tag["href"])
                    if "/product/" in href:
                        if not href.startswith("http"):
                            href = f"{host_root}{href}"
                        href = href.split("?")[0].rstrip("/") + "/"
                        if href not in seen:
                            seen.add(href)
                            page_links.append(href)

                if not page_links:
                    logger.debug(f"Bigam: no links on page {page}, stopping")
                    break

                links.extend(page_links)
                logger.debug(f"Bigam: page {page} → {len(page_links)} links")

            except Exception as exc:
                logger.warning(f"Bigam link extraction page {page} failed: {exc}")
                break

        return list(dict.fromkeys(links))

    # ------------------------------------------------------------------
    # Main parse entry point
    # ------------------------------------------------------------------

    async def parse(self, url: str) -> ProductCard:
        clean_url = url.split("?")[0].rstrip("/") + "/"

        # --- Fetch HTML ---
        html = await self._fetch_html(clean_url)
        if not html:
            logger.debug(f"Bigam HTTP failed, falling back to browser for {clean_url}")
            html = await self._fetch_html_via_browser(clean_url)
        if not html:
            raise ProductCardParsingException(
                f"Bigam: failed to fetch product page for {clean_url}"
            )

        # --- Parse JSON-LD ---
        jsonld = self._parse_jsonld(html)
        if not jsonld:
            logger.warning(f"Bigam: no JSON-LD Product found for {clean_url}")

        # Title
        product_title: str = jsonld.get("name") or ""

        # Description
        product_description: str = jsonld.get("description") or ""

        # Current price from JSON-LD offers
        product_current_price: Decimal = Decimal(0)
        offers = jsonld.get("offers") or {}
        if isinstance(offers, dict):
            raw_price = offers.get("price")
            if raw_price is not None:
                try:
                    product_current_price = Decimal(str(raw_price))
                except Exception:
                    pass
        if not product_current_price:
            price_match = re.search(r"(\d[\d\s]*)\s*₽", html)
            if price_match:
                product_current_price = self._parse_price_text(price_match.group(1))

        # Old/original price — from Nuxt state
        product_original_price: Decimal = Decimal(0)
        if product_current_price:
            product_original_price = self._extract_old_price(html, product_current_price)

        # Images — multi-strategy extraction
        product_images: list[str] = self._extract_images(html, jsonld)

        # Characteristics from Nuxt state
        product_characteristics: dict[str, str] = self._extract_characteristics(html)

        # Also merge additionalProperty from JSON-LD (if present)
        for prop in jsonld.get("additionalProperty") or []:
            if not isinstance(prop, dict):
                continue
            name = prop.get("name")
            value = prop.get("value")
            unit = prop.get("unitText") or prop.get("unitCode") or ""
            if name and value is not None:
                str_value = str(value)
                if unit and unit not in str_value:
                    str_value = f"{str_value} {unit}".strip()
                product_characteristics.setdefault(str(name), str_value)

        # SKU: prefer JSON-LD sku field, fall back to URL numeric ID
        product_sku: str = jsonld.get("sku") or ""
        if not product_sku:
            product_sku = self._extract_sku_from_url(clean_url) or ""

        if not product_title:
            raise ProductCardParsingException(
                f"Bigam: could not extract product title for {clean_url}"
            )

        return ProductCard(
            title=product_title,
            sku=product_sku,
            original_price=product_original_price,
            current_price=product_current_price,
            images=product_images,
            source_url=url,
            description=product_description,
            characteristics=product_characteristics,
        )


async def main() -> None:
    import json as _json
    crawler = BigamCrawler()
    product_card = await crawler.parse(
        "https://nizhniy-novgorod.bigam.ru/product/"
        "agat-l7-lyuks-motoblok-benzinovyi-7ls-2vpered-1nazad-60-80sm-2064429/"
    )
    print(_json.dumps(product_card.to_dict(), indent=2, ensure_ascii=False))


if __name__ == "__main__":
    asyncio.run(main())
