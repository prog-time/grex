# Observability Rules

> Purpose: Guarantee that every feature is observable in production. Prevent "black box" systems that cannot be debugged after deployment.
> Context: Read this file before implementing any endpoint, crawler, background process, or infrastructure change.
> Version: 1.0

## 1. Core principle

If you cannot observe it, you cannot operate it.

- Every crawl attempt must produce logs
- Every failure must be traceable
- Every cookie harvest must log its outcome
- Retry attempts must be logged with their attempt number
- Silent failures are forbidden

## 2. Logging library and configuration

This project uses **Loguru** exclusively. The standard library `logging` module must not be used directly except in `crawlers/base.py` where it exists only as a legacy stub.

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

Logger is configured in `utils/logger.py` via `setup_logger()`, called once at application startup in `api.py`. Do not reconfigure the logger anywhere else.

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

Each log message must include relevant context identifiers. The project uses f-string formatting (Loguru style, not JSON structured logging).

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
- Completed: `DEBUG` — method, URL, status code, and duration in seconds

Do not remove or bypass this middleware. Do not add per-endpoint request logging that duplicates it.

## 6. File-based logging

Log files are written only when `debug.logging_in_file: true` in `data/settings.yml`.

- Location: `logs/YYYY-MM-DD.log`
- Rotation: daily at `00:00`
- Retention: 7 days
- Level: same as stderr (DEBUG in debug mode, INFO in production)

Do not hardcode log file paths. Always use the `setup_logger()` configuration.

## 7. Demo mode

When `settings.is_demo == true`, the log context label is replaced with `"DEMO MODE"` for all log lines. This is injected via the `add_default_extra` filter in `setup_logger()`. Do not replicate this logic elsewhere.

## 8. Error tracking rules

```python
# ✅ Correct — log error with context before re-raising
except Exception as e:
    logger.error(f"Unexpected error parsing {request.url}: {e}")
    traceback.print_exc()
    return ParseResponse(success=False, error=str(e), ...)

# ❌ Incorrect — silent swallow
except Exception as e:
    pass
```

All `except` blocks in crawlers must log at minimum a `WARNING`. Blocks that catch unexpected exceptions must log at `ERROR`.

`traceback.print_exc()` is currently used in `api.py` and `services/parsing.py` for developer debugging. In new code, prefer `logger.exception(...)` which includes the traceback automatically.

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
- Never log full proxy strings including passwords (log host/count only)
- Never log full HTML page content

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
- ❌ Calling `setup_logger()` more than once
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
