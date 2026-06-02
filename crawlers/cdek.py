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
    def _spec_table(soup: BeautifulSoup) -> dict[str, str]:
        """Structured spec table — ``.sh-product-page__spec`` rows (title/value).

        Present on electronics (e.g. laptops): Бренд, Процессор, ОЗУ, …
        Absent on fashion items, which keep their attributes elsewhere.
        """
        pairs: dict[str, str] = {}
        for row in soup.find_all(class_="sh-product-page__spec"):
            title = row.find(class_="sh-product-page__spec-title")
            value = row.find(class_="sh-product-page__spec-value")
            if title and value:
                name = title.get_text(" ", strip=True)
                val = value.get_text(" ", strip=True)
                if name and val:
                    pairs[name] = val
        return pairs

    @staticmethod
    def _strong_pairs(block: Tag) -> dict[str, str]:
        """``<strong>Label:</strong> value`` pairs inside a prose block.

        Used by accessories (e.g. watches), whose attributes are bold labels in
        a "Характеристики:" section: ``<strong>Механизм:</strong> Кварцевый …``.
        A <strong> that ends with ':' but has no following text is a section
        header (e.g. "Характеристики:") and is skipped.
        """
        pairs: dict[str, str] = {}
        for st in block.find_all("strong"):
            label = st.get_text(" ", strip=True)
            if not label.endswith(":"):
                continue
            name = label[:-1].strip()
            value = ""
            sib = st.next_sibling
            while sib is not None and getattr(sib, "name", None) not in ("strong", "br"):
                value += sib.get_text(" ", strip=True) if hasattr(sib, "get_text") else str(sib)
                sib = sib.next_sibling
            value = value.strip(" \n\t—-–")
            if name and value:
                pairs[name] = value
        return pairs

    @staticmethod
    def _kv_lines(block: Tag) -> dict[str, str]:
        """Short ``Name: Value`` lines (fashion show-more: ``Бренд: …``/``Цвет: …``).

        Guarded against prose: only short name/value lines qualify.
        """
        pairs: dict[str, str] = {}
        for line in block.get_text("\n", strip=True).split("\n"):
            name, sep, value = line.partition(":")
            name, value = name.strip(), value.strip()
            if sep and name and value and len(name) <= 30 and len(value) <= 60:
                pairs[name] = value
        return pairs

    @classmethod
    def _extract_characteristics(cls, html: str, jsonld: dict) -> dict[str, str]:
        """Extract characteristics across cdek's inconsistent layouts.

        1. Electronics — the structured ``.sh-product-page__spec`` table.
        2. Accessories — ``<strong>Label:</strong> value`` pairs in the prose
           ``.ps-document--specs`` block (watches, etc.).
        3. Fashion — short ``Name: Value`` lines in ``.show-more__text--default``.
        Falls back to the JSON-LD brand when no attribute block yields a brand.
        """
        soup = BeautifulSoup(html, "html.parser")
        characteristics = cls._spec_table(soup)

        if not characteristics:
            prose = soup.find(class_="ps-document--specs") or soup.find(class_="sh-product-page__desc")
            if isinstance(prose, Tag):
                characteristics.update(cls._strong_pairs(prose))
            show_more = soup.find(class_="show-more__text--default") or soup.find(id="show-more-text")
            if isinstance(show_more, Tag) and not characteristics:
                characteristics.update(cls._kv_lines(show_more))

        if "Бренд" not in characteristics:
            brand = jsonld.get("brand")
            if isinstance(brand, dict):
                brand = brand.get("name")
            if isinstance(brand, str) and brand.strip():
                characteristics["Бренд"] = brand.strip()

        return characteristics

    @classmethod
    def _extract_description(cls, html: str, jsonld: dict) -> str:
        """Extract the product description, excluding the characteristics blocks.

        With a structured spec table (electronics) the prose lives in
        ``.show-more__text--default``; otherwise it is the ``.ps-document--specs``
        block — truncated before any explicit "Характеристики:" section so the
        bold-label attributes don't leak into the description.
        """
        soup = BeautifulSoup(html, "html.parser")
        if soup.find(class_="sh-product-page__spec"):
            block = (
                soup.find(class_="show-more__text--default")
                or soup.find(id="show-more-text")
            )
        else:
            block = (
                soup.find(class_="ps-document--specs")
                or soup.find(class_="sh-product-page__desc")
            )
        if isinstance(block, Tag):
            text = block.get_text(separator="\n", strip=True)
            # Drop an explicit "Характеристики" section (attributes, not prose).
            text = re.split(r"\n\s*Характеристики:?\s*\n", text, maxsplit=1)[0].strip()
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

    async def _fetch_html(self, url: str) -> str | None:
        """Fetch the product page HTML. Retries rotate the proxy exit IP.

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
                    return response.text
                logger.warning(
                    f"cdek HTTP {response.status_code} for {url} (attempt {attempt + 1})"
                )
            except Exception as exc:
                logger.warning(
                    f"cdek HTTP attempt {attempt + 1} failed for {url}: {exc}"
                )
            await asyncio.sleep(0.5 * (attempt + 1))
        return None

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
        product_images: list[str] = self._extract_images(html, jsonld)
        product_description: str = self._extract_description(html, jsonld)
        product_characteristics: dict[str, str] = self._extract_characteristics(html, jsonld)
        product_sku: str = self._extract_sku_from_url(clean_url)

        if not product_title:
            raise ProductCardParsingException(
                f"cdek: could not extract product title for {clean_url}"
            )

        return ProductCard(
            title=product_title,
            sku=product_sku,
            original_price=Decimal(0),  # cdek shows no strikethrough/old price
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
