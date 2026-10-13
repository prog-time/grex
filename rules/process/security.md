# Security Rules

> Purpose: Ensure every change is secure by default. Prevent common classes of vulnerabilities frequently introduced by AI-generated code.
> Context: Read this file before implementing any feature that touches authentication, input handling, API keys, cookies, proxy credentials, or configuration.
> Version: 1.0

## 1. Core principle

Security is not optional or deferred.
- Always secure by default
- Always validate external input via Pydantic
- Always enforce authentication explicitly
- Never trust generated code blindly
- Never assume a route or service is safe without reading this file

## 2. API key authentication rules

Clients send an API key in the `X-API-Key` header, validated by the `verify_api_key` FastAPI dependency. Keys are managed in the admin panel (Настройки → API-ключи) and stored in the `api_keys` table — see `services/api_keys.py`.

```python
# ✅ Correct — key looked up via the service, errors are explicit
async def verify_api_key(api_key: str = Depends(X_API_KEY)) -> str:
    if not api_key:
        raise HTTPException(status_code=401, detail="API key is required...")
    valid = await verify_key(api_key)   # None → key storage (DB) unavailable
    if valid is None:
        raise HTTPException(status_code=503, detail="API key storage is unavailable")
    if not valid:
        raise HTTPException(status_code=403, detail="Invalid API key")
    return api_key

# ❌ Incorrect — key hardcoded in source
VALID_KEY = "abc123secret"
if api_key != VALID_KEY:
    raise HTTPException(...)
```

**Rules:**
- Keys are generated server-side (`grx_` + `secrets.token_urlsafe(32)`); manual key entry is intentionally not supported
- Only the SHA-256 hash of a key is stored; the plaintext is returned once, by `POST /admin/api-keys/create`, and never logged
- Revoked keys must fail immediately — never cache verification results
- Key verification fails closed: if the DB is unavailable the API returns `503`, it never falls back to `settings.security.api_key`
- `settings.security.api_key` is only a one-time seed: on startup it is imported into `api_keys` unless a row with its hash already exists (so revoking it sticks)
- Authentication is enforced via `dependencies=[Depends(verify_api_key)]` at the route level — never inside the handler body
- `POST /parse` and `POST /parse/batch` are the only endpoints requiring an API key — this is intentional by design

## 3. Input validation rules

All HTTP input is validated by Pydantic before reaching any business logic.

```python
# ✅ Correct — URL validated by Pydantic HttpUrl and custom validator
class ParseRequest(BaseModel):
    url: HttpUrl

    @field_validator('url')
    def validate_marketplace_url(cls, v):
        if not any(domain in str(v).lower() for domain in supported_domains):
            raise ValueError(f"Unsupported marketplace")
        return v

# ❌ Incorrect — raw string passed directly to crawler
async def parse_product(url: str):
    await parsing_service.parse_product(url=url)
```

- Never accept raw `str` for URLs — always use `HttpUrl` from Pydantic
- The `validate_marketplace_url` validator is the first line of defense against invalid domains
- Never bypass Pydantic validation by constructing models directly without validation

## 4. Secrets management rules

```python
# ✅ Correct — secret loaded from settings file
password = settings.get("admin", {}).get("password")

# ❌ Incorrect — hardcoded secret
password = "admin"
```

- Config secrets (admin credentials, proxy credentials) must be stored in `data/settings.yml` or `data/proxy.txt`
- These files must be listed in `.gitignore` or equivalent
- Never commit `data/settings.yml` with real secrets to version control
- `settings.yml.example` ships `security.api_key: ""`, so a fresh install has no API key until one is created in the admin panel — never put a real or default key back into the example
- The key older examples shipped (`DEFAULT_LEGACY_KEY` in `services/api_keys.py`) is public — on installations that imported it, it is flagged as insecure in the admin panel and logged as a warning on startup while active; revoke it before production deployment

## 5. Cookie security rules

- Browser-harvested cookies (Ozon, MegaMarket) contain marketplace session tokens
- These cookies are stored in `data/ozon_cookies.json` and `data/mega_market_cookies.json`
- These files must never be committed to version control
- Cookie values must never appear in log output
- When a cookie expires, the file must be deleted and a fresh harvest initiated (not retried with stale cookies)

```python
# ✅ Correct — delete stale cookies before retry
if "Expecting value" in str(e):
    self.cookies_data = None
    self.delete_cookies_file()

# ❌ Incorrect — retry indefinitely with expired cookies
await self.session.get(url, cookies=self.cookies_data)
```

## 6. Proxy credential rules

- Proxy credentials are stored in `data/proxy.txt`
- This file must never be committed to version control
- Proxy strings must never be logged in full (password field exposed)
- When logging proxy events, log only count or host — never the full credential string

```python
# ✅ Correct — log count only
logger.debug(f"Proxies loaded: {len(cls.proxies)}")

# ❌ Incorrect — logs full proxy string with password
logger.debug(f"Using proxy: {proxy}")  # proxy = "http://user:pass@host:port"
```

## 7. External request security rules

- The `curl-cffi AsyncSession` impersonates `chrome136` — this is intentional for anti-bot bypass and must not be changed to a plain `requests` session
- TLS verification is disabled (`verify=False`) only for `MegaMarketCrawler` — this is intentional for that marketplace but must not be applied globally
- Never add `verify=False` to `WildberriesCrawler`, `OzonCrawler`, or `YandexMarketCrawler` sessions

```python
# ✅ Correct — verify=False only for MegaMarket where required
self.session = AsyncSession(impersonate="chrome136", verify=False)

# ❌ Incorrect — disabling TLS verification globally or for other crawlers
self.session = AsyncSession(impersonate="chrome136", verify=False)  # in WildberriesCrawler
```

## 8. Subprocess/process security rules

- Playwright-based cookie harvesting runs in a `multiprocessing.Process`
- The `result_queue` (multiprocessing `Queue`) is the only communication channel — never use shared memory or global state for cookie results
- The subprocess must not receive more arguments than needed (URL, proxy, cookies path, queue)

## 9. Error response rules

```python
# ✅ Correct — generic message, no internals exposed
return ParseResponse(success=False, error="Internal server error occurred", ...)

# ❌ Incorrect — stack trace or internal path exposed
return ParseResponse(success=False, error=traceback.format_exc(), ...)
```

- The global exception handler in `api.py` returns `"Internal server error occurred"` — never replace with detailed internal error messages
- Only the `error=str(e)` in the per-endpoint handler is allowed (and only for expected parsing errors, not framework errors)

## 10. Security checks required before merge

- No hardcoded secrets in any `.py` file
- No committed `data/proxy.txt` or `data/settings.yml` with real values
- No new `verify=False` outside `MegaMarketCrawler`
- All new endpoints: authentication requirement stated explicitly
- All new inputs: validated via Pydantic

## Forbidden behaviors

- ❌ Hardcoding API keys, cookies, or proxy credentials in source files
- ❌ Committing `data/proxy.txt` or `data/settings.yml` with real secrets
- ❌ Adding `verify=False` to crawlers that do not require it
- ❌ Logging cookie values or proxy passwords
- ❌ Bypassing Pydantic validation for URL input
- ❌ Removing authentication from `POST /parse`
- ❌ Writing custom cryptography or authentication schemes

## Checklist

- [ ] No hardcoded secrets in new code
- [ ] API key loaded from settings only
- [ ] All new URL inputs use `HttpUrl` and marketplace validator
- [ ] Cookie files not committed
- [ ] Proxy credentials not logged
- [ ] `verify=False` not added to non-MegaMarket crawlers
- [ ] Error responses do not expose internals
- [ ] Authentication dependency not moved out of route decorator
