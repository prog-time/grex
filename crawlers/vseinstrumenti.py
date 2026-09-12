"""Crawler for vseinstrumenti.ru (ВсеИнструменты.ру).

Strategy
--------
1. Fetch the product page HTML via HTTP (curl_cffi with Chrome impersonation).
2. Parse the JSON-LD ``application/ld+json`` script block for the main product
   data: name, description, current price, images, characteristics.
3. Extract the old/original price by parsing the Nuxt/Pinia serialised state
   embedded in the page HTML (regex-based) or from visible price span text.
4. Extract the product article code ("Код товара") from the page text.
5. Fall back to a browser subprocess when the HTTP request is blocked.

URL patterns
------------
Product pages:
    https://www.vseinstrumenti.ru/product/<slug>-<numeric-id>/
    e.g. /product/drel-shurupovert-aeg-bs18g4-202c-4935478630-1760653/

Category / catalog pages:
    https://www.vseinstrumenti.ru/category/<name>-<id>/
    e.g. /category/shurupoverty-1774/
"""
from __future__ import annotations

import asyncio
import json
import re
import time
from decimal import Decimal
from multiprocessing import Queue
from typing import ClassVar

from bs4 import BeautifulSoup, Tag
from curl_cffi.requests.session import AsyncSession
from loguru import logger

from config import settings
from core.exceptions import ProductCardParsingException
from models import ProductCard

from .base import BaseCrawler


class VseInstrumentiCrawler(BaseCrawler):
    """Crawler for vseinstrumenti.ru."""

    marketplace_name: ClassVar[str] = "vseinstrumenti"
    url_pattern: ClassVar[str] = r"vseinstrumenti\.ru"

    def __init__(self) -> None:
        super().__init__()
        self.session = AsyncSession(impersonate="chrome136")

    def is_valid_url(self, url: str) -> bool:
        return "vseinstrumenti.ru" in url

    # ------------------------------------------------------------------
    # HTTP helpers
    # ------------------------------------------------------------------

    @staticmethod
    def _build_headers(url: str) -> dict:
        return {
            "accept": (
                "text/html,application/xhtml+xml,application/xml;q=0.9,"
                "image/avif,image/webp,image/apng,*/*;q=0.8,"
                "application/signed-exchange;v=b3;q=0.7"
            ),
            "accept-language": "ru-RU,ru;q=0.9,en-US;q=0.8,en;q=0.7",
            "cache-control": "no-cache",
            "pragma": "no-cache",
            "referer": "https://www.vseinstrumenti.ru/",
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
        """Extract a Decimal value from a price string like ``'17 600 ₽'``."""
        cleaned = re.sub(r"[^\d]", "", text)
        return Decimal(cleaned) if cleaned else Decimal(0)

    @staticmethod
    def _extract_old_price(html: str, current_price: Decimal) -> Decimal:
        """Try to find the original/strikethrough price in the rendered HTML.

        vseinstrumenti.ru embeds a Nuxt/Pinia serialised state where prices
        appear as plain integers separated by commas.  We look for the pattern
        ``<current_int>,<old_int>`` and also scan all price-like text spans.
        """
        current_int = int(current_price)

        # Strategy 1 — Nuxt serialised array: look for "current_price,old_price"
        # The state embeds numbers in the format …,<cur>,<old>,…
        pattern = rf"\b{current_int}\b[,\s]{{1,10}}(\d{{4,6}})\b"
        for m in re.finditer(pattern, html):
            candidate = int(m.group(1))
            if candidate > current_int:
                return Decimal(candidate)

        # Strategy 2 — scan visible price spans for a larger price value
        soup = BeautifulSoup(html, "html.parser")
        prices: list[int] = []
        for el in soup.find_all(string=re.compile(r"\d[\d\s]*₽")):
            cleaned = re.sub(r"[^\d]", "", str(el))
            if cleaned:
                prices.append(int(cleaned))

        prices_above = sorted({p for p in prices if p > current_int})
        if prices_above:
            return Decimal(prices_above[0])

        return Decimal(0)

    # ------------------------------------------------------------------
    # SKU extraction
    # ------------------------------------------------------------------

    @staticmethod
    def _extract_sku_from_url(url: str) -> str | None:
        """Extract the trailing numeric ID from a product URL slug.

        URL format: /product/<slug>-<numeric-id>/
        """
        # Remove trailing slash, then take the last dash-separated segment
        path = url.rstrip("/").split("/")[-1]
        parts = path.split("-")
        for part in reversed(parts):
            if part.isdigit():
                return part
        return None

    @staticmethod
    def _extract_article_from_html(html: str) -> str | None:
        """Extract the internal article number ("Код товара") from the page text."""
        m = re.search(r"[Кк]од\s+товара[:\s]*(\d+)", html)
        if m:
            return m.group(1)
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
                    page.wait_for_selector("script[type='application/ld+json']", timeout=15000)
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
                    f"VseInstrumenti HTTP {response.status_code} for {url} "
                    f"(attempt {attempt + 1})"
                )
            except Exception as exc:
                logger.warning(
                    f"VseInstrumenti HTTP attempt {attempt + 1} failed for {url}: {exc}"
                )
            await asyncio.sleep(0.5 * (attempt + 1))
        return None

    async def _fetch_html_via_browser(self, url: str) -> str | None:
        """Browser fallback: renders the page and returns its HTML."""
        result = await self._run_in_process(
            self._process_parse_via_browser, (url,),
        )
        if result and result.get("success"):
            logger.debug("VseInstrumenti: browser extraction complete")
            return result["html"]
        error = (result or {}).get("error", "")
        if error:
            logger.warning(f"VseInstrumenti: browser parse failed — {error}")
        return None

    # ------------------------------------------------------------------
    # Link extraction — category / catalog pages
    # ------------------------------------------------------------------

    async def get_product_links(self, url: str, max_pages: int = 10) -> list[str]:
        """Extract product page links from a vseinstrumenti.ru category URL."""
        proxy = await self.get_proxy(self.marketplace_name)
        links: list[str] = []
        base_url = url.rstrip("/")

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
                        f"VseInstrumenti links HTTP {response.status_code} page {page}"
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
                            href = f"https://www.vseinstrumenti.ru{href}"
                        href = href.split("?")[0].rstrip("/") + "/"
                        if href not in seen:
                            seen.add(href)
                            page_links.append(href)

                if not page_links:
                    logger.debug(f"VseInstrumenti: no links on page {page}, stopping")
                    break

                links.extend(page_links)
                logger.debug(f"VseInstrumenti: page {page} → {len(page_links)} links")

            except Exception as exc:
                logger.warning(f"VseInstrumenti link extraction page {page} failed: {exc}")
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
            logger.debug(f"VseInstrumenti HTTP failed, falling back to browser for {clean_url}")
            html = await self._fetch_html_via_browser(clean_url)
        if not html:
            raise ProductCardParsingException(
                f"VseInstrumenti: failed to fetch product page for {clean_url}"
            )

        # --- Parse JSON-LD ---
        jsonld = self._parse_jsonld(html)
        if not jsonld:
            logger.warning(f"VseInstrumenti: no JSON-LD Product found for {clean_url}")

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
            # Fallback: find price in HTML spans
            price_match = re.search(r"(\d[\d\s]*)\s*₽", html)
            if price_match:
                product_current_price = self._parse_price_text(price_match.group(1))

        # Old/original price
        product_original_price: Decimal = Decimal(0)
        if product_current_price:
            product_original_price = self._extract_old_price(html, product_current_price)

        # Images
        product_images: list[str] = []
        raw_images = jsonld.get("image") or []
        if isinstance(raw_images, str):
            raw_images = [raw_images]
        for img in raw_images:
            if isinstance(img, str) and img.startswith("http"):
                product_images.append(img)

        # Characteristics from JSON-LD additionalProperty
        product_characteristics: dict[str, str] = {}
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
                product_characteristics[str(name)] = str_value

        # SKU: try URL numeric ID first, then "Код товара" from HTML
        product_sku: str | None = self._extract_sku_from_url(clean_url)
        if not product_sku:
            product_sku = self._extract_article_from_html(html)

        if not product_title:
            raise ProductCardParsingException(
                f"VseInstrumenti: could not extract product title for {clean_url}"
            )

        return ProductCard(
            title=product_title,
            sku=product_sku or "",
            original_price=product_original_price,
            current_price=product_current_price,
            images=product_images,
            source_url=url,
            description=product_description,
            characteristics=product_characteristics,
        )


async def main() -> None:
    import json as _json
    crawler = VseInstrumentiCrawler()
    product_card = await crawler.parse(
        "https://www.vseinstrumenti.ru/product/drel-shurupovert-aeg-bs18g4-202c-4935478630-1760653/"
    )
    print(_json.dumps(product_card.to_dict(), indent=2, ensure_ascii=False))


if __name__ == "__main__":
    asyncio.run(main())
