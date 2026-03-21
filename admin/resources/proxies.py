"""Admin resource — Proxies (TASK-11).

Provides read/write access to the proxy pool stored in Redis via ProxyManager.
Falls back to data/proxies.json when Redis is unavailable.
Passwords are masked in all responses.
"""
from __future__ import annotations

import json
import os
import re
from fastapi import Request
from fastapi.responses import JSONResponse
from loguru import logger

import yaml
from config import data_path, settings

_SETTINGS_FILE = os.path.join(data_path, "settings.yml")

_PROXY_REDIS_KEY = "proxy:pool"
_PROXY_FILE = os.path.join(data_path, "proxies.json")


def _read_proxy_file() -> list[str]:
    try:
        with open(_PROXY_FILE, encoding="utf-8") as f:
            return json.load(f)
    except Exception:
        return []


def _write_proxy_file(proxies: list[str]) -> None:
    try:
        os.makedirs(os.path.dirname(_PROXY_FILE), exist_ok=True)
        with open(_PROXY_FILE, "w", encoding="utf-8") as f:
            json.dump(proxies, f, indent=2, ensure_ascii=False)
    except Exception as exc:  # noqa: BLE001
        logger.warning(f"Failed to write proxies file: {exc}")


async def _get_proxies() -> list[str]:
    try:
        from services.redis_client import get_json  # noqa: PLC0415
        result = await get_json(_PROXY_REDIS_KEY)
        if result is not None:
            return result
    except Exception:
        pass
    return _read_proxy_file()


async def _set_proxies(proxies: list[str]) -> None:
    saved_to_redis = False
    try:
        from services.redis_client import set_json  # noqa: PLC0415
        await set_json(_PROXY_REDIS_KEY, proxies)
        saved_to_redis = True
    except Exception:
        pass
    # Always write to file as persistent backup
    _write_proxy_file(proxies)
    if not saved_to_redis:
        logger.warning("Redis unavailable — proxies saved to file only")


def _mask_proxy(proxy: str) -> str:
    """Replace the password portion with '***' for safe display."""
    # Pattern: [protocol://]login:password@host:port
    masked = re.sub(
        r"(://[^:]+):([^@]+)@",
        r"\1:***@",
        proxy,
    )
    # Also handle bare login:password@host:port (no protocol)
    if "://" not in proxy:
        masked = re.sub(r"^([^:]+):([^@]+)@", r"\1:***@", proxy)
    return masked


class ProxiesRouter:
    """FastAPI router for /admin/proxies endpoints."""

    @staticmethod
    async def list_proxies(request: Request) -> JSONResponse:
        try:
            proxies = await _get_proxies()
            logger.debug(f"Proxy list API: {len(proxies)} proxies")
        except Exception as exc:  # noqa: BLE001
            logger.warning(f"Failed to list proxies: {exc}")
            proxies = []

        masked = [{"index": i, "proxy": _mask_proxy(p)} for i, p in enumerate(proxies)]
        return JSONResponse({"total": len(masked), "proxies": masked})

    @staticmethod
    async def add_proxy(request: Request) -> JSONResponse:
        from core.managers import ProxyManager  # noqa: PLC0415

        body = await request.json()
        proxy_str: str = body.get("proxy", "").strip()
        if not proxy_str:
            return JSONResponse({"error": "proxy is required"}, status_code=422)

        if not ProxyManager.is_valid_proxy(proxy_str):
            return JSONResponse(
                {"error": "Invalid proxy format. Expected: host:port or login:password@host:port"},
                status_code=422,
            )

        try:
            proxies = await _get_proxies()
            proxies.append(proxy_str)
            await _set_proxies(proxies)
            logger.info(f"Proxy added: {_mask_proxy(proxy_str)}, total={len(proxies)}")
            return JSONResponse({"added": _mask_proxy(proxy_str), "total": len(proxies)})
        except Exception as exc:  # noqa: BLE001
            logger.error(f"Failed to add proxy: {exc}")
            return JSONResponse({"error": str(exc)}, status_code=500)

    @staticmethod
    async def remove_proxy(request: Request) -> JSONResponse:
        body = await request.json()
        index = body.get("index")
        if index is None:
            return JSONResponse({"error": "index is required"}, status_code=422)

        try:
            proxies = await _get_proxies()
            try:
                removed = proxies.pop(int(index))
            except (IndexError, ValueError):
                return JSONResponse({"error": f"No proxy at index {index}"}, status_code=422)

            await _set_proxies(proxies)
            logger.info(f"Proxy removed at index {index}: {_mask_proxy(removed)}, total={len(proxies)}")
            return JSONResponse({"removed": _mask_proxy(removed), "total": len(proxies)})
        except Exception as exc:  # noqa: BLE001
            logger.error(f"Failed to remove proxy at index {index}: {exc}")
            return JSONResponse({"error": str(exc)}, status_code=500)

    @staticmethod
    async def get_proxy_settings(request: Request) -> JSONResponse:
        proxy_cfg = settings.get("proxy", {})
        sources = settings.get("sources", {})
        source_proxy = {
            src: proxy_cfg.get("sources", {}).get(src, False)
            for src in sources
        }
        return JSONResponse({"sources": source_proxy})

    @staticmethod
    async def update_proxy_settings(request: Request) -> JSONResponse:
        body = await request.json()

        def _persist(updater):
            try:
                with open(_SETTINGS_FILE, encoding="utf-8") as f:
                    raw = yaml.safe_load(f) or {}
                updater(raw)
                with open(_SETTINGS_FILE, "w", encoding="utf-8") as f:
                    yaml.dump(raw, f, allow_unicode=True, default_flow_style=False, sort_keys=False)
            except Exception as exc:  # noqa: BLE001
                logger.warning(f"Failed to persist proxy settings: {exc}")

        # Protocol type
        if "type" in body:
            proxy_type = body["type"]
            if proxy_type not in ("http", "socks5"):
                return JSONResponse({"error": "type must be http or socks5"}, status_code=422)
            settings.setdefault("proxy", {})["type"] = proxy_type
            _persist(lambda raw: raw.setdefault("proxy", {}).__setitem__("type", proxy_type))
            logger.info(f"Proxy type set to {proxy_type}")

        # Per-source toggle
        if "source" in body and "source_active" in body:
            source = body["source"]
            source_active = body["source_active"]
            if not isinstance(source_active, bool):
                return JSONResponse({"error": "source_active must be boolean"}, status_code=422)
            settings.setdefault("proxy", {}).setdefault("sources", {})[source] = source_active

            def _update_source(raw):
                raw.setdefault("proxy", {}).setdefault("sources", {})[source] = source_active

            _persist(_update_source)
            logger.info(f"Proxy for {source} set to {source_active}")

        return JSONResponse({"ok": True})

    @staticmethod
    async def reload_from_file(request: Request) -> JSONResponse:
        from core.managers import ProxyManager  # noqa: PLC0415

        logger.info("Proxy reload from file requested")
        try:
            proxies = await ProxyManager.load_proxies()
            logger.info(f"Proxy reload complete: {len(proxies)} proxies loaded")
            return JSONResponse({"reloaded": len(proxies)})
        except Exception as exc:  # noqa: BLE001
            logger.error(f"Proxy reload failed: {exc}")
            return JSONResponse({"error": str(exc)}, status_code=500)


def register_proxies_routes(app) -> None:  # type: ignore[no-untyped-def]
    """Attach proxy management endpoints to *app*."""
    from fastapi import Depends  # noqa: PLC0415
    from admin.resources.auth import require_admin  # noqa: PLC0415
    _auth = [Depends(require_admin)]
    router = ProxiesRouter()
    app.add_api_route(
        "/admin/proxies/api",
        router.list_proxies,
        methods=["GET"],
        tags=["admin"],
        dependencies=_auth,
    )
    app.add_api_route(
        "/admin/proxies/add",
        router.add_proxy,
        methods=["POST"],
        tags=["admin"],
        dependencies=_auth,
    )
    app.add_api_route(
        "/admin/proxies/remove",
        router.remove_proxy,
        methods=["POST"],
        tags=["admin"],
        dependencies=_auth,
    )
    app.add_api_route(
        "/admin/proxies/reload",
        router.reload_from_file,
        methods=["POST"],
        tags=["admin"],
        dependencies=_auth,
    )
    app.add_api_route(
        "/admin/proxies/settings",
        router.get_proxy_settings,
        methods=["GET"],
        tags=["admin"],
        dependencies=_auth,
    )
    app.add_api_route(
        "/admin/proxies/settings",
        router.update_proxy_settings,
        methods=["POST"],
        tags=["admin"],
        dependencies=_auth,
    )
