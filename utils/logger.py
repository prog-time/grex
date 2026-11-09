"""Logger setup: stderr, admin SSE stream, text file and JSON file (ADR 0010).

Every record passes through one patcher (:func:`_patch_record`) before any
sink sees it. The patcher fills the default context label, hides secrets in
the message, the context values and the exception text, and pre-renders the
exception, so all sinks show the same redacted content.
"""
from __future__ import annotations

import asyncio
import inspect
import json
import logging
import os
import re
import sys
import traceback
from contextvars import ContextVar
from datetime import timezone
from typing import Any

from loguru import logger

from config import settings

SERVICE_NAME = "grex"

# Directory for the text and JSON log files. Tests point it at a temporary
# directory through GREX_LOG_DIR so they never touch the real logs/.
DEFAULT_LOG_DIR = "logs"

# Context keys that the JSON format owns; a context value with one of these
# names would overwrite a required field, so it is skipped.
_RESERVED_JSON_KEYS = frozenset({
    "ts", "level", "service", "version", "env", "msg", "logger", "function", "line", "error",
})

# Loguru level name -> contract level (debug/info/warning/error/critical).
_LEVEL_MAP = {"TRACE": "debug", "SUCCESS": "info"}

# Third-party loggers that flood DEBUG output with internal details.
_NOISY_STDLIB_LOGGERS = ("httpcore", "hpack", "aiosqlite", "asyncio", "urllib3", "multipart", "python_multipart")

# ---------------------------------------------------------------------------
# Actor: who performed the action a log line belongs to
# ---------------------------------------------------------------------------

# Set by the auth dependencies (require_admin, verify_api_key) for the rest of
# the request, and inherited by tasks the request starts with create_task.
_actor: ContextVar[str | None] = ContextVar("grex_log_actor", default=None)


def set_actor(actor: str) -> None:
    """Mark the current request (and tasks it spawns) as done by *actor*.

    Format: ``admin:<username>`` or ``api_key_id:<key id>`` (not ``api_key:``,
    which the redaction would take for a secret).
    """
    _actor.set(actor)


def audit(event: str, message: str, *, level: str = "INFO", **fields: Any) -> None:
    """Log a user-visible action: one line with a stable ``event`` name.

    ``event`` is a dotted name (``admin.login``, ``api_key.created``) that a
    reader filters by; ``fields`` become top-level JSON fields.
    """
    logger.bind(event=event, **fields).opt(depth=1).log(level, message)


# ---------------------------------------------------------------------------
# Global async queue for SSE log streaming (TASK-12)
# ---------------------------------------------------------------------------
log_queue: asyncio.Queue[str] = asyncio.Queue(maxsize=500)


def sse_sink(message: "logger.Message") -> None:  # type: ignore[name-defined]
    """Loguru sink that feeds formatted log lines into *log_queue*.

    When the queue is full the oldest entry is evicted before inserting the
    new one so the producer never blocks.
    """
    formatted: str = message.strip()  # type: ignore[attr-defined]
    try:
        log_queue.put_nowait(formatted)
    except asyncio.QueueFull:
        # Evict the oldest entry, then insert the new one.
        try:
            log_queue.get_nowait()
        except asyncio.QueueEmpty:
            pass
        try:
            log_queue.put_nowait(formatted)
        except asyncio.QueueFull:
            pass  # Give up silently — the sink must never raise.


# ---------------------------------------------------------------------------
# Secret redaction
# ---------------------------------------------------------------------------

_MASK = "***"

_REDACT_PATTERNS: tuple[tuple[re.Pattern[str], str], ...] = (
    # scheme://login:password@host — proxy and DSN credentials.
    (re.compile(r"(\b[a-zA-Z][a-zA-Z0-9+.-]*://)[^\s/@]+@"), rf"\1{_MASK}:{_MASK}@"),
    # login:password@host:port without a scheme (raw proxy-file lines).
    (re.compile(r"(?<![\w/@*])[^\s:@/'\"*]+:[^\s:@/'\"]+@(?=[\w.-]+:\d+)"), f"{_MASK}:{_MASK}@"),
    # Telegram bot token: <bot id>:<35 chars>, also inside api.telegram.org/bot<token>/.
    (re.compile(r"\d{6,12}:[A-Za-z0-9_-]{30,}"), _MASK),
    # Authorization: Bearer <token> / Basic <token>.
    (re.compile(r"(?i)\b(bearer|basic)\s+[A-Za-z0-9._~+/=-]{8,}"), rf"\1 {_MASK}"),
    # Cookie / Set-Cookie headers: hide the whole header value.
    (re.compile(r"(?i)\b((?:set-)?cookies?['\"]?\s*[:=]\s*)[^\n]+"), rf"\1{_MASK}"),
    # key=value / key: value / "key": "value" for secret-looking keys.
    (
        re.compile(
            r"(?i)\b((?:x[_-]wbaas[_-]token|x[_-]api[_-]key|api[_-]?key|access[_-]token|refresh[_-]token"
            r"|token|password|passwd|secret(?:[_-]key)?|authorization)['\"]?\s*[:=]\s*['\"]?)"
            r"(?!\*\*\*)[^\s,;&'\"}]+"
        ),
        rf"\1{_MASK}",
    ),
)

_SECRET_SETTING_KEYS = ("password", "secret", "token", "api_key", "apikey")
_known_secrets: tuple[str, ...] = ()


def _collect_secret_values(node: Any, key: str = "") -> set[str]:
    """Secret-looking string values from the settings tree (e.g. admin.password)."""
    found: set[str] = set()
    if isinstance(node, dict):
        for k, v in node.items():
            found |= _collect_secret_values(v, str(k).lower())
    elif isinstance(node, list):
        for item in node:
            found |= _collect_secret_values(item, key)
    elif isinstance(node, str) and len(node) >= 6 and any(s in key for s in _SECRET_SETTING_KEYS):
        found.add(node)
    return found


def redact(text: str) -> str:
    """Hide credentials, tokens, API keys and cookies in *text*."""
    if not text:
        return text
    for secret in _known_secrets:
        if secret in text:
            text = text.replace(secret, _MASK)
    for pattern, repl in _REDACT_PATTERNS:
        text = pattern.sub(repl, text)
    return text


# ---------------------------------------------------------------------------
# Record patcher and formats
# ---------------------------------------------------------------------------

def _patch_record(record: Any) -> None:
    """Runs once per record, before every sink."""
    extra = record["extra"]
    if settings.get("is_demo", False):
        extra["info"] = "DEMO MODE"
    else:
        extra.setdefault("info", "SYSTEM")

    # Every line says who it belongs to: an authenticated admin or API key,
    # an anonymous HTTP caller, or the service itself (scheduler, workers).
    if "actor" not in extra:
        extra["actor"] = _actor.get() or ("anonymous" if "request_id" in extra else "system")

    record["message"] = redact(record["message"])
    for key, value in list(extra.items()):
        if isinstance(value, str) and not key.startswith("_"):
            extra[key] = redact(value)

    exc = record["exception"]
    if exc is not None and exc.type is not None:
        stack = redact("".join(traceback.format_exception(exc.type, exc.value, exc.traceback)))
        extra["_exc"] = "\n" + stack.rstrip("\n")
        extra["_error"] = {
            "type": exc.type.__name__,
            "message": redact(str(exc.value)),
            "stack": stack,
        }
    else:
        extra["_exc"] = ""
        extra["_error"] = None


def _escape(text: str) -> str:
    """Escape braces so a pre-rendered value survives Loguru's str.format pass."""
    return text.replace("{", "{{").replace("}", "}}")


def _text_format(record: Any) -> str:
    return (
        "<cyan>{time:HH:mm:ss}</cyan> | "
        f"<cyan>{_escape(settings['soft_version'])}</cyan> | "
        "<level>{level: ^8}</level> | "
        "<cyan>{extra[info]}</cyan> | "
        "<level>{message}</level>{extra[_exc]}\n"
    )


def _plain_format(record: Any) -> str:
    return "{time:HH:mm:ss} | {level: ^8} | {extra[info]} | {message}{extra[_exc]}\n"


def _environment() -> str:
    return os.environ.get("GREX_ENV") or "production"


def build_json_line(record: Any) -> str:
    """One log record as a JSON document following the log contract (ADR 0010)."""
    extra = record["extra"]
    stdlib_name = extra.get("_stdlib_logger")
    doc: dict[str, Any] = {
        "ts": record["time"].astimezone(timezone.utc).isoformat(timespec="milliseconds").replace("+00:00", "Z"),
        "level": _LEVEL_MAP.get(record["level"].name, record["level"].name.lower()),
        "service": SERVICE_NAME,
        "version": settings["soft_version"],
        "env": _environment(),
        "msg": record["message"],
        "logger": stdlib_name or record["name"],
        "function": record["function"],
        "line": record["line"],
    }
    for key, value in extra.items():
        if key.startswith("_") or key == "info" or key in _RESERVED_JSON_KEYS:
            continue
        doc[key] = value
    if extra.get("info") not in (None, "SYSTEM"):
        doc["context"] = extra["info"]
    if extra.get("_error"):
        doc["error"] = extra["_error"]
    return json.dumps(doc, ensure_ascii=False, default=str)


def _json_format(record: Any) -> str:
    record["extra"]["_json"] = build_json_line(record)
    return "{extra[_json]}\n"


# ---------------------------------------------------------------------------
# Standard logging -> Loguru
# ---------------------------------------------------------------------------

class InterceptHandler(logging.Handler):
    """Route records of the standard ``logging`` module (uvicorn, httpx…) to Loguru."""

    def emit(self, record: logging.LogRecord) -> None:
        try:
            level: str | int = logger.level(record.levelname).name
        except ValueError:
            level = record.levelno

        # Find the caller outside the logging module so Loguru reports the right frame.
        frame, depth = inspect.currentframe(), 0
        while frame and (depth == 0 or frame.f_code.co_filename == logging.__file__):
            frame = frame.f_back
            depth += 1

        logger.bind(_stdlib_logger=record.name).opt(depth=depth, exception=record.exc_info).log(
            level, record.getMessage()
        )


def _intercept_stdlib_logging(level: str) -> None:
    logging.basicConfig(handlers=[InterceptHandler()], level=level, force=True)
    # uvicorn installs its own handlers; drop them so records reach the root handler once.
    for name in ("uvicorn", "uvicorn.error", "uvicorn.access", "uvicorn.asgi", "httpx", "patchright"):
        std_logger = logging.getLogger(name)
        std_logger.handlers = []
        std_logger.propagate = True
    # The request middleware logs every request with actor and request_id;
    # uvicorn's access line would only duplicate it.
    logging.getLogger("uvicorn.access").setLevel(logging.WARNING)
    for name in _NOISY_STDLIB_LOGGERS:
        logging.getLogger(name).setLevel(logging.WARNING)


# ---------------------------------------------------------------------------
# Setup
# ---------------------------------------------------------------------------

def log_dir() -> str:
    return os.environ.get("GREX_LOG_DIR") or DEFAULT_LOG_DIR


def setup_logger(*, child: bool = False) -> None:
    """Configure Loguru for this process. Call once per process.

    ``child=True`` is for a browser subprocess started with ``spawn``
    (ADR 0009): no SSE sink (its queue lives in the parent), no file rotation
    or retention (the parent owns them), and no ``enqueue`` (a subprocess
    exits with ``os._exit`` and would drop queued lines).
    """
    global _known_secrets
    _known_secrets = tuple(sorted(_collect_secret_values(settings), key=len, reverse=True))

    logger.remove()
    logger.configure(patcher=_patch_record)

    log_level = "DEBUG" if settings.get("debug", {}).get("active", False) else "INFO"
    enqueue = not child

    logger.add(sys.stderr, format=_text_format, enqueue=enqueue, level=log_level)

    if not child:
        # SSE sink — raw text (no ANSI colours) for the admin logs page.
        logger.add(sse_sink, format=_plain_format, level=log_level, colorize=False)

    if settings.get("debug", {}).get("logging_in_file"):
        directory = log_dir()
        os.makedirs(directory, exist_ok=True)
        file_options: dict[str, Any] = (
            {"buffering": 1} if child else {"rotation": "00:00", "retention": "7 days", "enqueue": True}
        )
        logger.add(
            os.path.join(directory, "{time:YYYY-MM-DD}.log"),
            level=log_level,
            format=_text_format,
            colorize=False,
            **file_options,
        )
        json_options: dict[str, Any] = (
            {"buffering": 1} if child else {"rotation": "00:00", "retention": "14 days", "enqueue": True}
        )
        logger.add(
            os.path.join(directory, "app.jsonl"),
            level=log_level,
            format=_json_format,
            colorize=False,
            **json_options,
        )

    _intercept_stdlib_logging(log_level)
