# Proxy Management Domain Rules

> Purpose: This file defines mandatory rules for proxy configuration, loading, validation, and rotation. It covers the `ProxyManager` class, `data/proxy.txt` format, settings flags, and per-crawler proxy injection patterns.
> Context: Read this file before modifying `core/managers.py`, proxy-related settings in `data/settings.yml`, or proxy injection logic in any crawler.
> Version: 1.0

## 1. Core principle

Proxies are optional but, when active, must be valid and randomly rotated on every request attempt.

- Proxy usage is controlled exclusively by `settings.proxy.active`
- The `ProxyManager` is the single source of proxy state
- All proxies must be validated on load — invalid entries abort startup
- Proxy selection must be random — never sequential or sticky per crawler instance
- Proxy credentials must never be hardcoded in source code

## 2. What is this domain?

The proxy management domain provides authenticated proxy selection for crawlers that need to bypass IP-based rate limiting or geo-restrictions. It reads proxy definitions from `data/proxy.txt`, validates format, and exposes a thread-safe random-selection interface.

Currently, only `MegaMarketCrawler` uses proxy injection during parsing. All other crawlers (`WildberriesCrawler`, `OzonCrawler`, `YandexMarketCrawler`) do not currently use `ProxyManager` but may be extended to do so.

### Key concepts

| Concept | Description |
|---|---|
| `ProxyManager` | Class-level singleton managing proxy list load and random selection |
| `proxies` | Class-level list shared across all instances. Populated once via `load_proxies()` |
| `_proxy_lock` | `asyncio.Lock()` preventing concurrent duplicate loads |
| `proxy.txt` | One proxy per line. Format: `[protocol://]user:password@host:port` |
| `proxy.active` | Boolean settings flag. If `false`, `get_random_proxy()` returns `None` |
| `proxy.type` | Default protocol (`http` or `socks5`) prepended when `://` is absent from a proxy line |

## 3. Business rules

**BR-001** — Proxy usage is globally controlled by `settings.proxy.active`. When `false`, `ProxyManager.get_random_proxy()` must return `None` and no proxy must be applied to any session.
_Enforced in:_ `core/managers.py @ get_random_proxy()`

**BR-002** — Proxies are loaded lazily on first call to `get_random_proxy()`. They are never loaded at application startup. This prevents startup failure when proxies are not needed.
_Enforced in:_ `core/managers.py @ get_random_proxy()`

**BR-003** — The proxy list is a class-level variable (`ProxyManager.proxies = []`). It is loaded once and reused across all crawler instances and requests. Do not reload on every request.
_Enforced in:_ `core/managers.py @ load_proxies()` (guarded by `if not cls.proxies`)

**BR-004** — `load_proxies()` must be protected by `_proxy_lock` to prevent race conditions when multiple concurrent requests trigger the first load simultaneously.
_Enforced in:_ `core/managers.py @ load_proxies()`

**BR-005** — If `data/proxy.txt` does not exist, `load_proxies()` must raise `FileNotFoundError`. This is an unrecoverable configuration error.
_Enforced in:_ `core/managers.py @ load_proxies()`

**BR-006** — Every proxy line in `data/proxy.txt` is validated against the regex pattern on load. Any invalid line must raise `ValueError` with the offending line in the message. The file is not partially loaded.
_Enforced in:_ `core/managers.py @ load_proxies()`, `is_valid_proxy()`

**BR-007** — If `data/proxy.txt` exists but contains no valid lines (empty or all blank), `load_proxies()` must raise `ValueError("The proxy.txt file is empty or does not contain valid proxies")`.
_Enforced in:_ `core/managers.py @ load_proxies()`

**BR-008** — `get_random_proxy()` must use `random.choice(cls.proxies)` for selection. No weighting, no round-robin, no sticky assignment per crawler.
_Enforced in:_ `core/managers.py @ get_random_proxy()`

**BR-009** — When a proxy string lacks a `://` scheme prefix, `get_random_proxy()` must prepend the configured `proxy.type` from settings before returning it.
_Enforced in:_ `core/managers.py @ get_random_proxy()`

**BR-010** — For Playwright-based cookie harvesting in `MegaMarketCrawler`, the proxy must be parsed via `ProxyManager.split_proxy_string()` into a `ProxySettings` object. Never pass the raw proxy string to Playwright.
_Enforced in:_ `crawlers/megamarket.py @ _process_save_cookies()`

**BR-011** — MegaMarket must call `get_random_proxy()` on each retry iteration, not once before the retry loop. This ensures a fresh proxy is selected on each attempt.
_Enforced in:_ `crawlers/megamarket.py @ parse()`

**BR-012** — The proxy format in `data/proxy.txt` must be `username:password@server:port`. The `protocol://` prefix is optional in the file but will be prepended by `ProxyManager` using the configured type.
_Enforced in:_ `core/managers.py @ is_valid_proxy()` (regex pattern)

## 4. Proxy format specification

```
# Valid formats in data/proxy.txt:
username:password@server:port
http://username:password@server:port
socks5://username:password@server:port

# Invalid formats:
server:port               (missing credentials)
username:password@server  (missing port)
username@server:port      (missing password)
```

## 5. Configuration reference

```yaml
# data/settings.yml
proxy:
  active: false   # Set to true to enable proxy rotation
  type: "http"    # "http" or "socks5" — used when proxy line has no protocol prefix
```

## 6. AI-specific rules

- Never hardcode proxy credentials in any source file
- Never change `ProxyManager.proxies` from instance methods — it is a class variable
- Never call `load_proxies()` directly from crawlers — always use `get_random_proxy()`
- Never pass raw proxy strings to Playwright — always use `split_proxy_string()` and `ProxySettings`
- Never skip proxy selection on retries for MegaMarket — a fresh proxy per attempt is required

## 7. Definition of done

A proxy management change is not complete until:

- Business rules updated and numbered
- `data/proxy.txt` format specification confirmed
- Settings schema in `rules/database/schema.md` updated if new config keys added
- Tests cover: load success, invalid line rejection, empty file rejection, missing file rejection, `active=false` short-circuit

## Forbidden behaviors

- ❌ Hardcoded proxy strings in code
- ❌ Reloading proxies on every request
- ❌ Skipping format validation on load
- ❌ Passing raw proxy string to Playwright
- ❌ Using the same proxy for all retries in MegaMarket
- ❌ Accessing `ProxyManager.proxies` directly from crawler code

## Checklist

- [ ] `proxy.active` checked before any proxy usage
- [ ] `load_proxies()` protected by `_proxy_lock`
- [ ] Every proxy line validated on load
- [ ] Invalid lines raise `ValueError` with line content
- [ ] Empty file raises `ValueError`
- [ ] Missing file raises `FileNotFoundError`
- [ ] Random selection via `random.choice()`
- [ ] Protocol prefix prepended when missing
- [ ] MegaMarket uses `split_proxy_string()` for Playwright
- [ ] No hardcoded credentials
- [ ] Tests written for all error cases
