# Testing Strategy Rules

> Purpose: Guarantee that every change is tested adequately before merging. Prevent regressions and silent failures in production.
> Context: Read this file before writing any code that affects crawlers, models, services, or API endpoints.
> Version: 1.0

## 1. Core principle

Test everything that can fail, and fail fast.

- Unit tests for individual components (URL detection, SKU extraction, price parsing)
- Integration tests for service and crawler interactions with mocked HTTP
- End-to-end tests against real marketplaces are excluded from CI (they require live cookies, proxies, and network access)
- Regression tests for every previously fixed bug
- Test-driven mindset: define expected `ProductCard` output before implementing extraction

_Note: This project currently has no `tests/` directory. The rules below define the mandatory standard when tests are added._

## 2. Test framework

Use `pytest` with `pytest-asyncio` for async test support.

```bash
pip install pytest pytest-asyncio
```

```python
# ✅ Correct — async test with pytest-asyncio
import pytest

@pytest.mark.asyncio
async def test_wildberries_parse_returns_product_card():
    crawler = WildberriesCrawler()
    # mock network calls
    ...
```

## 3. Test hierarchy

**3.1 Unit tests (required)**

- `tests/test_factory.py` — `CrawlerFactory.detect_marketplace()` for all supported and unsupported URLs
- `tests/test_models.py` — `ParseRequest` URL validator (supported domains, unsupported domains, malformed URLs)
- `tests/test_product_card.py` — `ProductCard.to_dict()` output structure, `Decimal` to `float` conversion, `parsed_at` excluded
- `tests/test_proxy_manager.py` — `is_valid_proxy()` for valid and invalid formats, `load_proxies()` with missing file and empty file
- `tests/test_price_parsing.py` — Price extraction logic: kopeck division (WB), string cleaning (Ozon, YM)
- `tests/test_sku_extraction.py` — SKU extraction for each marketplace URL format

**3.2 Integration tests (required)**

- `tests/test_parsing_service.py` — `ParsingService.parse_product()` with mocked crawlers: success path, `UnsupportedMarketplaceError`, `CrawlerNotFoundError`, unexpected exception
- `tests/test_api.py` — FastAPI endpoint tests using `httpx.AsyncClient`:
  - `GET /health` returns 200 with correct structure
  - `POST /parse` without API key returns 401
  - `POST /parse` with wrong API key returns 403
  - `POST /parse` with unsupported URL returns 422
  - `POST /parse` with valid URL and mocked service returns 200

**3.3 End-to-end tests (manual only)**

Real marketplace parsing tests must not run in CI. Run them manually using the `main()` functions in each crawler file:

```bash
python -m crawlers.wildberries
python -m crawlers.ozon
python -m crawlers.yandex_market
python -m crawlers.megamarket
```

## 4. Mocking rules

All network calls must be mocked in unit and integration tests. Use `unittest.mock.AsyncMock` or `pytest-mock`.

```python
# ✅ Correct — mock the HTTP session
from unittest.mock import AsyncMock, patch

@pytest.mark.asyncio
async def test_wildberries_returns_product_card(mocker):
    mock_session = AsyncMock()
    mock_session.get.return_value.json.return_value = {
        "data": {"products": [{"sizes": [{"price": {"basic": 150000, "product": 120000}}]}]}
    }
    mocker.patch.object(WildberriesCrawler, 'session', mock_session)
    ...

# ❌ Incorrect — makes real HTTP requests in unit test
async def test_wildberries():
    crawler = WildberriesCrawler()
    result = await crawler.parse("https://www.wildberries.ru/catalog/17386348/detail.aspx")
```

## 5. Test data rules

- Use fixture URLs confirmed in the codebase (see `api.py` `example_urls`)
- Build `ProductCard` instances explicitly in tests — do not rely on external state
- Reset any class-level state before tests: `ProxyManager.proxies = []`
- Do not use real cookies or proxy credentials in test fixtures

## 6. Test naming rules

```python
# ✅ Correct — describes expected behavior
def test_detect_marketplace_raises_for_unsupported_url():
    ...

def test_parse_request_rejects_non_marketplace_url():
    ...

def test_product_card_to_dict_excludes_parsed_at():
    ...

# ❌ Incorrect — meaningless name
def test1():
    ...

def test_parse():
    ...
```

## 7. Critical paths requiring 100% test coverage

These behaviors must be tested without exception:

| Behavior | Test location |
|---|---|
| `UnsupportedMarketplaceError` raised for unknown URLs | `test_factory.py` |
| `ParseRequest` rejects non-marketplace URLs | `test_models.py` |
| `POST /parse` returns 401 without `X-API-Key` | `test_api.py` |
| `POST /parse` returns 403 with wrong key | `test_api.py` |
| `to_dict()` does not include `parsed_at` | `test_product_card.py` |
| WB kopeck-to-RUB division | `test_price_parsing.py` |
| Cookie file deletion on stale cookie error | `test_ozon_crawler.py`, `test_megamarket_crawler.py` |
| `ProxyManager` raises on empty `proxy.txt` | `test_proxy_manager.py` |
| `ProductCardParsingException` raised after max retries | `test_ozon_crawler.py`, `test_megamarket_crawler.py` |

## 8. Forbidden test patterns

- ❌ Tests that make real HTTP requests to marketplaces
- ❌ Tests that write to `data/` directory (use `tmp_path` fixture)
- ❌ Tests that depend on existing `data/ozon_cookies.json` state
- ❌ Tests with hardcoded real proxy credentials
- ❌ `time.sleep()` in tests unless testing retry jitter specifically
- ❌ Skipping tests without a documented reason

## 9. AI-specific test rules

AI-generated code must include:
- Unit tests for every new crawler's SKU extraction and price parsing
- A test asserting the new crawler is registered in `CrawlerFactory`
- A test asserting the new domain appears in `ParseRequest` supported domains
- A test for the cookie lifecycle if the crawler uses browser-based cookies

## 10. Definition of done

A change is not complete until:
- Tests exist for all critical paths listed in Section 7
- All tests pass with `pytest tests/`
- No real network calls in test suite
- `data/` directory not modified during tests
- Test names are descriptive

## Checklist

- [ ] Unit tests created for new logic
- [ ] Integration tests created for service/API changes
- [ ] Critical paths from Section 7 covered
- [ ] No real HTTP calls in tests
- [ ] `tmp_path` used instead of `data/` directory
- [ ] `ProxyManager.proxies` reset in relevant tests
- [ ] Test names are descriptive and consistent
- [ ] No forbidden patterns used
- [ ] All tests pass locally
