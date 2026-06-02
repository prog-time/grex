"""Crawler for cdek.shopping (CDEK.Shopping cross-border marketplace).

Strategy
--------
1. Fetch the product page HTML via HTTP (curl_cffi with Chrome impersonation).
   A residential RU proxy is required — cdek.shopping returns HTTP 503 to
   non-RU / datacenter IPs, so enable the proxy pool (``proxy.active: true``).
2. Parse the ``application/ld+json`` block with ``@type == "Product"`` for the
   main data: name, current price, images, description, brand.
3. SKU is the numeric id from the URL path; characteristics are minimal
   (brand) — cdek does not expose a structured spec table in the SSR HTML.

URL patterns
------------
Product pages:
    https://cdek.shopping/p/<numeric-id>/<slug>
    e.g. https://cdek.shopping/p/11076395/krossovki-tommy-hilfiger-...
"""
from __future__ import annotations

import re
import asyncio
import json
from typing import ClassVar
from decimal import Decimal

from bs4 import BeautifulSoup, Tag
from curl_cffi.requests.session import AsyncSession
from loguru import logger

from .base import BaseCrawler
from models import ProductCard
from config import settings
from core.exceptions import ProductCardParsingException


class CdekCrawler(BaseCrawler):
    """Crawler for cdek.shopping (HTTP + JSON-LD, residential RU proxy required)."""

    marketplace_name: ClassVar[str] = "cdek"
    url_pattern: ClassVar[str] = r"cdek\.shopping"
    # The numeric id is canonical — cdek 301-redirects /p/<id> (and any wrong
    # slug) to the real /p/<id>/<slug>. Collapse every variant to /p/<id> so the
    # slug never produces duplicate results; the fetch follows the redirect.
    canonical_url_pattern: ClassVar[str] = r"(https?://[^/]+/p/\d+)"
    strip_query_params: ClassVar[bool] = True

    def __init__(self) -> None:
        super().__init__()
        self.session = AsyncSession(impersonate="chrome136")

    def is_valid_url(self, url: str) -> bool:
        return "cdek.shopping" in url.lower()

    # ------------------------------------------------------------------
    # HTTP helpers
    # ------------------------------------------------------------------

    @staticmethod
    def _build_headers(url: str) -> dict:
        m = re.match(r"(https?://[^/]+)", url)
        referer = m.group(1) + "/" if m else "https://cdek.shopping/"
        return {
            "accept": (
                "text/html,application/xhtml+xml,application/xml;q=0.9,"
                "image/avif,image/webp,image/apng,*/*;q=0.8"
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
            "sec-fetch-site": "none",
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
    def _is_product_node(node: object) -> bool:
        """True when *node* is a JSON-LD object whose ``@type`` is ``Product``."""
        if not isinstance(node, dict):
            return False
        node_type = node.get("@type")
        if isinstance(node_type, str):
            return node_type == "Product"
        if isinstance(node_type, list):
            return "Product" in node_type
        return False

    @classmethod
    def _find_product_node(cls, data: object) -> dict:
        """Recursively search a parsed JSON-LD value for the ``Product`` node."""
        if cls._is_product_node(data):
            return data  # type: ignore[return-value]
        if isinstance(data, dict):
            graph = data.get("@graph")
            if isinstance(graph, list):
                found = cls._find_product_node(graph)
                if found:
                    return found
        if isinstance(data, list):
            for item in data:
                found = cls._find_product_node(item)
                if found:
                    return found
        return {}

    @classmethod
    def _parse_jsonld(cls, html: str) -> dict:
        """Return the JSON-LD ``Product`` node, or ``{}``."""
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
            product = cls._find_product_node(data)
            if product:
                return product
        return {}

    # ------------------------------------------------------------------
    # Field extraction helpers
    # ------------------------------------------------------------------

    @staticmethod
    def _extract_price(jsonld: dict) -> Decimal:
        """Current price from JSON-LD ``offers`` (offers may be a dict or list)."""
        offers = jsonld.get("offers") or {}
        if isinstance(offers, list):
            offers = offers[0] if offers else {}
        if isinstance(offers, dict):
            raw = offers.get("price")
            if raw is not None:
                try:
                    return Decimal(str(raw).replace(" ", "").replace(",", "."))
                except Exception:
                    pass
        return Decimal(0)

    @staticmethod
    def _extract_old_price(html: str) -> Decimal:
        """Old/strikethrough price from ``.sh-product-page__old-price`` (``'22 109₽'``).

        Present only when the item is discounted; absent otherwise.
        """
        soup = BeautifulSoup(html, "html.parser")
        el = soup.find(class_="sh-product-page__old-price")
        if isinstance(el, Tag):
            digits = re.sub(r"[^\d]", "", el.get_text(strip=True))
            if digits:
                return Decimal(digits)
        return Decimal(0)

    @staticmethod
    def _extract_images(html: str, jsonld: dict) -> list[str]:
        """Image URLs from the JSON-LD ``image`` field, falling back to og:image."""
        images: list[str] = []
        seen: set[str] = set()

        def add(url: str) -> None:
            url = (url or "").strip()
            if url.startswith("http") and url not in seen:
                seen.add(url)
                images.append(url)

        raw_images = jsonld.get("image") or []
        if isinstance(raw_images, str):
            raw_images = [raw_images]
        for img in raw_images:
            if isinstance(img, str):
                add(img)

        if not images:
            soup = BeautifulSoup(html, "html.parser")
            for meta in soup.find_all("meta", property="og:image"):
                add(meta.get("content", ""))

        return images

    @staticmethod
    def _extract_description(html: str, jsonld: dict) -> str:
        """Return the full product description.

        cdek keeps a product's specifications as plain text inside the same
        ``.ps-document--specs`` block (the spec table on electronics is nested
        there too), so the description deliberately includes them. Per project
        decision, cdek characteristics are NOT parsed into a separate field.
        """
        soup = BeautifulSoup(html, "html.parser")
        block = (
            soup.find(class_="ps-document--specs")
            or soup.find(class_="sh-product-page__desc")
            or soup.find(class_="show-more__text--default")
            or soup.find(id="show-more-text")
        )
        if isinstance(block, Tag):
            text = block.get_text(separator="\n", strip=True)
            if text:
                return text
        return (jsonld.get("description") or "").strip()

    @staticmethod
    def _extract_sku_from_url(url: str) -> str:
        """Numeric product id from the URL path (/p/<id>/<slug>)."""
        m = re.search(r"/p/(\d+)", url)
        return m.group(1) if m else ""

    # ------------------------------------------------------------------
    # Fetch
    # ------------------------------------------------------------------

    async def _fetch_html_and_url(self, url: str) -> tuple[str | None, str | None]:
        """Fetch HTML and the final (post-redirect) URL. Retries rotate the proxy.

        cdek.shopping returns 503 to non-RU IPs and some residential exits are
        flagged, so retries with a fresh proxy materially improve success.
        """
        max_retries = settings.get("max_retries", 3)
        for attempt in range(max_retries):
            proxy = await self.get_proxy(self.marketplace_name)
            try:
                response = await self.session.get(
                    url=url,
                    headers=self._build_headers(url),
                    proxy=proxy,
                )
                if response.status_code == 200:
                    return response.text, str(response.url)
                logger.warning(
                    f"cdek HTTP {response.status_code} for {url} (attempt {attempt + 1})"
                )
            except Exception as exc:
                logger.warning(
                    f"cdek HTTP attempt {attempt + 1} failed for {url}: {exc}"
                )
            await asyncio.sleep(0.5 * (attempt + 1))
        return None, None

    async def _fetch_html(self, url: str) -> str | None:
        html, _ = await self._fetch_html_and_url(url)
        return html

    # ------------------------------------------------------------------
    # Link extraction — category / brand listing pages
    # ------------------------------------------------------------------

    async def get_product_links(self, url: str, max_pages: int = 10) -> list[str]:
        """Extract product page links from a cdek listing URL.

        Listing pages (brand ``/m/<id>``, category ``/c/<id>``) link products as
        ``/p/<id>/<slug>`` and paginate with ``?page=<n>``. Pagination only works
        on the *slugged* URL (``/m/<id>/<slug>``); ``/m/<id>`` alone ignores
        ``?page``. So page 1 is resolved via the redirect to obtain the canonical
        slugged base, and subsequent pages paginate from it. Product links are
        normalised to ``/p/<id>`` and deduplicated across pages.
        """
        m = re.match(r"(https?://[^/]+)", url)
        host_root = m.group(1) if m else "https://cdek.shopping"

        links: list[str] = []
        seen: set[str] = set()
        base_url = url.split("#")[0].split("?")[0].rstrip("/")
        for page in range(1, max_pages + 1):
            if page == 1:
                html, final_url = await self._fetch_html_and_url(base_url)
                if final_url:  # follow redirect to the canonical slugged URL
                    base_url = final_url.split("?")[0].rstrip("/")
            else:
                html = await self._fetch_html(f"{base_url}?page={page}")
            if not html:
                logger.warning(f"cdek: failed to fetch listing page {page} of {base_url}")
                break

            soup = BeautifulSoup(html, "html.parser")
            page_links: list[str] = []
            for a in soup.find_all("a", href=True):
                href = str(a["href"])
                if not re.search(r"/p/\d+", href):
                    continue
                if not href.startswith("http"):
                    href = host_root + href
                href = self.normalize_url(href)
                if href not in seen:
                    seen.add(href)
                    page_links.append(href)

            if not page_links:
                logger.debug(f"cdek: no new product links on page {page}, stopping")
                break

            links.extend(page_links)
            logger.debug(f"cdek: listing page {page} → {len(page_links)} links")

        return links

    # ------------------------------------------------------------------
    # Main parse entry point
    # ------------------------------------------------------------------

    async def parse(self, url: str) -> ProductCard:
        clean_url = self.normalize_url(url)

        html = await self._fetch_html(clean_url)
        if not html:
            raise ProductCardParsingException(
                f"cdek: failed to fetch product page for {clean_url} "
                "(a residential RU proxy is required — enable proxy.active)"
            )

        jsonld = self._parse_jsonld(html)
        if not jsonld:
            logger.warning(f"cdek: no JSON-LD Product found for {clean_url}")

        product_title: str = (jsonld.get("name") or "").strip()
        product_current_price: Decimal = self._extract_price(jsonld)
        product_original_price: Decimal = self._extract_old_price(html)
        if product_original_price and product_original_price <= product_current_price:
            product_original_price = Decimal(0)
        product_images: list[str] = self._extract_images(html, jsonld)
        product_description: str = self._extract_description(html, jsonld)
        product_sku: str = self._extract_sku_from_url(clean_url)
        # cdek characteristics are intentionally not parsed separately — they
        # stay as part of the description text.
        product_characteristics: dict[str, str] = {}

        if not product_title:
            raise ProductCardParsingException(
                f"cdek: could not extract product title for {clean_url}"
            )

        return ProductCard(
            title=product_title,
            sku=product_sku,
            original_price=product_original_price,
            current_price=product_current_price,
            images=product_images,
            source_url=clean_url,
            description=product_description,
            characteristics=product_characteristics,
        )


async def main() -> None:
    import json as _json
    crawler = CdekCrawler()
    product_card = await crawler.parse(
        "https://cdek.shopping/p/11076395/"
        "krossovki-tommy-hilfiger-essential-leather-lace-up-belyi"
    )
    print(_json.dumps(product_card.to_dict(), indent=2, ensure_ascii=False))


if __name__ == "__main__":
    asyncio.run(main())
