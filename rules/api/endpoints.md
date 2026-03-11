# API Contract Rules

> Context: Read this file before adding, modifying, or deleting any HTTP endpoint in `api.py`.
> Version: 1.0

## 1. Core principle

The API is defined by explicit documentation.

- This file is the contract
- This file is the documentation
- This file is the integration surface
- No undocumented endpoints are allowed
- No parallel descriptions are allowed

If behavior exists in code but not here, it is undocumented and must be added before merging.

## 2. Endpoint inventory

All endpoints are defined in `api.py`. The FastAPI application is instantiated as `app = FastAPI(...)`.

Authentication via `X-API-Key` header is required for `POST /parse` only. All other endpoints are unauthenticated.

| Method | Path | Auth | Description |
|---|---|---|---|
| `GET` | `/` | No | Root info, returns service name and key endpoint links |
| `GET` | `/health` | No | Service health check with uptime and supported marketplaces |
| `POST` | `/parse` | Yes (`X-API-Key`) | Parse a product URL and return product card data |
| `GET` | `/marketplaces` | No | List supported marketplace names and example URLs |
| `GET` | `/parse/detect` | No | Detect marketplace from a URL without parsing |

## 3. Endpoint specifications

### `GET /`

Returns a static info object. No parameters. No authentication.

**Response `200`:**
```json
{
  "message": "Marketplace Parsing API",
  "documentation": "/docs",
  "health": "/health"
}
```

---

### `GET /health`

Returns service health, uptime (seconds), and list of registered marketplace names.

**Response `200` (schema: `HealthResponse`):**
```json
{
  "status": "healthy",
  "uptime": 123.45,
  "supported_marketplaces": ["wildberries", "ozon", "yandex_market", "megamarket"]
}
```

| Field | Type | Description |
|---|---|---|
| `status` | `string` | Always `"healthy"` while the process is alive |
| `uptime` | `float` | Seconds since the `ParsingService` singleton was created |
| `supported_marketplaces` | `string[]` | Keys registered in `CrawlerFactory` |

---

### `POST /parse`

Parses a product page from a supported marketplace URL.

**Authentication:** `X-API-Key` header required.
- Missing header → `401 Unauthorized`
- Wrong key value → `403 Forbidden`

**Request body (schema: `ParseRequest`):**
```json
{
  "url": "https://www.wildberries.ru/catalog/17386348/detail.aspx"
}
```

| Field | Type | Required | Validation |
|---|---|---|---|
| `url` | `HttpUrl` | Yes | Must contain a supported marketplace domain (see domain list below) |

**Supported domains (validated by Pydantic `field_validator`):**
- `wildberries.ru`, `wildberries.by`, `wildberries.kz`
- `ozon.ru`, `ozon.by`, `ozon.kz`
- `market.yandex.ru`, `market.yandex.by`
- `megamarket.ru`

URLs not matching any supported domain return HTTP 422 (Pydantic validation error).

**Response `200` — Success (schema: `ParseResponse`):**
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

**Response `200` — Failure (non-HTTP exceptions are caught and returned with `success: false`):**
```json
{
  "success": false,
  "data": null,
  "error": "Unexpected error description",
  "marketplace": null,
  "processing_time": 0.45
}
```

**Response `400` — Unsupported marketplace or bad URL:**
```json
{
  "detail": "Unsupported marketplace URL: https://example.com/product"
}
```

**Response `401` — Missing API key:**
```json
{
  "detail": "API key is required. Add 'X-API-Key' header"
}
```

**Response `403` — Invalid API key:**
```json
{
  "detail": "Invalid API key"
}
```

**Response `422` — Invalid request body (Pydantic validation):**
Standard FastAPI 422 response with details on which field failed validation.

**Side effects:**
- May spawn a `multiprocessing.Process` for cookie harvesting (Ozon, MegaMarket) if cookies are expired or missing
- May write `data/ozon_cookies.json` or `data/mega_market_cookies.json`
- May write `data/wb_upstreams.json` if WB upstreams are not cached

| Field | Type | Nullable | Description |
|---|---|---|---|
| `success` | `bool` | No | `true` when parsing completed without unhandled exception |
| `data` | `object` | Yes | `ProductCard.to_dict()` output when `success=true`; `null` otherwise |
| `error` | `string` | Yes | Error description when `success=false`; `null` otherwise |
| `marketplace` | `string` | Yes | Detected marketplace key; `null` when detection failed |
| `processing_time` | `float` | Yes | Total seconds from request receipt to response |

---

### `GET /marketplaces`

Returns the list of registered marketplace names and hardcoded example URLs.

**Response `200`:**
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

Detects the marketplace for a given URL without performing a parse.

**Query parameter:**

| Name | Type | Required | Description |
|---|---|---|---|
| `url` | `HttpUrl` | Yes | URL to detect marketplace for |

**Response `200` — Success:**
```json
{
  "url": "https://www.wildberries.ru/catalog/17386348/detail.aspx",
  "marketplace": "wildberries",
  "status": "supported"
}
```

**Response `400` — Unsupported URL:**
```json
{
  "detail": "Unsupported marketplace URL: https://unknown.com/product"
}
```

## 4. Authentication rules

- Authentication uses a static `X-API-Key` header
- The key value is stored in `settings.security.api_key` (loaded from `data/settings.yml`)
- Authentication is implemented in the `verify_api_key` dependency, not in the endpoint body
- The `GET /health`, `GET /`, `GET /marketplaces`, and `GET /parse/detect` endpoints are intentionally unauthenticated
- Never move authentication logic out of the `verify_api_key` dependency and into handler functions

## 5. Middleware behavior

An HTTP middleware logs every incoming request (`DEBUG`) and every completed response (`DEBUG`), including method, URL, status code, and duration in seconds.

## 6. Swagger UI

The Swagger UI at `/docs` and `/redoc` is only available when `debug.active: true` in `data/settings.yml`. In production, these endpoints return `404`.

## 7. Change workflow (mandatory)

1. Update this document with the new endpoint specification
2. Implement the endpoint in `api.py`
3. Add or update Pydantic models in `models/product.py`
4. Write tests covering: happy path, missing auth, bad input, service failure
5. Verify the endpoint appears correctly in Swagger UI (debug mode)

Never implement an endpoint without updating this document first.

## 8. AI-specific rules

- Never invent new endpoints without documenting them here first
- Never change the `ParseResponse` schema without updating Section 3 of this file and `rules/database/schema.md`
- Never add authentication to currently unauthenticated endpoints without a business justification documented in this file
- Never return a different HTTP status code than documented above for an existing scenario

## Forbidden behaviors

- ❌ Undocumented endpoints
- ❌ Business logic inside endpoint handler functions (delegate to `ParsingService`)
- ❌ Missing auth on `POST /parse`
- ❌ Returning raw exceptions or stack traces in response bodies
- ❌ Changing the response schema without updating this file
- ❌ Swallowing `HTTPException` — it must be re-raised

## Checklist

- [ ] All endpoints documented
- [ ] Request schemas specified
- [ ] All response statuses documented
- [ ] Auth requirements stated per endpoint
- [ ] Side effects listed for `POST /parse`
- [ ] Middleware behavior documented
- [ ] Swagger availability rule documented
- [ ] No undocumented endpoints in `api.py`
