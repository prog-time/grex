"""Crawler for mm.ru (бывший KazanExpress, Vue.js SPA).

Strategy
--------
1. Fetch via HTTP (curl_cffi Chrome impersonation) — works on Russian IPs.
2. Fall back to patchright browser if HTTP is blocked (WAF on non-RU IPs).
3. Parse rendered HTML with BeautifulSoup using data-test-id selectors.

HTML structure (confirmed from live page)
-----------------------------------------
Title:        h1[data-test-id="text__product-name"]
Current price:[data-test-id="text__product-price"]
Old price:    [data-test-id="text__product-old-price"]
Images:       [data-test-id="list__vertical-photo-carousel"] img[src]
              → replace t_product_240_low with t_product_high for full-res
Description:  .short-description .description  (main text)
              .short-description .list .item    (bullet points)
Characteristics:
  .characteristics .item
    .subtitle.short        → group label
    .characteristic .name  → attr name
    .characteristic .value → attr value

URL patterns
------------
Product: https://mm.ru/product/<slug>-<product_id>?skuId=<sku_id>
Category: https://mm.ru/category/<slug>-<category_id>?page=<n>
"""
from __future__ import annotations

import asyncio
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


class MmCrawler(BaseCrawler):
    """Crawler for mm.ru (KazanExpress)."""

    marketplace_name: ClassVar[str] = "mm"
    url_pattern: ClassVar[str] = r"(?<![a-z])mm\.ru"

    def __init__(self) -> None:
        super().__init__()
        self.session = AsyncSession(impersonate="chrome136")

    def is_valid_url(self, url: str) -> bool:
        return "mm.ru" in url.lower()

    # ------------------------------------------------------------------
    # HTTP helpers
    # ------------------------------------------------------------------

    @staticmethod
    def _build_headers() -> dict:
        return {
            "accept": (
                "text/html,application/xhtml+xml,application/xml;q=0.9,"
                "image/avif,image/webp,*/*;q=0.8"
            ),
            "accept-language": "ru-RU,ru;q=0.9",
            "cache-control": "no-cache",
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

    async def _fetch_html_http(self, url: str) -> str | None:
        proxy = await self.get_proxy(self.marketplace_name)
        for attempt in range(settings.get("max_retries", 3)):
            try:
                response = await self.session.get(
                    url=url, headers=self._build_headers(), proxy=proxy,
                )
                if response.status_code == 200:
                    return response.text
                logger.debug(f"mm.ru HTTP {response.status_code} (attempt {attempt + 1})")
            except Exception as exc:
                logger.debug(f"mm.ru HTTP attempt {attempt + 1} failed: {exc}")
            await asyncio.sleep(0.5 * (attempt + 1))
        return None

    # ------------------------------------------------------------------
    # Browser fallback
    # ------------------------------------------------------------------

    @staticmethod
    def _process_fetch_page(
        url: str, proxy: dict | None, result_queue: Queue
    ) -> None:
        """Open mm.ru in a fresh browser context and return rendered HTML.

        Uses an ephemeral profile (``user_data_dir=""``) rather than a shared
        persistent one: a persistent profile gets locked when a timed-out run
        leaves an orphaned Chromium, which then hangs every subsequent launch,
        and it also prevents concurrent parses. A residential RU proxy provides
        the access the warm profile used to.
        """
        try:
            from patchright.sync_api import sync_playwright  # noqa: PLC0415

            launch_kwargs: dict = {
                "user_data_dir": "",
                "headless": False,
            }
            if proxy:
                launch_kwargs["proxy"] = proxy

            with sync_playwright() as p:
                context = p.chromium.launch_persistent_context(**launch_kwargs)
                page = context.new_page()
                page.goto(url, wait_until="domcontentloaded", timeout=30000)

                # Wait for Vue.js to render the product card
                try:
                    page.wait_for_selector(
                        'h1[data-test-id="text__product-name"]',
                        timeout=20000,
                    )
                except Exception:
                    # For category pages or slow loads — wait a bit more
                    time.sleep(3)

                # Scroll through the page so lazily-rendered sections
                # (characteristics, extra gallery images) mount before capture.
                try:
                    for y in range(0, 6000, 800):
                        page.evaluate(f"() => window.scrollTo(0, {y})")
                        time.sleep(0.3)
                    page.wait_for_selector(".characteristics .characteristic", timeout=5000)
                except Exception:
                    pass

                time.sleep(1)
                html = page.content()
                context.close()

            result_queue.put({"success": True, "html": html})
        except Exception as exc:
            result_queue.put({"success": False, "error": str(exc)})

    async def _fetch_html_browser(self, url: str) -> str | None:
        proxy_dict = self._playwright_proxy(await self.get_proxy(self.marketplace_name))
        result = await self._run_in_process(
            self._process_fetch_page,
            (url, proxy_dict),
            timeout=90,
        )
        if result and result.get("success"):
            return result["html"]
        error = (result or {}).get("error", "")
        if error:
            logger.warning(f"mm.ru: browser fetch failed — {error}")
        return None

    async def _fetch_html(self, url: str) -> str | None:
        # HTTP first — works on Russian IPs; mm.ru is Vue.js SPA so check
        # for a rendered marker rather than accepting any 200 response
        html = await self._fetch_html_http(url)
        if html and ('data-test-id="text__product-name"' in html
                     or 'data-test-id="product-card"' in html):
            return html
        logger.debug("mm.ru: HTTP returned unrendered SPA or was blocked, using browser")
        return await self._fetch_html_browser(url)

    # ------------------------------------------------------------------
    # Backend API (KazanExpress) — full structured product data
    # ------------------------------------------------------------------

    async def _fetch_api(self, product_id: str) -> dict | None:
        """Fetch full product JSON from the KazanExpress backend (mm.ru's API).

        Returns the ``payload.data`` object, or ``None`` when unavailable. This
        is far richer and faster than scraping the rendered SPA — it includes
        the full ``attributes`` characteristics list, all photos and prices.
        """
        if not product_id:
            return None
        api_url = f"https://api.kazanexpress.ru/api/v2/product/{product_id}"
        proxy = await self.get_proxy(self.marketplace_name)
        for attempt in range(settings.get("max_retries", 3)):
            try:
                resp = await self.session.get(
                    url=api_url,
                    headers={
                        "accept": "application/json",
                        "accept-language": "ru-RU,ru;q=0.9",
                        "user-agent": self._build_headers()["user-agent"],
                    },
                    proxy=proxy,
                )
                if resp.status_code == 200 and resp.text.lstrip().startswith("{"):
                    payload = resp.json().get("payload") or {}
                    data = payload.get("data")
                    if isinstance(data, dict) and data.get("title"):
                        return data
                logger.debug(f"mm.ru API HTTP {resp.status_code} (attempt {attempt + 1})")
            except Exception as exc:
                logger.debug(f"mm.ru API attempt {attempt + 1} failed: {exc}")
            await asyncio.sleep(0.5 * (attempt + 1))
        return None

    def _parse_api(
        self, data: dict, url: str
    ) -> tuple[str, str, Decimal, Decimal, list[str], str, dict]:
        title = (data.get("title") or "").strip()

        # Description — strip HTML tags and decode entities (e.g. &#43; → +)
        import html as _html  # noqa: PLC0415
        description = re.sub(r"<[^>]+>", " ", data.get("description") or "")
        description = _html.unescape(description)
        description = re.sub(r"\s+", " ", description).strip()

        # Price — from skuList. sellPrice is in kopecks, fullPrice in roubles.
        skus = [s for s in (data.get("skuList") or []) if isinstance(s, dict)]
        available = [s for s in skus if (s.get("availableAmount") or 0) > 0] or skus
        chosen = min(available, key=lambda s: s.get("sellPrice") or 0) if available else {}
        sell = chosen.get("sellPrice")
        current_price = (Decimal(str(sell)) / 100) if sell else Decimal(0)
        full = chosen.get("fullPrice")
        original_price = Decimal(str(full)) if full else Decimal(0)
        if original_price and original_price <= current_price:
            original_price = Decimal(0)

        # Images — highest-res "high" variant of each photo
        images: list[str] = []
        for ph in data.get("photos") or []:
            photo = (ph or {}).get("photo") or {}
            best = ""
            for res in ("800", "720", "540", "480", "240"):
                cand = (photo.get(res) or {}).get("high")
                if cand:
                    best = cand
                    break
            if best and best not in images:
                images.append(best)

        # Characteristics — full spec lines from `attributes`
        characteristics: dict[str, str] = {}
        for idx, attr in enumerate(data.get("attributes") or [], 1):
            if isinstance(attr, str) and attr.strip():
                characteristics[str(idx)] = attr.strip()

        sku = self._extract_product_id(url)
        return title, sku, original_price, current_price, images, description, characteristics

    # ------------------------------------------------------------------
    # HTML parsing
    # ------------------------------------------------------------------

    @staticmethod
    def _to_high_res(src: str) -> str:
        """Replace thumbnail suffix with high-res variant."""
        return re.sub(
            r"t_product_\w+\.jpg", "t_product_high.jpg", src
        )

    @staticmethod
    def _clean_price(text: str) -> Decimal:
        cleaned = re.sub(r"[^\d]", "", text)
        return Decimal(cleaned) if cleaned else Decimal(0)

    @staticmethod
    def _extract_product_id(url: str) -> str:
        path = url.split("?")[0].rstrip("/")
        m = re.search(r"-(\d+)$", path)
        return m.group(1) if m else ""

    def _parse_html(
        self, html: str, url: str
    ) -> tuple[str, str, Decimal, Decimal, list[str], str, dict]:
        soup = BeautifulSoup(html, "html.parser")

        # Title
        title_el = soup.find(attrs={"data-test-id": "text__product-name"})
        title = title_el.get_text(strip=True) if title_el else ""

        # Current price
        price_el = soup.find(attrs={"data-test-id": "text__product-price"})
        current_price = self._clean_price(price_el.get_text()) if price_el else Decimal(0)

        # Original price
        old_el = soup.find(attrs={"data-test-id": "text__product-old-price"})
        original_price = self._clean_price(old_el.get_text()) if old_el else Decimal(0)
        if original_price and original_price <= current_price:
            original_price = Decimal(0)

        # Images — from vertical carousel thumbnails, upgraded to high-res
        images: list[str] = []
        seen: set[str] = set()
        for thumb in soup.find_all(attrs={"data-test-id": "list__vertical-photo-carousel"}):
            if not isinstance(thumb, Tag):
                continue
            img = thumb.find("img")
            if not isinstance(img, Tag):
                continue
            src = str(img.get("src") or "")
            if not src.startswith("http"):
                continue
            high = self._to_high_res(src)
            if high not in seen:
                seen.add(high)
                images.append(high)

        # Description — main text + bullet list
        desc_parts: list[str] = []
        short_desc = soup.find(class_="short-description")
        if isinstance(short_desc, Tag):
            main_text = short_desc.find(class_="description")
            if main_text:
                t = main_text.get_text(strip=True)
                if t:
                    desc_parts.append(t)
            for li in short_desc.find_all(class_="item"):
                t = li.get_text(strip=True)
                if t:
                    desc_parts.append(f"• {t}")
        description = "\n".join(desc_parts)

        # Characteristics. DOM structure:
        #   .characteristics > .list > .item
        #       > .subtitle (group label)
        #       > .sublist > li.characteristic > .name + .value
        # (the .item sits under .list, not directly under .characteristics).
        characteristics: dict[str, str] = {}
        chars_block = soup.find(class_="characteristics")
        if isinstance(chars_block, Tag):
            for item in chars_block.find_all(class_="item"):
                if not isinstance(item, Tag):
                    continue
                group_el = item.find(class_="subtitle")
                group = group_el.get_text(strip=True) if group_el else ""
                for char in item.find_all(class_="characteristic"):
                    if not isinstance(char, Tag):
                        continue
                    name_el = char.find(class_="name")
                    val_el = char.find(class_="value")
                    if name_el and val_el:
                        name = name_el.get_text(strip=True)
                        value = val_el.get_text(strip=True)
                        key = f"{group}: {name}" if group else name
                        if name and value:
                            characteristics[key] = value

        sku = self._extract_product_id(url)

        return title, sku, original_price, current_price, images, description, characteristics

    # ------------------------------------------------------------------
    # Link extraction
    # ------------------------------------------------------------------

    async def get_product_links(self, url: str, max_pages: int = 10) -> list[str]:
        links: list[str] = []
        base_url = url.split("?")[0].rstrip("/")

        for page in range(1, max_pages + 1):
            page_url = base_url if page == 1 else f"{base_url}?page={page}"
            html = await self._fetch_html(page_url)
            if not html:
                logger.warning(f"mm.ru: failed to fetch category page {page}")
                break

            page_links = self._extract_category_links(html)
            if not page_links:
                logger.debug(f"mm.ru: no links on page {page}, stopping")
                break

            links.extend(page_links)
            logger.debug(f"mm.ru: page {page} → {len(page_links)} links")

        return list(dict.fromkeys(links))

    @staticmethod
    def _extract_category_links(html: str) -> list[str]:
        soup = BeautifulSoup(html, "html.parser")
        links: list[str] = []
        seen: set[str] = set()
        for a in soup.find_all("a", href=True):
            if not isinstance(a, Tag):
                continue
            href = str(a["href"])
            if "/product/" in href:
                full = href if href.startswith("http") else f"https://mm.ru{href}"
                full = full.split("?")[0]
                if full not in seen:
                    seen.add(full)
                    links.append(full)
        return links

    # ------------------------------------------------------------------
    # Main parse entry point
    # ------------------------------------------------------------------

    async def parse(self, url: str) -> ProductCard:
        # Prefer the backend API — full structured data (incl. all
        # characteristics), faster and no browser rendering required.
        product_id = self._extract_product_id(url)
        data = await self._fetch_api(product_id)
        if data:
            (title, sku, original_price, current_price,
             images, description, characteristics) = self._parse_api(data, url)
        else:
            # Fallback: render the SPA in a browser and scrape the DOM.
            logger.debug(f"mm.ru: API unavailable, falling back to browser for {url}")
            html = await self._fetch_html(url)
            if not html:
                raise ProductCardParsingException(
                    f"mm.ru: failed to fetch product page for {url}"
                )
            (title, sku, original_price, current_price,
             images, description, characteristics) = self._parse_html(html, url)

        if not title:
            raise ProductCardParsingException(
                f"mm.ru: could not extract product title for {url}"
            )

        return ProductCard(
            title=title,
            sku=sku,
            original_price=original_price,
            current_price=current_price,
            images=images,
            source_url=url,
            description=description,
            characteristics=characteristics,
        )


async def main() -> None:
    import json as _json
    crawler = MmCrawler()
    product_card = await crawler.parse(
        "https://mm.ru/product/zaschitnoe-steklo-dlya-126042?skuId=12929772"
    )
    print(_json.dumps(product_card.to_dict(), indent=2, ensure_ascii=False))


if __name__ == "__main__":
    asyncio.run(main())
