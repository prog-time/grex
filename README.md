# Marketplace Parsing API

A REST API service that parses product cards from Russian e-commerce marketplaces. Given a product URL, it returns structured data: title, SKU, original price, current price, and image URLs.

## Supported Marketplaces

| Marketplace | Domains |
|---|---|
| Wildberries | `wildberries.ru`, `wildberries.by`, `wildberries.kz` |
| Ozon | `ozon.ru`, `ozon.by`, `ozon.kz` |
| Yandex Market | `market.yandex.ru`, `market.yandex.by` |
| MegaMarket | `megamarket.ru` |

---

## Installation

**Requirements:** Python 3.10+

```bash
# 1. Create and activate a virtual environment
python -m venv .venv
source .venv/bin/activate        # macOS / Linux
.venv\Scripts\activate           # Windows

# 2. Install Python dependencies
pip install -r requirements.txt

# 3. Install the Playwright browser (required for cookie harvesting on Ozon and MegaMarket)
patchright install chromium
```

---

## Configuration

All configuration lives in `data/settings.yml`. The file is read once on startup.

```yaml
# APP SETTINGS
app:
  host: "localhost"
  port: 8004

# SECURITY
security:
  api_key: "change-me"        # Value required in the X-API-Key header for POST /parse

max_retries: 3                # How many times a crawler retries a failed request

# PROXY SETTINGS
proxy:
  active: false               # Set to true to route requests through proxies
  type: "http"                # "http" or "socks5"

# DEBUG MODE
debug:
  active: false               # true enables /docs and /redoc Swagger UI
  logging_in_file: true       # true writes logs to the logs/ directory
```

### Proxy file (`data/proxy.txt`)

Add one proxy per line in the format below. The proxy type (`http` / `socks5`) is taken from `settings.yml`, not from this file.

```
username:password@server:port
username:password@server:port
```

Example:

```
john:secret@proxy.example.com:8080
```

The proxy is used only when `proxy.active: true` in `settings.yml`. Currently, proxy rotation applies to MegaMarket requests only.

### Cookies

Ozon and MegaMarket require browser cookies to bypass bot-protection. The service handles this automatically:

- On the first request to Ozon, a headless Chromium browser opens, navigates to the product page, harvests cookies, and saves them to `data/ozon_cookies.json`.
- On the first request to MegaMarket, the same process saves cookies to `data/mega_market_cookies.json`.
- On subsequent requests, cookies are loaded from the saved files.
- If a request fails with a "Trying to disable VPN" error (MegaMarket), the cookie file is deleted and cookies are re-harvested on the next attempt.

You can also pre-populate the cookie files manually by extracting cookies from your browser and writing them as a JSON object (`{"cookie_name": "cookie_value", ...}`).

---

## Running the Server

```bash
python api.py
```

The server starts on the host and port defined in `data/settings.yml` (default: `localhost:8004`).

When `debug.active: true`, the interactive Swagger UI is available at:

```
http://localhost:8004/docs
http://localhost:8004/redoc
```

In production (`debug.active: false`), those URLs return `404`.

---

## API Reference

### Authentication

Only `POST /parse` requires authentication. Pass your API key (from `settings.yml`) in the `X-API-Key` header.

All other endpoints are unauthenticated.

---

### `GET /`

Returns service info and key endpoint links.

```bash
curl http://localhost:8004/
```

Response:

```json
{
  "message": "Marketplace Parsing API",
  "documentation": "/docs",
  "health": "/health"
}
```

---

### `GET /health`

Returns service health status, uptime in seconds, and the list of supported marketplace names.

```bash
curl http://localhost:8004/health
```

Response:

```json
{
  "status": "healthy",
  "uptime": 123.45,
  "supported_marketplaces": ["wildberries", "ozon", "yandex_market", "megamarket"]
}
```

---

### `GET /marketplaces`

Returns the list of supported marketplace names and example product URLs.

```bash
curl http://localhost:8004/marketplaces
```

Response:

```json
{
  "supported_marketplaces": ["wildberries", "ozon", "yandex_market", "megamarket"],
  "example_urls": [
    "https://www.wildberries.ru/catalog/17386348/detail.aspx",
    "https://ozon.ru/product/shumin-maska-dlya-sna-chernaya-1563221610",
    "https://market.yandex.ru/card/espander-dlya-paltsev-ruk-kistevoy-95-kg-s-krepleniyem-na-zapyastye-tsvet---chernyy/103757317666",
    "https://megamarket.ru/catalog/details/puhovik-detskiy-poivre-blanc-w21-1201-jrgl-2122-mega-pink-128-600011555038"
  ]
}
```

---

### `GET /parse/detect`

Detects the marketplace for a given URL without performing a full parse. Useful for validating URLs before sending them to `POST /parse`.

```bash
curl "http://localhost:8004/parse/detect?url=https://www.wildberries.ru/catalog/17386348/detail.aspx"
```

Response `200`:

```json
{
  "url": "https://www.wildberries.ru/catalog/17386348/detail.aspx",
  "marketplace": "wildberries",
  "status": "supported"
}
```

Response `400` (unsupported URL):

```json
{
  "detail": "Unsupported marketplace URL: https://example.com/product"
}
```

---

### `POST /parse`

Parses a product page and returns structured product card data.

**Headers:**

| Header | Required | Description |
|---|---|---|
| `X-API-Key` | Yes | API key from `settings.yml` → `security.api_key` |
| `Content-Type` | Yes | `application/json` |

**Request body:**

```json
{
  "url": "https://www.wildberries.ru/catalog/17386348/detail.aspx"
}
```

The `url` field must point to a supported marketplace domain. Unsupported domains are rejected with HTTP 422.

#### curl examples

Wildberries:

```bash
curl -X POST http://localhost:8004/parse \
  -H "X-API-Key: your-api-key" \
  -H "Content-Type: application/json" \
  -d '{"url": "https://www.wildberries.ru/catalog/17386348/detail.aspx"}'
```

Ozon:

```bash
curl -X POST http://localhost:8004/parse \
  -H "X-API-Key: your-api-key" \
  -H "Content-Type: application/json" \
  -d '{"url": "https://ozon.ru/product/shumin-maska-dlya-sna-chernaya-1563221610"}'
```

Yandex Market:

```bash
curl -X POST http://localhost:8004/parse \
  -H "X-API-Key: your-api-key" \
  -H "Content-Type: application/json" \
  -d '{"url": "https://market.yandex.ru/card/espander-dlya-paltsev-ruk-kistevoy-95-kg-s-krepleniyem-na-zapyastye-tsvet---chernyy/103757317666"}'
```

MegaMarket:

```bash
curl -X POST http://localhost:8004/parse \
  -H "X-API-Key: your-api-key" \
  -H "Content-Type: application/json" \
  -d '{"url": "https://megamarket.ru/catalog/details/puhovik-detskiy-poivre-blanc-w21-1201-jrgl-2122-mega-pink-128-600011555038"}'
```

#### Python example

```python
import requests

API_URL = "http://localhost:8004"
API_KEY = "your-api-key"

response = requests.post(
    f"{API_URL}/parse",
    headers={"X-API-Key": API_KEY, "Content-Type": "application/json"},
    json={"url": "https://www.wildberries.ru/catalog/17386348/detail.aspx"},
)

data = response.json()

if data["success"]:
    product = data["data"]
    print(f"Title:         {product['title']}")
    print(f"SKU:           {product['sku']}")
    print(f"Original price:{product['original_price']}")
    print(f"Current price: {product['current_price']}")
    print(f"Images:        {product['images']}")
else:
    print(f"Error: {data['error']}")
```

#### Successful response

```json
{
  "success": true,
  "data": {
    "title": "Product Name",
    "sku": "17386348",
    "source_url": "https://www.wildberries.ru/catalog/17386348/detail.aspx",
    "original_price": 1500.0,
    "current_price": 1200.0,
    "images": [
      "https://basket-01.wbbasket.ru/vol173/part17386/17386348/images/big/1.webp"
    ]
  },
  "marketplace": "wildberries",
  "processing_time": 1.23
}
```

#### Error responses

| Status | Cause |
|---|---|
| `200` with `"success": false` | Parsing failed (network error, page structure changed, etc.) |
| `400` | Unsupported marketplace detected after Pydantic validation passed |
| `401` | `X-API-Key` header is missing |
| `403` | `X-API-Key` value does not match the configured key |
| `422` | Request body is invalid or URL domain is not supported |

---

## Adding a New Marketplace Crawler

Follow these steps to add support for a new marketplace.

### 1. Create the crawler file

Create `crawlers/yourmarketplace.py`. Implement `BaseCrawler`:

```python
import asyncio
from .base import BaseCrawler
from models import ProductCard
from core.exceptions import ProductCardParsingException


class YourMarketplaceCrawler(BaseCrawler):
    def __init__(self):
        super().__init__()
        # Initialize any HTTP sessions, file paths, etc.

    def is_valid_url(self, url: str) -> bool:
        return "yourmarketplace." in url

    async def parse(self, url: str) -> ProductCard:
        # Fetch and parse the product page.
        # Raise ProductCardParsingException on failure.

        return ProductCard(
            title="...",
            sku="...",
            original_price=0.0,
            current_price=0.0,
            images=[],
            source_url=url,
        )
```

`BaseCrawler` requires two abstract methods:

- `parse(url: str) -> ProductCard` — fetch the page and return a populated `ProductCard`.
- `is_valid_url(url: str) -> bool` — return `True` if this crawler handles the given URL.

### 2. Export from the crawlers package

Add your class to `crawlers/__init__.py`:

```python
from .yourmarketplace import YourMarketplaceCrawler
```

### 3. Register in `CrawlerFactory`

In `core/factory.py`, add your crawler to both dictionaries:

```python
from crawlers import (
    ...,
    YourMarketplaceCrawler,
)

class CrawlerFactory:
    def __init__(self):
        self.crawlers = {
            ...,
            "yourmarketplace": YourMarketplaceCrawler,   # add this
        }

        self.url_patterns = {
            ...,
            r'yourmarketplace\.(ru|com)': "yourmarketplace",  # add this
        }
```

### 4. Update the domain validator

In `models/product.py`, add the new domains to the `supported_domains` list inside `ParseRequest.validate_marketplace_url`:

```python
supported_domains = [
    ...,
    'yourmarketplace.ru',
]
```

### 5. Restart the server

```bash
python api.py
```

Verify the new marketplace appears:

```bash
curl http://localhost:8004/marketplaces
```

The new marketplace name should appear in `supported_marketplaces`.

---

## Project Structure

```
parser_python/
├── api.py                  # FastAPI application and endpoint definitions
├── config.py               # Loads data/settings.yml into the `settings` dict
├── main.py                 # Sandbox script for manual testing (not the server)
├── requirements.txt
├── data/
│   ├── settings.yml        # Main configuration file
│   ├── proxy.txt           # Proxy list (one per line)
│   ├── ozon_cookies.json   # Auto-generated; Ozon session cookies
│   └── mega_market_cookies.json  # Auto-generated; MegaMarket session cookies
├── crawlers/
│   ├── base.py             # BaseCrawler abstract class
│   ├── wildberries.py
│   ├── ozon.py
│   ├── yandex_market.py
│   └── megamarket.py
├── services/
│   └── parsing.py          # ParsingService: orchestrates crawler selection and execution
├── core/
│   ├── factory.py          # CrawlerFactory: URL detection and crawler instantiation
│   ├── managers.py         # ProxyManager: loads and rotates proxies from proxy.txt
│   └── exceptions.py       # Custom exception types
├── models/
│   └── product.py          # ProductCard dataclass; ParseRequest / ParseResponse Pydantic models
├── utils/
│   ├── helpers.py          # URL parsing utilities
│   └── logger.py           # Loguru logger setup
├── logs/                   # Log files (created when logging_in_file: true)
└── rules/
    └── api/
        └── endpoints.md    # Authoritative API contract (read before changing api.py)
```
