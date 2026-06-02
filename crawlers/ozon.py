import asyncio
import json
import os
import random
import re
import time
import uuid
from decimal import Decimal
from multiprocessing import Queue
from typing import ClassVar

from curl_cffi.requests.session import AsyncSession
from loguru import logger

from config import data_path, settings
from core.exceptions import ProductCardParsingException
from models import ProductCard
from utils.helpers import parse_url

from .base import CookieCrawler


class OzonCrawler(CookieCrawler):
    marketplace_name: ClassVar[str] = "ozon"
    url_pattern: ClassVar[str] = r"ozon\.(ru|by|kz)"
    requires_cookie_lock: ClassVar[bool] = True

    cookies_file_path: str = os.path.join(data_path, "ozon_cookies.json")
    api_headers_file_path: str = os.path.join(data_path, "ozon_api_headers.json")

    # Reuse a captured Ozon session for this long before recapturing via browser.
    # The ~30s browser capture then runs at most once per window instead of per
    # product. Overridable via settings: ozon.cookie_ttl_seconds.
    cookie_ttl_seconds: ClassVar[int] = 1800  # 30 minutes

    def __init__(self) -> None:
        super().__init__()
        self.api_headers_data: dict | None = None
        self.session = AsyncSession(impersonate="chrome136")

    # Phrases that mark an Ozon anti-bot / CAPTCHA challenge page (lowercased).
    # When these appear as the page title, the page is a challenge, not a product.
    _ANTIBOT_MARKERS: ClassVar[tuple[str, ...]] = (
        "сопоставьте пазл",
        "двигая ползунок",
        "не бот",
        "подтвердите, что вы не робот",
        "я не робот",
        "доступ ограничен",
        "проверка безопасности",
        "проверка соединения",
        "ошибка доступа",
        "captcha",
        "challenge",
    )

    def is_valid_url(self, url: str) -> bool:
        return "ozon." in url

    @classmethod
    def _looks_like_antibot(cls, text: str | None) -> bool:
        """True when *text* matches a known Ozon anti-bot / CAPTCHA phrase."""
        if not text:
            return False
        low = text.lower()
        return any(marker in low for marker in cls._ANTIBOT_MARKERS)

    # ------------------------------------------------------------------
    # Cookie management (overrides CookieCrawler.get_cookies to also
    # load API headers captured during the browser session)
    # ------------------------------------------------------------------

    def _cookies_fresh(self) -> bool:
        """True when the cached cookie file exists and is within its TTL."""
        ttl = int(settings.get("ozon", {}).get("cookie_ttl_seconds", self.cookie_ttl_seconds))
        try:
            age = time.time() - os.path.getmtime(self.cookies_file_path)
        except OSError:
            return False
        return age < ttl

    async def get_cookies(self, url: str) -> dict:
        # Reuse the cached session while fresh (within cookie_ttl_seconds) so the
        # ~30s browser capture runs at most once per TTL window — not per product.
        if self._cookies_fresh():
            self.cookies_data = await self.load_cookies_from_file()
        else:
            self.cookies_data = {}
        if not self.cookies_data:
            logger.debug("Ozon: cookies missing or stale — capturing fresh session via browser")
            await self.save_cookies(url)
        if not self.api_headers_data:
            self.api_headers_data = await self._load_api_headers_from_file()
        return self.cookies_data or {}

    async def save_cookies(self, url: str) -> bool:
        proxy_dict = self._playwright_proxy(await self.get_proxy(self.marketplace_name))
        result = await self._run_in_process(
            self._process_save_cookies,
            (url, self.cookies_file_path, self.api_headers_file_path, proxy_dict),
            timeout=30,
        )
        if result and result.get("success"):
            self.cookies_data = result["cookies"]
            self.api_headers_data = result.get("api_headers") or {}
            logger.debug(
                f"Ozon: cookies saved (captured {len(self.api_headers_data)} API headers)"
            )
            return True
        error = result.get("error", "no result") if result else "process returned nothing"
        logger.error(f"Ozon: cookie save failed — {error}")
        return False

    async def _load_api_headers_from_file(self) -> dict:
        if not os.path.exists(self.api_headers_file_path):
            return {}
        try:
            import aiofiles  # noqa: PLC0415
            async with aiofiles.open(self.api_headers_file_path) as f:
                return json.loads(await f.read())
        except Exception:
            return {}

    # ------------------------------------------------------------------
    # Subprocess targets (run in a separate process via _run_in_process)
    # ------------------------------------------------------------------

    @staticmethod
    def _process_save_cookies(
        url: str, cookies_file_path: str, api_headers_file_path: str,
        proxy: dict | None, result_queue: Queue
    ) -> None:
        """Open Ozon in a browser, capture session cookies and API headers."""
        try:
            from patchright.sync_api import sync_playwright

            captured_headers: dict[str, str] = {}
            launch_kwargs: dict = {"user_data_dir": "", "headless": False}
            if proxy:
                launch_kwargs["proxy"] = proxy

            with sync_playwright() as p:
                context = p.chromium.launch_persistent_context(**launch_kwargs)

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
                    cookies_data = {item["name"]: item["value"] for item in cookies}
                    with open(cookies_file_path, "w", encoding="utf-8") as f:
                        json.dump(cookies_data, f, indent=2, ensure_ascii=False)
                    if captured_headers:
                        with open(api_headers_file_path, "w", encoding="utf-8") as f:
                            json.dump(captured_headers, f, indent=2, ensure_ascii=False)
                    result_queue.put({
                        "success": True,
                        "cookies": cookies_data,
                        "api_headers": captured_headers,
                    })
                else:
                    result_queue.put({"success": False, "error": "No cookies found"})

        except Exception as e:
            result_queue.put({"success": False, "error": str(e)})

    @staticmethod
    def _process_parse_via_browser(url: str, proxy: dict | None, result_queue: Queue) -> None:
        """Open the product page in a browser; extract data via API interception + DOM."""
        try:
            from patchright.sync_api import sync_playwright

            api_data: dict = {}
            launch_kwargs: dict = {"user_data_dir": "", "headless": False}
            if proxy:
                launch_kwargs["proxy"] = proxy

            with sync_playwright() as p:
                context = p.chromium.launch_persistent_context(**launch_kwargs)

                def on_response(response):
                    if "entrypoint-api.bx/page/json" in response.url:
                        try:
                            data = response.json()
                            ws = data.get("widgetStates", {})
                            if len(ws) > len(api_data.get("widgetStates", {})):
                                api_data.clear()
                                api_data.update(data)
                        except Exception:
                            pass

                context.on("response", on_response)
                page = context.new_page()
                page.goto(url, wait_until="domcontentloaded", timeout=30000)

                try:
                    page.wait_for_function(
                        '() => { const h = document.querySelector("h1"); '
                        'return h && h.textContent.length > 5 && !h.textContent.includes("не бот"); }',
                        timeout=20000,
                    )
                except Exception:
                    pass

                time.sleep(2)

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

            # If no product API was intercepted and the page title is an
            # anti-bot challenge, report it as a CAPTCHA so the caller can retry
            # with a fresh context instead of treating the challenge as success.
            dom_title = (dom or {}).get("title") or ""
            if not api_data.get("widgetStates") and OzonCrawler._looks_like_antibot(dom_title):
                result_queue.put({
                    "success": False,
                    "error": "Ozon anti-bot challenge (CAPTCHA)",
                    "captcha": True,
                })
                return

            result_queue.put({"success": True, "api_data": api_data, "dom": dom})

        except Exception as e:
            result_queue.put({"success": False, "error": str(e)})

    # ------------------------------------------------------------------
    # Internal helpers
    # ------------------------------------------------------------------

    def _build_headers(self, url: str) -> dict:
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
        if self.api_headers_data:
            headers.update(self.api_headers_data)
            headers['x-page-view-id'] = str(uuid.uuid4())
        return headers

    def _extract_from_widget_states(
        self, widget_states: dict, url: str
    ) -> tuple[str | None, str | None, Decimal, Decimal, list, str, dict]:
        """Parse title, sku, prices, images, description, characteristics from Ozon widgetStates JSON."""
        product_title = None
        product_sku = None
        product_original_price: Decimal = Decimal(0)
        product_current_price: Decimal = Decimal(0)
        product_images: list = []
        product_description: str = ""
        product_characteristics: dict = {}

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
                        product_original_price = (
                            Decimal(re.sub(r'[^\d]', '', m_orig.group())) if m_orig else Decimal(0)
                        )
                    cur = data.get("price")
                    if cur:
                        m_cur = re.search(r'[\d\s]+', cur)
                        product_current_price = (
                            Decimal(re.sub(r'[^\d]', '', m_cur.group())) if m_cur else Decimal(0)
                        )
            except Exception as e:
                logger.warning(f"Error parsing prices for {url}: {e}")

            try:
                if "webDescription" in widget_name and not product_description:
                    data = json.loads(widget_data)
                    product_description = data.get("description") or data.get("html") or ""
                    if isinstance(product_description, dict):
                        product_description = product_description.get("text") or ""
            except Exception as e:
                logger.warning(f"Error parsing description for {url}: {e}")

            try:
                if "webCharacteristics" in widget_name and not product_characteristics:
                    data = json.loads(widget_data)
                    for section in data.get("characteristics", []):
                        for item in section.get("short", []) + section.get("full", []):
                            name = item.get("name")
                            values = item.get("values", [])
                            if name and values:
                                product_characteristics[name] = ", ".join(
                                    str(v.get("text", "")) for v in values if v.get("text")
                                )
            except Exception as e:
                logger.warning(f"Error parsing characteristics for {url}: {e}")

        return (product_title, product_sku, product_original_price, product_current_price,
                product_images, product_description, product_characteristics)

    async def _parse_via_browser(self, url: str) -> dict | None:
        """Fallback: open the page in a browser and extract data from API + DOM.

        Retries with a fresh browser context when Ozon serves an anti-bot
        CAPTCHA, which it often rotates per session.
        """
        captcha_retries = int(
            settings.get("ozon", {}).get("browser_captcha_retries", 3)
        )
        result = None
        for attempt in range(captcha_retries):
            # Fetch a fresh proxy each attempt so CAPTCHA retries rotate the
            # exit IP. SOCKS5-auth proxies convert to None (Chromium can't use
            # them) → browser runs direct; add HTTP proxies to route via RU IP.
            proxy_str = await self.get_proxy(self.marketplace_name)
            proxy_dict = self._playwright_proxy(proxy_str)
            result = await self._run_in_process(
                self._process_parse_via_browser, (url, proxy_dict),
            )
            if result and result.get("success"):
                logger.debug("Ozon: browser extraction complete")
                return result
            if result and result.get("captcha"):
                logger.warning(
                    f"Ozon: anti-bot CAPTCHA in browser "
                    f"(attempt {attempt + 1}/{captcha_retries}) for {url}"
                )
                await asyncio.sleep(random.uniform(1.0, 3.0))
                continue
            break  # non-CAPTCHA failure — handle below

        error = result.get("error", "") if result else ""
        if error:
            logger.warning(f"Ozon: browser parse failed — {error}")

        # Auto-install patchright only if browser executable is explicitly missing
        browser_missing = any(k in error.lower() for k in ("executable", "not found", "no such file"))
        if browser_missing:
            logger.info("Ozon: browser not available, attempting patchright install...")
            if await self._install_patchright():
                proxy_dict = self._playwright_proxy(await self.get_proxy(self.marketplace_name))
                result = await self._run_in_process(
                    self._process_parse_via_browser, (url, proxy_dict),
                )
                if result and result.get("success"):
                    logger.debug("Ozon: browser extraction complete after install")
                    return result
                if result:
                    logger.warning(f"Ozon: browser parse failed after install — {result.get('error')}")

        return None

    @staticmethod
    async def _install_patchright() -> bool:
        """Run `patchright install chromium` and return True on success."""
        import sys  # noqa: PLC0415
        try:
            proc = await asyncio.create_subprocess_exec(
                sys.executable, "-m", "patchright", "install", "chromium",
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.PIPE,
            )
            await asyncio.wait_for(proc.communicate(), timeout=120)
            success = proc.returncode == 0
            logger.info(f"patchright install chromium: exit code {proc.returncode}")
            return success
        except Exception as exc:  # noqa: BLE001
            logger.error(f"patchright install failed: {exc}")
            return False

    # ------------------------------------------------------------------
    # Link extraction
    # ------------------------------------------------------------------

    async def get_product_links(self, url: str, max_pages: int = 10) -> list[str]:
        parsed_url = parse_url(url)
        base_path = parsed_url.get("path", "")
        links: list[str] = []

        for page in range(1, max_pages + 1):
            try:
                path = (
                    base_path if page == 1
                    else f"{base_path}{'&' if '?' in base_path else '?'}page={page}"
                )
                cookies = await self.get_cookies(url)
                proxy = await self.get_proxy(self.marketplace_name)
                response = await self.session.get(
                    url="https://www.ozon.ru/api/entrypoint-api.bx/page/json/v2",
                    params={"url": path},
                    cookies=cookies,
                    headers=self._build_headers(url),
                    proxy=proxy,
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

    # ------------------------------------------------------------------
    # Main parse entry point
    # ------------------------------------------------------------------

    async def parse(self, url: str) -> ProductCard:
        parsed_url = parse_url(url)
        params = {"url": parsed_url.get("path")}

        # --- Try HTTP API with cookies first ---
        result_data = None
        got_403 = False
        for attempt in range(settings.get("max_retries", 3)):
            try:
                cookies = await self.get_cookies(url)
                proxy = await self.get_proxy(self.marketplace_name)
                response = await self.session.get(
                    url='https://www.ozon.ru/api/entrypoint-api.bx/page/json/v2',
                    params=params,
                    cookies=cookies,
                    headers=self._build_headers(url),
                    proxy=proxy,
                )
                if response.status_code != 200:
                    logger.warning(f"Ozon API HTTP {response.status_code} for {url}")
                    raise ValueError(f"HTTP {response.status_code}")

                raw = response.text
                if not raw.lstrip().startswith("{"):
                    logger.warning(
                        f"Ozon API non-JSON response, status={response.status_code}, "
                        f"start={raw[:200]!r}"
                    )
                    raise ValueError("Expecting value")

                result_data = response.json()
                logger.debug(f"Ozon API success via HTTP for {url}")
                break

            except Exception as e:
                logger.warning(f"Ozon HTTP attempt {attempt + 1} failed for {url}: {e}")
                err = str(e)
                blocked = "HTTP 403" in err or "HTTP 401" in err
                non_json = "Expecting value" in err
                if blocked:
                    got_403 = True
                if blocked or non_json:
                    # The HTTP API path is unusable for this request. Do NOT
                    # invalidate the cached session: Ozon 403s even with a freshly
                    # captured session, so deleting cookies only forces a wasteful
                    # ~30s recapture on the next product. Keep the cache (TTL
                    # governs refresh) and go straight to the browser fallback,
                    # which works independently of cookies.
                    logger.debug(f"Ozon HTTP unusable ({err}), switching to browser for {url}")
                    break
                await asyncio.sleep(random.uniform(0.1, 1.5))

        # --- Fallback: browser-based extraction ---
        if not result_data:
            logger.debug(f"Ozon HTTP failed, falling back to browser for {url}")
            browser_result = await self._parse_via_browser(url)
        else:
            browser_result = {"success": True, "api_data": result_data, "dom": None}

        if not browser_result:
            if got_403:
                raise ProductCardParsingException(
                    "Proxies with Russian IP addresses are required to access Ozon"
                )
            raise ProductCardParsingException(f"Failed to get Ozon product data for {url}")

        api_data = browser_result.get("api_data") or {}
        dom = browser_result.get("dom") or {}

        widget_states = api_data.get("widgetStates", {})
        title, sku, orig_price, cur_price, images, description, characteristics = (
            self._extract_from_widget_states(widget_states, url)
        )

        # Fill gaps from API top-level fields
        if not title:
            seo_title = api_data.get("seo", {}).get("title", "")
            title = re.sub(
                r'\s+купить\s+c?\s+доставкой.*$', '', seo_title, flags=re.IGNORECASE
            ).strip() or None
        if not sku:
            sku = (
                str(api_data.get("pageInfo", {}).get("analyticsInfo", {}).get("sku", ""))
                or None
            )

        # Fill gaps from DOM extraction
        if dom:
            if not title and dom.get("title") and not self._looks_like_antibot(dom["title"]):
                title = dom["title"]
            if not images and dom.get("imgs"):
                images = dom["imgs"]
            if (not orig_price or not cur_price) and dom.get("prices"):
                nums = sorted({
                    int(re.sub(r'[^\d]', '', p))
                    for p in dom["prices"]
                    if re.sub(r'[^\d]', '', p)
                })
                if len(nums) >= 2:
                    cur_price = Decimal(min(nums))
                    orig_price = Decimal(max(nums))
                elif len(nums) == 1:
                    cur_price = Decimal(nums[0])

        # Reject anti-bot challenge pages: their CAPTCHA text can leak in as the
        # title (e.g. "Сопоставьте пазл, двигая ползунок"). Fail cleanly instead
        # of persisting the challenge page as a product.
        if self._looks_like_antibot(title):
            raise ProductCardParsingException(
                f"Ozon anti-bot challenge (CAPTCHA) encountered for {url}"
            )
        if not title:
            raise ProductCardParsingException(
                f"Ozon: could not extract product title for {url}"
            )

        return ProductCard(
            title=title,
            sku=sku,
            original_price=orig_price,
            current_price=cur_price,
            images=images,
            source_url=url,
            description=description,
            characteristics=characteristics,
        )


async def main():
    import json as _json
    crawler = OzonCrawler()
    product_card = await crawler.parse("https://ozon.ru/product/shumin-maska-dlya-sna-chernaya-1563221610")
    print(_json.dumps(product_card.to_dict(), indent=2, ensure_ascii=False))


if __name__ == '__main__':
    asyncio.run(main())
