# Crawling Domain Rules

> Purpose: This file defines mandatory rules for implementing, modifying, or extending crawlers. It covers crawler architecture, bot-bypass strategies, retry logic, cookie management, and the factory registration contract.
> Context: Read this file before modifying any file in `crawlers/`, `core/factory.py`, or `core/managers.py`.
> Version: 1.0

## 1. Core principle

Every crawler is an isolated, async, stateless-by-design unit behind a single abstract contract.

- Every crawler must extend `BaseCrawler`
- Every crawler must implement `parse()` and `is_valid_url()`
- Every crawler must be registered in `CrawlerFactory`
- No crawler may call another crawler
- No crawler may raise unhandled exceptions to the service layer

## 2. What is this domain?

The crawling domain is responsible for selecting the correct crawler for a given URL, executing it, and returning a `ProductCard`. It encapsulates all marketplace-specific logic: HTTP requests, browser automation, bot-bypass techniques, cookie management, proxy injection, and retry orchestration.

The service layer (`ParsingService`) is not aware of how crawling works — it only calls `crawler.parse(url)` and receives a `ProductCard`.

### Key concepts

| Concept | Description |
|---|---|
| `BaseCrawler` | Abstract base class all crawlers must extend. Defines `parse()` and `is_valid_url()` contracts |
| `CrawlerFactory` | Maps URL patterns to crawler classes. Creates crawler instances on demand |
| `ProxyManager` | Loads, validates, and randomly selects proxies from `data/proxy.txt` |
| `curl-cffi AsyncSession` | HTTP client impersonating Chrome 136. Used by Wildberries, Ozon, YandexMarket, MegaMarket |
| `Patchright` | Chromium browser automation (fork of Playwright with anti-detection). Used for cookie harvesting |
| Cookie harvesting | Browser-based approach run in a separate `multiprocessing.Process` to avoid async/sync conflicts with Playwright |
| Upstreams cache | Wildberries CDN routing map stored in `data/wb_upstreams.json` — maps SKU vol range to CDN host |

## 3. Business rules

**BR-001** — Every new crawler must extend `BaseCrawler` and implement both `parse(url: str) -> ProductCard` and `is_valid_url(url: str) -> bool`.
_Enforced in:_ `crawlers/base.py` (ABC abstract methods)

**BR-002** — Every new crawler must be registered in `CrawlerFactory.__init__()` with a URL pattern key and a class reference.
_Enforced in:_ `core/factory.py @ CrawlerFactory.__init__()`

**BR-003** — The `CrawlerFactory` detects the marketplace by matching regex patterns against the lowercased URL. Pattern order is significant — more specific patterns must come first.
_Enforced in:_ `core/factory.py @ detect_marketplace()`

**BR-004** — When a URL does not match any known pattern, `CrawlerFactory.detect_marketplace()` must raise `UnsupportedMarketplaceError`. This exception is caught by `ParsingService` and returned as HTTP 400.
_Enforced in:_ `core/factory.py @ detect_marketplace()`, `services/parsing.py`

**BR-005** — Crawlers that require session cookies (Ozon, MegaMarket) must implement a cookie lifecycle: load from file → if missing, harvest via browser process → cache in memory.
_Enforced in:_ `crawlers/ozon.py`, `crawlers/megamarket.py`

**BR-006** — Cookie harvesting via Patchright must run in a separate `multiprocessing.Process`, not in the async event loop. This prevents blocking the FastAPI event loop and avoids sync/async Playwright conflicts.
_Enforced in:_ `crawlers/ozon.py @ _process_save_cookies()`, `crawlers/megamarket.py @ _process_save_cookies()`

**BR-007** — When a crawler detects an invalid or expired cookie (e.g. JSON parse error from API), it must delete the cookies file and clear in-memory cookie state before the next retry attempt.
_Enforced in:_ `crawlers/ozon.py @ parse()`, `crawlers/megamarket.py @ parse()`

**BR-008** — All crawlers that make retryable network calls must respect `settings.get("max_retries", 3)`. The default is 3. After all retries are exhausted, they must raise `ProductCardParsingException`.
_Enforced in:_ `crawlers/ozon.py @ parse()`, `crawlers/megamarket.py @ parse()`

**BR-009** — Between retry attempts, crawlers must apply a random jitter sleep of `random.uniform(0.1, 1.5)` seconds to avoid rate-limiting. Exact value must remain randomized.
_Enforced in:_ `crawlers/ozon.py @ parse()`, `crawlers/megamarket.py @ parse()`

**BR-010** — The Wildberries crawler must resolve the CDN upstream host using `get_upstream_host_by_sku()` before building image URLs. Image URLs follow the pattern `{upstream_host}/{sku}/images/big/{index}.webp`.
_Enforced in:_ `crawlers/wildberries.py @ parse()`

**BR-011** — The Wildberries crawler loads upstreams data from `data/wb_upstreams.json` on initialization. If the file is missing or empty, it fetches and saves upstreams on first request.
_Enforced in:_ `crawlers/wildberries.py @ load_upstreams_from_file()`, `save_upstreams_data()`

**BR-012** — The Wildberries prices API returns prices in kopecks. The crawler must divide by 100 before storing in `ProductCard`.
_Enforced in:_ `crawlers/wildberries.py @ parse()`

**BR-013** — The Yandex Market crawler must strip query parameters from the URL before fetching the product page (`url.split("?")[0]`). The URL must also contain `"card/"` to be considered valid.
_Enforced in:_ `crawlers/yandex_market.py @ parse()`

**BR-014** — The Yandex Market crawler parses product images from an embedded JSON blob inside a `<noframes data-apiary="patch">` element. Images are sorted by resolution (`width * height`) in descending order.
_Enforced in:_ `crawlers/yandex_market.py @ parse()`

**BR-015** — The MegaMarket crawler must inject `"adult_disclaimer_confirmed": "1"` into cookies on every request to bypass age verification.
_Enforced in:_ `crawlers/megamarket.py @ get_cookies()`

**BR-016** — The MegaMarket crawler extracts the SKU from the URL path: the second-to-last path segment, then the last `-`-delimited token. This must not be changed without verifying the MegaMarket URL format.
_Enforced in:_ `crawlers/megamarket.py @ parse()`

**BR-017** — The MegaMarket crawler fetches price from the `productOffers` endpoint and selects the offer with the lowest `price` (sorted ascending). If no offers are returned, `current_price` remains `0.0`.
_Enforced in:_ `crawlers/megamarket.py @ get_product_offers()`

**BR-018** — If proxy is active (`settings.proxy.active == true`), MegaMarket crawler must call `ProxyManager.get_random_proxy()` on each retry attempt and configure `self.session.proxies` before making the request.
_Enforced in:_ `crawlers/megamarket.py @ parse()`

**BR-019** — ProxyManager proxies are loaded lazily and cached as a class-level list (`proxies = []`). Load is protected by an async lock (`_proxy_lock`) to prevent race conditions on concurrent first requests.
_Enforced in:_ `core/managers.py @ ProxyManager.load_proxies()`

**BR-020** — Proxy strings in `data/proxy.txt` must match the pattern `[protocol://]user:password@host:port`. Lines that do not match must cause a `ValueError` during load, stopping startup.
_Enforced in:_ `core/managers.py @ ProxyManager.is_valid_proxy()`

## 4. Crawler architecture flow

```mermaid
stateDiagram-v2
    [*] --> URLReceived
    URLReceived --> MarketplaceDetected : factory.detect_marketplace()
    MarketplaceDetected --> CrawlerCreated : factory.create_crawler()
    CrawlerCreated --> Parsing : crawler.parse(url)
    Parsing --> CookiesRequired : needs session cookies
    Parsing --> DirectFetch : no cookies needed (WB, YM)
    CookiesRequired --> LoadFromFile : load_cookies_from_file()
    LoadFromFile --> CookiesLoaded : file exists and valid
    LoadFromFile --> HarvestCookies : file missing or empty
    HarvestCookies --> BrowserProcess : run in separate Process
    BrowserProcess --> CookiesLoaded : cookies obtained
    CookiesLoaded --> DirectFetch : proceed with request
    DirectFetch --> ProductCardBuilt : response parsed successfully
    DirectFetch --> RetryAttempt : request failed
    RetryAttempt --> CookiesInvalidated : "Expecting value" error (bad cookie)
    CookiesInvalidated --> HarvestCookies : delete file, clear memory
    RetryAttempt --> DirectFetch : retry with jitter sleep
    RetryAttempt --> [*] : max_retries exceeded, raise ProductCardParsingException
    ProductCardBuilt --> [*] : return ProductCard
```

## 5. Adding a new crawler — checklist

When adding a new marketplace crawler:

1. Create `crawlers/{marketplace_name}.py` extending `BaseCrawler`
2. Implement `async def parse(self, url: str) -> ProductCard`
3. Implement `def is_valid_url(self, url: str) -> bool`
4. Register the crawler class in `CrawlerFactory.__init__()` under a string key
5. Add the URL regex pattern to `CrawlerFactory.url_patterns`
6. Add the import to `crawlers/__init__.py`
7. Add the supported domain to the `supported_domains` list in `ParseRequest.validate_marketplace_url()`
8. Update `rules/domain/crawling.md` with new business rules
9. Update `rules/database/schema.md` if new file-based state is introduced
10. Write tests covering at least: successful parse, URL detection, invalid URL rejection

## 6. AI-specific rules

- Never invent marketplace API endpoints — only use URLs confirmed in existing crawlers or marketplace documentation
- Never change the SKU extraction logic for existing crawlers without verifying against the actual URL format
- Never remove the `multiprocessing.Process` wrapper around Playwright calls — this is required to avoid event loop conflicts
- Always check `settings.get("proxy", {}).get("active", False)` before using the proxy
- Never hardcode cookie values — cookies must come from file or browser harvest
- Never combine two crawlers into one class

## 7. Definition of done

A crawling change is not complete until:

- Business rules documented and numbered
- Crawler registered in factory
- Cookie lifecycle documented (if applicable)
- SKU extraction logic documented
- Tests cover happy path and error cases
- No hardcoded credentials or cookies

## Forbidden behaviors

- ❌ Crawlers not extending `BaseCrawler`
- ❌ Crawlers not registered in `CrawlerFactory`
- ❌ Synchronous blocking calls inside async `parse()` (use `asyncio.to_thread` or separate `Process`)
- ❌ Hardcoded cookies or session tokens
- ❌ Silent exception swallowing without logging
- ❌ Bypassing retry logic
- ❌ Calling `sync_playwright()` directly in the async event loop

## Checklist

- [ ] Crawler extends `BaseCrawler`
- [ ] `parse()` and `is_valid_url()` implemented
- [ ] Registered in `CrawlerFactory`
- [ ] URL pattern added to factory
- [ ] Domain added to `ParseRequest` validator
- [ ] Cookie lifecycle documented (if needed)
- [ ] Retry logic uses `settings.max_retries`
- [ ] Jitter sleep between retries
- [ ] `ProductCardParsingException` raised after max retries
- [ ] No hardcoded secrets or cookies
- [ ] Tests written
