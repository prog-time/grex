# Product Parsing Domain Rules

> Purpose: This file defines mandatory rules for how product data is extracted, normalized, and returned as a `ProductCard`. It covers field extraction per marketplace, price normalization, image handling, SKU resolution, and the serialization contract.
> Context: Read this file before modifying any extraction logic inside crawler `parse()` methods, the `ProductCard` dataclass, or its `to_dict()` method.
> Version: 1.0

## 1. Core principle

Product data accuracy is the primary service guarantee.

- Every field must be extracted from the authoritative source for that marketplace
- Missing fields are allowed — they must default gracefully, never crash
- Prices must always be in RUB as a `Decimal`, never as raw strings
- Images must always be a list of strings (URLs), never nested objects
- SKU must always be a string, even when numeric in origin
- The `to_dict()` output is the API contract — never change it silently

## 2. What is this domain?

The product parsing domain is responsible for transforming raw marketplace responses (JSON APIs, HTML pages) into a standardized `ProductCard` dataclass. Each crawler implements its own extraction logic, but all must produce the same output structure.

### Key concepts

| Concept | Description |
|---|---|
| `ProductCard` | The single output type for all crawlers. Defined in `models/product.py` |
| SKU | Marketplace-specific product identifier. Always extracted as a `str`, even when numeric |
| `original_price` | Pre-discount price in RUB as `Decimal`. Defaults to `0.0` if unavailable |
| `current_price` | Current selling price in RUB as `Decimal`. Defaults to `0.0` if unavailable |
| `images` | Ordered list of image URLs as strings. Empty list if none found |
| `to_dict()` | Serialization method producing the exact JSON structure returned by `POST /parse` |

## 3. Business rules

**BR-001** — All five `ProductCard` fields (`title`, `sku`, `original_price`, `current_price`, `images`) must be initialized with safe defaults before any network call. This prevents unbound variable errors on partial parse failure.
_Enforced in:_ all crawler `parse()` methods

**BR-002** — Individual field extraction failures must be caught per-field with a `try/except` block that logs a warning. A failure in one field must not abort extraction of other fields.
_Enforced in:_ all crawler `parse()` methods (per-field try/except pattern)

**BR-003** — `original_price` and `current_price` must be stored as `Decimal`, not `float`. Conversion from string prices (e.g. Ozon, Yandex Market) must strip all non-digit characters before constructing `Decimal`.
_Enforced in:_ `crawlers/ozon.py`, `crawlers/yandex_market.py`, `crawlers/megamarket.py`

**BR-004** — Wildberries prices are returned in kopecks (integer). They must be divided by 100 using `Decimal((price) / 100)` before storing.
_Enforced in:_ `crawlers/wildberries.py @ parse()`

**BR-005** — `ProductCard.images` must always be a flat list of URL strings. Yandex Market returns image metadata objects — the crawler must extract only the `"url"` field into the final list.
_Enforced in:_ `crawlers/yandex_market.py @ parse()`

**BR-006** — Yandex Market images must be sorted by resolution (`width * height`) in descending order before extracting URLs. Highest-quality images come first.
_Enforced in:_ `crawlers/yandex_market.py @ parse()`

**BR-007** — The SKU for Wildberries is extracted from the URL path segment at index 2 of the split path: `url.path.split("/")[2]`. This is only valid when the path contains `"catalog"` and `"detail.aspx"`.
_Enforced in:_ `crawlers/wildberries.py @ parse()`

**BR-008** — The SKU for Yandex Market is the last segment of the URL path after stripping query parameters.
_Enforced in:_ `crawlers/yandex_market.py @ parse()`

**BR-009** — The SKU for MegaMarket is extracted from the second-to-last path segment using `.split("-")[-1]`, which isolates the numeric goodsId suffix.
_Enforced in:_ `crawlers/megamarket.py @ parse()`

**BR-010** — The SKU for Ozon comes from the `webGallery` widget's `"sku"` field in the API response, not from the URL.
_Enforced in:_ `crawlers/ozon.py @ parse()`

**BR-011** — `ProductCard.to_dict()` must convert `Decimal` prices to `float` using `float(price) if price else None`. It must never return raw `Decimal` objects — JSON serialization will fail.
_Enforced in:_ `models/product.py @ ProductCard.to_dict()`

**BR-012** — `ProductCard.to_dict()` must not include `parsed_at` in its output. The timestamp is for internal use only.
_Enforced in:_ `models/product.py @ ProductCard.to_dict()`

**BR-013** — The Ozon crawler parses data from the `widgetStates` dictionary of the entrypoint API response. Widget names are dynamic keys. The crawler must match widgets by substring: `"webGallery"` for images/SKU, `"webProductHeading"` for title, `"webPrice-"` for prices.
_Enforced in:_ `crawlers/ozon.py @ parse()`

**BR-014** — When `product_sku` is not found after all extraction attempts, crawlers (Wildberries specifically) must raise `ProductCardParsingException("Product SKU not found")` rather than returning an incomplete card.
_Enforced in:_ `crawlers/wildberries.py @ parse()`

**BR-015** — MegaMarket `original_price` is not returned by the API. It must remain `0.0`. Never attempt to populate it from the MegaMarket response unless the API response structure is verified to include it.
_Enforced in:_ `crawlers/megamarket.py @ parse()` (field never assigned)

## 4. Per-marketplace extraction reference

### Wildberries
| Field | Source | Notes |
|---|---|---|
| `title` | `card.json` → `imt_name` | Fetched from CDN basket host |
| `sku` | URL path segment index 2 | Must be under `/catalog/*/detail.aspx` |
| `original_price` | `card.wb.ru` API → `sizes[0].price.basic` | Kopecks / 100 |
| `current_price` | `card.wb.ru` API → `sizes[0].price.product` | Kopecks / 100 |
| `images` | CDN: `{upstream_host}/{sku}/images/big/{1..n}.webp` | Count from `media.photo_count` |

### Ozon
| Field | Source | Notes |
|---|---|---|
| `title` | `widgetStates["webProductHeading*"]` → `title` | JSON string parsed from widget |
| `sku` | `widgetStates["webGallery*"]` → `sku` | |
| `original_price` | `widgetStates["webPrice-*"]` → `originalPrice` | Strip non-digits, parse as Decimal |
| `current_price` | `widgetStates["webPrice-*"]` → `price` | Strip non-digits, parse as Decimal |
| `images` | `widgetStates["webGallery*"]` → `images[].src` | |

### Yandex Market
| Field | Source | Notes |
|---|---|---|
| `title` | HTML `<h1 data-auto="productCardTitle">` | BeautifulSoup text |
| `sku` | URL last path segment | After stripping `?` query string |
| `original_price` | HTML `<span data-auto="snippet-price-old">` | Strip non-digits |
| `current_price` | HTML `<span data-auto="snippet-price-current">` | Strip non-digits |
| `images` | `<noframes data-apiary="patch">` embedded JSON → sorted by resolution | `origUrl` field |

### MegaMarket
| Field | Source | Notes |
|---|---|---|
| `title` | `productCardMainInfo` API → `goods.title` | |
| `sku` | URL second-to-last path segment, last `-` token | Numeric goodsId |
| `original_price` | Not available | Always `0.0` |
| `current_price` | `productOffers` API → lowest `finalPrice` offer | Sorted ascending by price |
| `images` | `productCardMainInfo` API → `goods.images` | Direct list |

## 5. AI-specific rules

- Never change the `to_dict()` output structure without updating `rules/database/schema.md` and `rules/api/endpoints.md`
- Never assume a field is always present in the API response — always use `.get()` with a default
- Never convert prices to `float` before storing in `ProductCard` — use `Decimal`
- Never return a `None` images list — default to `[]`
- Never invent widget names for Ozon — they are dynamic and must be matched by substring only

## 6. Definition of done

A product parsing change is not complete until:

- All extraction rules updated and numbered
- Per-marketplace extraction reference updated
- `to_dict()` output verified unchanged (or documented if changed)
- Edge cases tested: missing fields, empty API responses, malformed prices

## Forbidden behaviors

- ❌ Returning `None` for `images` instead of `[]`
- ❌ Storing raw string prices in `ProductCard`
- ❌ Using `float()` directly instead of `Decimal` for prices
- ❌ Crashing on a single missing field instead of using defaults
- ❌ Including `parsed_at` in `to_dict()` output
- ❌ Hardcoding widget names for Ozon (they are runtime-generated keys)

## Checklist

- [ ] All fields initialized with safe defaults before network calls
- [ ] Per-field try/except with warning logging
- [ ] Prices stored as `Decimal`
- [ ] WB prices divided by 100
- [ ] Images always a flat list of URL strings
- [ ] YM images sorted by resolution
- [ ] SKU extraction documented per marketplace
- [ ] `to_dict()` excludes `parsed_at`
- [ ] `to_dict()` converts `Decimal` to `float`
- [ ] Tests cover missing fields and malformed responses
