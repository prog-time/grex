"""Crawler for citilink.ru (Ситилинк).

Strategy
--------
1. Fetch the product page HTML via HTTP (curl_cffi Chrome impersonation) through
   a residential RU proxy — citilink.ru rate-limits non-RU/datacenter IPs with
   HTTP 429, so enable the proxy pool (``proxy.active: true``).
2. Parse the ``application/ld+json`` ``Product`` block for the core data: name,
   sku, current price, images, description, brand.
3. Parse the embedded Next.js state (``<script id="__NEXT_DATA__">``) for the
   structured characteristics — a list of ``{name, value, measure}`` objects.

URL patterns
------------
Product pages:
    https://www.citilink.ru/product/<slug>-<numeric-id>/
    e.g. https://www.citilink.ru/product/televizor-led-digma-...-2024401/
"""
from __future__ import annotations

import os
import re
import json
import time
import asyncio
from typing import ClassVar
from decimal import Decimal
from multiprocessing import Queue

from bs4 import BeautifulSoup, Tag
from curl_cffi.requests.session import AsyncSession
from loguru import logger

from .base import BaseCrawler
from models import ProductCard
from config import settings, data_path
from core.exceptions import ProductCardParsingException


class CitilinkCrawler(BaseCrawler):
    """Crawler for citilink.ru (HTTP + JSON-LD + Next.js state, RU proxy required)."""

    marketplace_name: ClassVar[str] = "citilink"
    url_pattern: ClassVar[str] = r"citilink\.ru"
    # Product pages are /product/<slug>-<id>/; drop the query string and fragment.
    strip_query_params: ClassVar[bool] = True

    # Browser-seeded cookies for the Qrator-protected image CDN, cached on disk.
    cookies_file_path: ClassVar[str] = os.path.join(data_path, "citilink_cookies.json")
    cookie_ttl_seconds: ClassVar[int] = 1800  # 30 minutes

    def __init__(self) -> None:
        super().__init__()
        self.session = AsyncSession(impersonate="chrome136")

    def is_valid_url(self, url: str) -> bool:
        return "citilink.ru" in url.lower()

    # ------------------------------------------------------------------
    # HTTP helpers
    # ------------------------------------------------------------------

    @staticmethod
    def _build_headers(url: str) -> dict:
        m = re.match(r"(https?://[^/]+)", url)
        referer = m.group(1) + "/" if m else "https://www.citilink.ru/"
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

    async def _fetch_html(self, url: str) -> str | None:
        """Fetch the product page HTML. Retries rotate the proxy exit IP.

        citilink.ru returns 429 to non-RU IPs and some residential exits are
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
                    f"citilink HTTP {response.status_code} for {url} (attempt {attempt + 1})"
                )
            except Exception as exc:
                logger.warning(
                    f"citilink HTTP attempt {attempt + 1} failed for {url}: {exc}"
                )
            await asyncio.sleep(0.5 * (attempt + 1))
        return None

    # ------------------------------------------------------------------
    # JSON-LD extraction
    # ------------------------------------------------------------------

    @staticmethod
    def _is_product_node(node: object) -> bool:
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
    def _find_product_base(html: str) -> dict:
        """Return the ``productBase`` object from the Next.js state.

        It is the authoritative source for the price (``{current, old}``), the
        image gallery (each image as ``{sources: [{url}]}`` in several sizes) and
        the characteristics (``properties``). Identified as the dict that has a
        ``price`` mapping with ``current`` plus an ``images`` field.
        """
        soup = BeautifulSoup(html, "html.parser")
        node = soup.find("script", {"id": "__NEXT_DATA__"})
        if not isinstance(node, Tag):
            return {}
        try:
            data = json.loads(node.get_text())
        except Exception:
            return {}

        found: list[dict] = []

        def walk(obj: object) -> None:
            if found:
                return
            if isinstance(obj, dict):
                price = obj.get("price")
                if isinstance(price, dict) and "current" in price and "images" in obj:
                    found.append(obj)
                    return
                for v in obj.values():
                    walk(v)
            elif isinstance(obj, list):
                for item in obj:
                    walk(item)

        walk(data)
        return found[0] if found else {}

    @staticmethod
    def _to_decimal(raw: object) -> Decimal:
        if raw is None:
            return Decimal(0)
        cleaned = re.sub(r"[^\d.,]", "", str(raw)).replace(",", ".")
        try:
            return Decimal(cleaned) if cleaned else Decimal(0)
        except Exception:
            return Decimal(0)

    @classmethod
    def _extract_price(cls, base: dict, jsonld: dict) -> Decimal:
        """Current price from ``productBase.price.current``, else JSON-LD offers."""
        price = base.get("price")
        if isinstance(price, dict):
            cur = cls._to_decimal(price.get("current"))
            if cur:
                return cur
        offers = jsonld.get("offers") or {}
        if isinstance(offers, list):
            offers = offers[0] if offers else {}
        if isinstance(offers, dict):
            return cls._to_decimal(offers.get("price"))
        return Decimal(0)

    @classmethod
    def _extract_old_price(cls, base: dict) -> Decimal:
        """Strikethrough/old price from ``productBase.price.old``."""
        price = base.get("price")
        if isinstance(price, dict):
            return cls._to_decimal(price.get("old"))
        return Decimal(0)

    @classmethod
    def _extract_images(cls, base: dict, html: str, jsonld: dict) -> list[str]:
        """Gallery images at the largest available size.

        Prefers ``productBase.images[].sources`` (each image carries SM/MD/ML/XL
        variants — the XL/1200px URL is taken). Falls back to scanning the page
        for product-image URLs, then JSON-LD / og:image.
        """
        images: list[str] = []
        seen: set[str] = set()

        def add(u: str) -> None:
            u = (u or "").strip()
            if u.startswith("http") and u not in seen:
                seen.add(u)
                images.append(u)

        # 1) Structured gallery — largest source per image.
        for img in base.get("images") or []:
            if not isinstance(img, dict):
                continue
            best_url, best_w = "", -1
            for src in img.get("sources") or []:
                u = src.get("url") if isinstance(src, dict) else None
                if not u:
                    continue
                wm = re.search(r"width:(\d+)", u)
                w = int(wm.group(1)) if wm else 0
                if w > best_w:
                    best_w, best_url = w, u
            if best_url:
                add(best_url)
        if images:
            return images

        # 2) Fallback — scan all product-image URLs, largest per id.
        best: dict[str, tuple[int, str]] = {}
        order: list[str] = []
        for m in re.finditer(
            r"https://cdn\.citilink\.ru/[^\"\\ ]*?/plain/product-images/"
            r"([0-9a-f-]+)\.(?:jpg|jpeg|png|webp)",
            html,
        ):
            u, img_id = m.group(0), m.group(1)
            wm = re.search(r"width:(\d+)", u)
            w = int(wm.group(1)) if wm else 0
            if img_id not in best:
                order.append(img_id)
            if img_id not in best or w > best[img_id][0]:
                best[img_id] = (w, u)
        if order:
            return [best[i][1] for i in order]

        # 3) Fallback — JSON-LD ImageObject / og:image.
        raw_images = jsonld.get("image") or []
        if isinstance(raw_images, str):
            raw_images = [raw_images]
        for img in raw_images:
            if isinstance(img, str):
                add(img)
            elif isinstance(img, dict):
                add(str(img.get("url") or ""))
        if not images:
            soup = BeautifulSoup(html, "html.parser")
            for meta in soup.find_all("meta", property="og:image"):
                add(meta.get("content", ""))
        return images

    @staticmethod
    def _extract_characteristics(base: dict) -> dict[str, str]:
        """Characteristics from ``productBase.properties`` ({name, value, measure})."""
        characteristics: dict[str, str] = {}
        for p in base.get("properties") or []:
            if not isinstance(p, dict):
                continue
            name = str(p.get("name") or "").strip().rstrip(":")
            value = str(p.get("value") or "").strip()
            measure = str(p.get("measure") or "").strip()
            if measure and value:
                value = f"{value} {measure}".strip()
            if name and value and name not in characteristics:
                characteristics[name] = value
        return characteristics

    @staticmethod
    def _extract_sku_from_url(url: str) -> str:
        """Numeric product id from the URL slug (…-<id>/)."""
        path = url.split("?")[0].rstrip("/")
        m = re.search(r"-(\d+)$", path)
        return m.group(1) if m else ""

    # ------------------------------------------------------------------
    # Image-CDN cookies (Qrator) — captured via browser, cached on disk
    # ------------------------------------------------------------------

    def _cookies_fresh(self) -> bool:
        ttl = int(settings.get("citilink", {}).get("cookie_ttl_seconds", self.cookie_ttl_seconds))
        try:
            return (time.time() - os.path.getmtime(self.cookies_file_path)) < ttl
        except OSError:
            return False

    def _load_cookies_file(self) -> dict:
        try:
            with open(self.cookies_file_path, encoding="utf-8") as f:
                data = json.load(f)
            return data if isinstance(data, dict) else {}
        except Exception:
            return {}

    @staticmethod
    def _process_capture_cookies(
        url: str, proxy: dict | None, cookies_file_path: str, result_queue: Queue
    ) -> None:
        """Open citilink in a browser and capture its (JS-set) cookies."""
        try:
            from patchright.sync_api import sync_playwright  # noqa: PLC0415

            launch_kwargs: dict = {"user_data_dir": "", "headless": False}
            if proxy:
                launch_kwargs["proxy"] = proxy

            with sync_playwright() as p:
                context = p.chromium.launch_persistent_context(**launch_kwargs)
                page = context.new_page()
                page.goto(url, wait_until="domcontentloaded", timeout=30000)
                page.wait_for_timeout(3000)
                cookies = {c["name"]: c["value"] for c in context.cookies()}
                context.close()

            if cookies:
                with open(cookies_file_path, "w", encoding="utf-8") as f:
                    json.dump(cookies, f, ensure_ascii=False)
                result_queue.put({"success": True, "cookies": cookies})
            else:
                result_queue.put({"success": False, "error": "no cookies captured"})
        except Exception as exc:
            result_queue.put({"success": False, "error": str(exc)})

    async def _get_cookies(self) -> dict:
        """Return fresh citilink cookies, capturing via browser when stale."""
        if self._cookies_fresh():
            cookies = self._load_cookies_file()
            if cookies:
                return cookies
        proxy = self._playwright_proxy(await self.get_proxy(self.marketplace_name))
        result = await self._run_in_process(
            self._process_capture_cookies,
            ("https://www.citilink.ru/", proxy, self.cookies_file_path),
        )
        if result and result.get("success"):
            logger.debug("citilink: image-CDN cookies captured")
            return result.get("cookies") or {}
        logger.warning(f"citilink: cookie capture failed — {(result or {}).get('error')}")
        return self._load_cookies_file()

    async def download_context(self) -> dict:
        """Cookies / headers / proxy for downloading citilink CDN images.

        The image CDN is Qrator-protected and 403s unless the request carries
        browser-seeded cookies. They are captured once via a browser and cached
        (TTL), then reused for cheap curl downloads — no browser per image.
        """
        return {
            "cookies": await self._get_cookies(),
            "proxy": await self.get_proxy(self.marketplace_name),
            "headers": {
                "referer": "https://www.citilink.ru/",
                "accept": "image/avif,image/webp,image/apng,image/*,*/*;q=0.8",
            },
        }

    # ------------------------------------------------------------------
    # Main parse entry point
    # ------------------------------------------------------------------

    async def parse(self, url: str) -> ProductCard:
        clean_url = self.normalize_url(url)

        html = await self._fetch_html(clean_url)
        if not html:
            raise ProductCardParsingException(
                f"citilink: failed to fetch product page for {clean_url} "
                "(a residential RU proxy is required — enable proxy.active)"
            )

        base = self._find_product_base(html)
        jsonld = self._parse_jsonld(html)
        if not base and not jsonld:
            logger.warning(f"citilink: no product data found for {clean_url}")

        import html as _html  # noqa: PLC0415

        product_title: str = str(base.get("name") or jsonld.get("name") or "").strip()
        product_current_price: Decimal = self._extract_price(base, jsonld)
        product_original_price: Decimal = self._extract_old_price(base)
        if product_original_price and product_original_price <= product_current_price:
            product_original_price = Decimal(0)
        product_images: list[str] = self._extract_images(base, html, jsonld)

        # Description — strip HTML tags and decode entities.
        raw_desc = base.get("description") or jsonld.get("description") or ""
        product_description = re.sub(r"<[^>]+>", " ", str(raw_desc))
        product_description = _html.unescape(product_description)
        product_description = re.sub(r"\s+", " ", product_description).strip()

        # Characteristics from the spec block (Характеристики tab data).
        product_characteristics: dict[str, str] = self._extract_characteristics(base)

        # SKU: prefer productBase id / JSON-LD sku, fall back to the URL id.
        product_sku: str = (
            str(base.get("id") or jsonld.get("sku") or "").strip()
            or self._extract_sku_from_url(clean_url)
        )

        # Brand as a characteristic when present.
        brand = base.get("brand") or jsonld.get("brand")
        if isinstance(brand, dict):
            brand = brand.get("name")
        if isinstance(brand, str) and brand.strip():
            product_characteristics.setdefault("Бренд", brand.strip())

        if not product_title:
            raise ProductCardParsingException(
                f"citilink: could not extract product title for {clean_url}"
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
    crawler = CitilinkCrawler()
    product_card = await crawler.parse(
        "https://www.citilink.ru/product/"
        "televizor-led-digma-32-dm-led32sbb39-smart-android-tv-frameless-metal-2024401/"
    )
    print(_json.dumps(product_card.to_dict(), indent=2, ensure_ascii=False))


if __name__ == "__main__":
    asyncio.run(main())
