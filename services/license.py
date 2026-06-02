"""Source licensing module.

Responsibilities:
- Hold in-memory LicenseConfig (sources list)
- load_config_from_response(response_data) -> bool
- load_config_from_cache() -> bool
- is_source_allowed(source) -> bool
- get_allowed_sources() -> list[str]
"""
from __future__ import annotations

import asyncio
import base64
import json
import os
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Optional

from loguru import logger

from config import settings, data_path


# ---------------------------------------------------------------------------
# Token helpers
# ---------------------------------------------------------------------------

def _extract_source(token: str) -> Optional[str]:
    """Decode a JWT payload (no signature verification) and return the 'source' claim."""
    try:
        payload_b64 = token.split(".")[1]
        payload_b64 += "=" * (-len(payload_b64) % 4)
        payload = json.loads(base64.urlsafe_b64decode(payload_b64))
        source: Optional[str] = payload.get("source")
        if not source:
            logger.warning("JWT token missing 'source' claim — skipped")
        return source
    except Exception as exc:
        logger.warning(f"Failed to decode JWT token: {exc}")
        return None


# ---------------------------------------------------------------------------
# Data structures
# ---------------------------------------------------------------------------

@dataclass
class LicenseConfig:
    sources: list[str] = field(default_factory=list)
    client_id: str = ""
    fetched_at: datetime = field(default_factory=lambda: datetime.now(timezone.utc))


_current_config: Optional[LicenseConfig] = None
_config_lock: asyncio.Lock = asyncio.Lock()


# ---------------------------------------------------------------------------
# Cache helpers
# ---------------------------------------------------------------------------

def _cache_file_path() -> str:
    raw: str = settings.get("licensing", {}).get(
        "cache_file", os.path.join(data_path, "license_cache.json")
    )
    if not os.path.isabs(raw):
        return os.path.join(data_path, raw)
    return raw


def _save_cache(tokens: list[str], client_id: str) -> None:
    path = _cache_file_path()
    payload = {
        "fetched_at": datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),
        "client_id": client_id,
        "tokens": tokens,
    }
    try:
        with open(path, "w", encoding="utf-8") as fh:
            json.dump(payload, fh, indent=2)
        logger.debug(f"License cache saved to '{path}'")
    except Exception as exc:
        logger.warning(f"Failed to save license cache: {exc}")


def _read_cache() -> Optional[dict]:
    path = _cache_file_path()
    if not os.path.exists(path):
        return None
    try:
        with open(path, encoding="utf-8") as fh:
            return json.load(fh)
    except Exception as exc:
        logger.warning(f"Failed to read license cache: {exc}")
        return None


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------

async def load_config_from_response(response_data: dict) -> bool:
    """Process a fresh API response, extract sources, update memory and save cache."""
    global _current_config

    tokens: list[str] = response_data.get("tokens", [])
    client_id: str = response_data.get("client_id", "")

    sources = [s for s in (_extract_source(t) for t in tokens) if s is not None]

    async with _config_lock:
        _current_config = LicenseConfig(
            sources=sources,
            client_id=client_id,
            fetched_at=datetime.now(timezone.utc),
        )

    _save_cache(tokens, client_id)

    logger.debug(f"License config updated: {len(sources)} source(s) — {sources}")
    return bool(sources)


async def load_config_from_cache() -> bool:
    """Load tokens from the on-disk cache and update memory."""
    global _current_config

    cache = _read_cache()
    if cache is None:
        logger.warning("No license cache found — starting with empty allowed sources")
        return False

    tokens: list[str] = cache.get("tokens", [])
    client_id: str = cache.get("client_id", "")

    sources = [s for s in (_extract_source(t) for t in tokens) if s is not None]

    async with _config_lock:
        _current_config = LicenseConfig(
            sources=sources,
            client_id=client_id,
            fetched_at=datetime.now(timezone.utc),
        )

    logger.debug(f"License config loaded from cache: {len(sources)} source(s) — {sources}")
    return bool(sources)


def is_source_allowed(source: str) -> bool:
    """Return True if the given source is permitted by the current license config."""
    if not settings.get("licensing", {}).get("enabled", False):
        return True

    if _current_config is not None:
        return source in _current_config.sources

    # In-memory config not loaded yet — fall back to cache file (same as dashboard)
    cache = _read_cache()
    if cache is None:
        return False
    tokens: list[str] = cache.get("tokens", [])
    sources = [s for s in (_extract_source(t) for t in tokens) if s is not None]
    return source in sources


def is_marketplace_allowed(marketplace: str) -> bool:
    """Return True if marketplace is permitted, resolving crawler's license_source_name."""
    try:
        from crawlers.registry import CrawlerRegistry  # noqa: PLC0415
        crawler_cls = CrawlerRegistry.get(marketplace)
        license_source = getattr(crawler_cls, "license_source_name", "") or marketplace
    except Exception:
        license_source = marketplace
    return is_source_allowed(license_source)


def get_allowed_sources() -> list[str]:
    """Return the list of currently allowed source codes."""
    if _current_config is None:
        return []
    return list(_current_config.sources)
