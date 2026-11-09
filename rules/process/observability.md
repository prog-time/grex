# Observability Rules

> Purpose: Guarantee that every feature is observable in production. Prevent "black box" systems that cannot be debugged after deployment.
> Context: Read this file before implementing any endpoint, crawler, background process, or infrastructure change.
> Version: 1.1

## 1. Core principle

If you cannot observe it, you cannot operate it.

- Every crawl attempt must produce logs
- Every failure must be traceable
- Every cookie harvest must log its outcome
- Retry attempts must be logged with their attempt number
- Silent failures are forbidden

## 2. Logging library and configuration

This project uses **Loguru** exclusively. Do not log through the standard library `logging` module in project code. Records that third-party libraries write through `logging` (uvicorn, httpx, patchright) are routed into Loguru by `InterceptHandler` in `utils/logger.py`, so they get the same format, redaction and destinations.

```python
# ✅ Correct
from loguru import logger
logger.info("parse_started", ...)
logger.warning("retry_attempt", ...)
logger.error("parse_failed", ...)

# ❌ Incorrect
import logging
logging.info("parse started")
print("parse started")
```

Logger is configured in `utils/logger.py` via `setup_logger()`, called **once per process**: at application startup in `api.py`, and in a browser subprocess started with `spawn` by `_child_main()` in `crawlers/base.py` (`setup_logger(child=True)`). Do not reconfigure the logger anywhere else. `uvicorn.run` gets `log_config=None` so uvicorn does not install its own handlers.

## 3. Log level conventions

| Level | Use in this project |
|-------|-----|
| `DEBUG` | Detailed diagnostic info: marketplace detection, upstream host selection, cookie file paths, proxy counts, widget names being parsed |
| `INFO` | Key business events: parse started (`logger.info`), upstreams saved, cookies saved |
| `SUCCESS` | Successful parse completion (`logger.success(f"Successfully parsed {url} in {time}s")`) |
| `WARNING` | Per-field extraction failures, retry triggers, bad proxy detection, cookie validation errors |
| `ERROR` | Unrecoverable errors: parse completely failed, cookie process returned error, upstreams fetch failed |

```python
# ✅ Correct — uses SUCCESS for completed parse
logger.success(f"Successfully parsed {request.url} in {processing_time:.2f}s")

# ✅ Correct — uses WARNING for non-fatal field extraction failure
logger.warning(f"Failed to get product prices from WB #{product_sku}: {e}")

# ❌ Incorrect — using ERROR for a recoverable per-field issue
logger.error(f"No price found")
```

## 4. Structured context rules

Each log message must include relevant context identifiers. Messages are written as f-strings; identifiers that a reader filters by are **also** attached as context with `logger.contextualize(...)`, which puts them as top-level fields into `logs/app.jsonl` (ADR 0010).

Context that is already set — do not set it again in nested code:

| Where | Fields |
|---|---|
| HTTP middleware `log_requests` (`api.py`) | `request_id` (client `X-Request-ID` if it matches `[A-Za-z0-9._-]{1,64}`, else a new UUID; returned in the `X-Request-ID` response header) |
| `ParsingService.parse_product` (`services/parsing.py`) | `url`, `marketplace` |
| `_check_one` (`services/monitoring.py`), admin background parse and refetch | `target_id` |
| `_run_job` (`services/api_jobs.py`) | `job_id` |
| `_run_all_background` (`admin/resources/targets.py`) | `session_id` |
| Browser subprocess | the caller's context, handed over by `_run_in_process` |

### Actions: who did what

Every line in `app.jsonl` has `actor`:

| `actor` | Set by |
|---|---|
| `admin:<username>` | `require_admin` (admin API routes) via `set_actor()`; for starlette-admin pages, the session after the request |
| `api_key_id:<id>` | `verify_api_key` (public API) via `set_actor()`. Not `api_key:` — the redaction would hide it |
| `anonymous` | HTTP request without a valid admin session or API key |
| `system` | anything outside an HTTP request: scheduler, workers, startup |

Background tasks started from a request with `asyncio.create_task` inherit the request's `actor` and `request_id`.

The request middleware writes one `event="http.request"` line per request with `actor`, `http_method`, `path`, `status`, `duration_ms`, `client_ip`. Level: `INFO` for every change (non-GET method) and every `/v1/*` call, `WARNING` for 401/403 on those, `ERROR` for 5xx, `DEBUG` for page views, polling, static files, health and the log stream. uvicorn's access log is off (`access_log=False` in `uvicorn.run`): it duplicated this line. uvicorn's other records (startup, errors) still come through.

Named events (`event` field) for actions that a reader looks for directly:

| `event` | Where |
|---|---|
| `http.request` | request middleware, `api.py` |
| `admin.login`, `admin.logout`, `admin.login_failed` (WARNING, with `client_ip`) | `SettingsAuthProvider`, `admin/__init__.py` |
| `parse.finished` with `outcome` = `ok` / `failed` / `error` and `duration_ms` | `ParsingService.parse_product` |

Add a named event with `audit("area.action", "message", **fields)` from `utils/logger.py` when an action matters on its own (security, money, deletion of user data). Do not log request bodies: the object ids belong in the message or in fields. A domain action that already has an INFO line (target deleted, API key revoked, proxy added) does not need a second one: it carries `actor` and `request_id` automatically.

New background entry points (tasks created with `asyncio.create_task`, scheduler loops) wrap their body in `logger.contextualize(...)` with their own identifier. Context values must be short scalars (str/int/float/bool).

```python
# ✅ Correct — includes URL and marketplace context
logger.info(f"Starting parse for {marketplace}: {request.url}")
logger.error(f"Parsing error for {url}: {e}")
logger.warning(f"Failed to get product prices from WB #{product_sku}: {e}")

# ❌ Incorrect — no context
logger.info("Parsing started")
logger.error("Error occurred")
```

Minimum required context per log line:

| Situation | Required context fields |
|---|---|
| Parse start/end | `url`, `marketplace` |
| Crawler field extraction failure | `url`, field name, exception |
| Retry attempt | `url`, attempt number (if available) |
| Cookie lifecycle events | `cookies_file_path` |
| Proxy events | `proxy count` or `proxy string` (never the password) |
| Process/subprocess outcomes | `success/failure`, `error message` |

## 5. HTTP request logging (middleware)

Every HTTP request and response is logged by the middleware in `api.py`:

- Incoming: `DEBUG` — method and URL
- Completed: one `event="http.request"` line with actor, method, path, status, duration; level by the rule in section 4 (changes and API calls at `INFO`)

Do not remove or bypass this middleware. Do not add per-endpoint request logging that duplicates it.

## 6. File-based logging

Log files are written only when `debug.logging_in_file: true` in `data/settings.yml`. The directory is `logs/`, or `$GREX_LOG_DIR` when set (tests set it to a temporary directory).

| File | Format | Rotation | Retention |
|---|---|---|---|
| `logs/YYYY-MM-DD.log` | text, same as stderr | daily at `00:00` | 7 days |
| `logs/app.jsonl` | one JSON object per line (below) | daily at `00:00` | 14 days |

Level: same as stderr (DEBUG in debug mode, INFO in production).

JSON line fields (ADR 0010):

| Field | Always | Meaning |
|---|---|---|
| `ts` | yes | UTC, ISO 8601 with milliseconds, `Z` suffix |
| `level` | yes | `debug` / `info` / `warning` / `error` / `critical` (`SUCCESS` → `info`) |
| `service` | yes | `grex` |
| `version` | yes | from `VERSION` |
| `env` | yes | `$GREX_ENV`, default `production` |
| `msg` | yes | the message, redacted |
| `logger` | yes | module, or the stdlib logger name for intercepted records (`uvicorn.access`) |
| `function`, `line` | yes | call site |
| `error.type`, `error.message`, `error.stack` | on exceptions | from `logger.exception(...)` / `opt(exception=...)` |
| `context` | demo mode | `DEMO MODE` |
| `actor` | yes | who: `admin:<user>`, `api_key_id:<id>`, `anonymous`, `system` |
| `event` | named events | `http.request`, `admin.login`, `parse.finished`… |
| context fields | when set | `request_id`, `url`, `marketplace`, `target_id`, `job_id`, `session_id`, `outcome`, `duration_ms`… |

Context keys that collide with a field above are dropped. Do not hardcode log file paths. Always use the `setup_logger()` configuration.

## 7. Demo mode

When `settings.is_demo == true`, the log context label is replaced with `"DEMO MODE"` for all log lines. This is injected by the record patcher `_patch_record()` in `utils/logger.py`. Do not replicate this logic elsewhere.

## 8. Error tracking rules

```python
# ✅ Correct — log error with its traceback
except Exception as e:
    logger.exception(f"Unexpected error parsing {request.url}: {e}")
    return ParseResponse(success=False, error=str(e), ...)

# ❌ Incorrect — silent swallow
except Exception as e:
    pass
```

All `except` blocks in crawlers must log at minimum a `WARNING`. Blocks that catch unexpected exceptions must log at `ERROR`.

Use `logger.exception(...)` (or `logger.opt(exception=exc).error(...)` outside an `except` block) for unexpected errors: the traceback goes to every destination and into `error.stack` of `app.jsonl`. Never `traceback.print_exc()`: it bypasses the logger, the files and the redaction.

## 9. Cookie and process observability

Cookie lifecycle events must be logged:

```python
# ✅ Correct
logger.debug(f"Cookies data loaded from file: {self.cookies_file_path}")
logger.debug("Cookies saved successfully via process")
logger.error(f"Process error: {result['error']}")
logger.error("Process completed but no result received")
```

Never silently ignore a failed cookie harvest — it will cause silent parse failures downstream.

## 10. What not to log

- Never log the value of `security.api_key`
- Never log full cookie dictionaries (they contain session tokens)
- Never log proxy credentials — neither password nor login (some providers put the account id in the login)
- Never log full HTML page content

A safety net exists: `redact()` in `utils/logger.py` runs on every record (message, context values, exception text) before any destination and hides `scheme://login:password@`, `login:password@host:port`, Telegram bot tokens, `Bearer`/`Basic` credentials, `Cookie` headers, `key=value` pairs for token/password/secret/api-key keys, and secret values from `data/settings.yml`. It is a net, not a licence: still do not pass secrets to the logger. When a new kind of secret appears, add its pattern to `_REDACT_PATTERNS` and a case to `tests/test_logger.py`.

## 11. Definition of done

A feature is not complete unless:
- All new code paths have at minimum one `INFO` or `SUCCESS` log on the happy path
- All `except` blocks log a `WARNING` or `ERROR`
- Cookie lifecycle events are logged
- No silent exception swallowing introduced

## Forbidden behaviors

- ❌ `print()` statements for diagnostic output
- ❌ `import logging; logging.info(...)` instead of Loguru
- ❌ Silent `except: pass` blocks
- ❌ Logging API keys, cookie values, or proxy passwords
- ❌ Calling `setup_logger()` more than once per process
- ❌ `traceback.print_exc()` — use `logger.exception(...)`
- ❌ Removing or bypassing the HTTP request middleware

## Checklist

- [ ] Loguru used for all logging
- [ ] Correct log levels applied per convention
- [ ] URL/marketplace/SKU context in all relevant log lines
- [ ] No silent exception swallows
- [ ] Cookie lifecycle events logged
- [ ] Proxy events logged without credential exposure
- [ ] HTTP middleware intact
- [ ] No `print()` statements in production code paths
- [ ] File logging configured only via `setup_logger()`
- [ ] New background entry points set their id with `logger.contextualize(...)`
- [ ] Unexpected errors logged with `logger.exception(...)`
