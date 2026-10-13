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

All public endpoints are defined in `api.py` on two routers:

- `api_router` — endpoints that existed before versioning. Mounted under `/v1` **and** at the original unversioned path, marked `deprecated` in OpenAPI so old clients keep working.
- `v1_router` — endpoints added after versioning. Mounted under `/v1` only.

Authentication via `X-API-Key` header is required for `parse`, `parse/batch`, `jobs`, `monitors` and `alerts`. All other public endpoints are unauthenticated; `/admin/*` endpoints require an admin session.

| Method | Path | Auth | Description |
|---|---|---|---|
| `GET` | `/` | No | Root info, returns service name and key endpoint links (unversioned) |
| `GET` | `/v1/health` | No | Service health check with uptime and supported marketplaces |
| `POST` | `/v1/parse` | Yes (`X-API-Key`) | Parse a product URL and return product card data |
| `POST` | `/v1/parse/batch` | Yes (`X-API-Key`) | Parse up to 100 URLs; responds when all are done |
| `GET` | `/v1/marketplaces` | No | List supported marketplace names |
| `GET` | `/v1/parse/detect` | No | Detect marketplace from a URL without parsing |
| `POST` | `/v1/jobs` | Yes (`X-API-Key`) | Create a background parse job (up to 1000 URLs), `202` |
| `GET` | `/v1/jobs` | Yes (`X-API-Key`) | List the key's most recent jobs (`limit` 1–100, default 20), without results |
| `GET` | `/v1/jobs/{id}` | Yes (`X-API-Key`) | Job status, progress and results finished so far |
| `POST` | `/v1/monitors` | Yes (`X-API-Key`) | Start price monitoring of a URL, `201` |
| `GET` | `/v1/monitors` | Yes (`X-API-Key`) | The key's monitors with their latest price |
| `GET` | `/v1/monitors/{id}` | Yes (`X-API-Key`) | One monitor |
| `PATCH` | `/v1/monitors/{id}` | Yes (`X-API-Key`) | Partial update: `active`, `interval_seconds` (null = default), `name` |
| `DELETE` | `/v1/monitors/{id}` | Yes (`X-API-Key`) | Delete the monitor with its history and alerts, `204` |
| `GET` | `/v1/monitors/{id}/history` | Yes (`X-API-Key`) | Price points, oldest → newest (`limit` 1–2000, default 500) |
| `GET` | `/v1/alerts` | Yes (`X-API-Key`) | Price-change alerts of the key's monitors, newest first (`since`, `limit` 1–500) |

Unversioned `/health`, `/parse`, `/parse/batch`, `/marketplaces`, `/parse/detect` are deprecated aliases of the `/v1` endpoints. The specifications below use the unversioned names; they apply to both paths.

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

Returns the list of registered marketplace names.

**Response `200`:**
```json
{
  "supported_marketplaces": ["wildberries", "ozon", "yandex_market", "megamarket"]
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

### `POST /v1/jobs`, `GET /v1/jobs/{id}`

Asynchronous counterpart of `/parse/batch` (`services/api_jobs.py`, tables `api_jobs` / `api_job_items`).

**Request:** `{"urls": [...1–1000 supported URLs], "concurrency": null | 1–200}` — same validation as `/parse/batch`.

**Response `202`** (and `GET /v1/jobs/{id}` → `200`):
```json
{
  "id": "uuid", "status": "running", "total": 2, "done": 1, "succeeded": 1, "failed": 0,
  "error": null, "created_at": "...", "started_at": "...", "finished_at": null,
  "results": [{"url": "...", "success": true, "data": {...}, "error": null, "marketplace": "wildberries", "processing_time": 2.1}]
}
```

- `status`: `pending` → `running` → `completed`; `failed` only when the job as a whole broke (`error` explains). Per-URL failures are items with `success: false` in a `completed` job.
- `results` holds finished URLs only, in request order; it fills in while the job runs.
- A job is visible only to the API key that created it — any other key gets `404`.
- At most 5 unfinished jobs per key — over the limit `POST` returns `429`.
- Jobs run as in-process asyncio tasks: jobs unfinished at shutdown are marked `failed` ("Interrupted by server restart") on the next startup. This assumes a single server process; do not run multiple workers against one DB.
- Results are returned to the client only — jobs do not write to the admin panel's Products.

### `/v1/monitors`, `/v1/alerts`

Public-API price monitoring (`services/api_monitors.py`, schemas in `models/monitor.py`).

- An API monitor is a `ParseTarget` row with `api_key_id` set. The regular scheduler (`services/monitoring.py`) checks it, records `PriceSnapshot`s and logs `PriceAlert`s exactly like admin targets — but it does **not** upsert `ParsedProduct`, so API products never show up in the admin panel. The latest price comes from the newest snapshot.
- **Ownership is mandatory in every query.** API endpoints filter by `ParseTarget.api_key_id == <key id>`; admin views and the Telegram report filter with `admin_owned()` (targets) and `not_api_owned(<Model>.target_id)` (snapshots, alerts) from `models/parse_target.py`. Any new query over `parse_targets`, `price_snapshots` or `price_alerts` must use one of them — `tests/test_api_monitors.py::TestAdminIsolation` covers the existing ones.
- The previous price for change detection is scoped by owner: admin targets by URL (excluding API snapshots), API monitors by their own target id.
- URLs are normalized (`crawler.normalize_url`) before storing; one key can't monitor the same URL twice (`409`). Limit: 500 monitors per key (`429`). `interval_seconds` below `monitoring.min_interval_seconds` → `422`.
- Foreign or unknown monitor id → `404` (never `403`, so ids of other keys can't be probed).
- Revoking a key pauses its monitors; deleting a revoked key in the admin panel deletes its monitors, their snapshots and alerts, and its jobs.
- Alert thresholds are global (`monitoring.drop_percent` / `rise_percent`).

## 4. Authentication rules

- Authentication uses an `X-API-Key` header; keys are created and revoked in the admin panel (Настройки → API-ключи)
- Keys are stored hashed in the `api_keys` table (`models/api_key.py`, `services/api_keys.py`); `settings.security.api_key` is only imported once on startup as a seed
- If key storage (DB) is unavailable, authenticated endpoints return `503`
- Authentication is implemented in the `verify_api_key` dependency, not in the endpoint body
- `verify_api_key` returns the key's id; endpoints that own data (`/v1/jobs`, `/v1/monitors`, `/v1/alerts`) take it via `Depends(verify_api_key)` to scope access
- The `GET /`, `health`, `marketplaces` and `parse/detect` endpoints are intentionally unauthenticated
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
