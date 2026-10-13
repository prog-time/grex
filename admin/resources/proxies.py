"""Admin resource — Proxies (TASK-11).

Provides read/write access to the proxy pool stored in Redis via ProxyManager.
Falls back to data/proxies.json when Redis is unavailable.
Passwords are masked in all responses.
"""
from __future__ import annotations

import json
import os
import re

import yaml
from fastapi import Request
from fastapi.responses import JSONResponse
from loguru import logger

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
    async def remove_many(request: Request) -> JSONResponse:
        """Bulk-delete proxies by their list indexes. Body: {"indexes": [int, ...]}."""
        body = await request.json()
        raw_indexes = body.get("indexes")
        if not isinstance(raw_indexes, list) or not raw_indexes:
            return JSONResponse({"error": "indexes (non-empty list) is required"}, status_code=422)

        try:
            indexes = sorted({int(i) for i in raw_indexes}, reverse=True)
        except (TypeError, ValueError):
            return JSONResponse({"error": "indexes must be integers"}, status_code=422)

        try:
            proxies = await _get_proxies()
            removed = 0
            for idx in indexes:  # descending — pop from the tail so positions stay valid
                if 0 <= idx < len(proxies):
                    proxies.pop(idx)
                    removed += 1

            await _set_proxies(proxies)
            logger.info(f"Bulk proxy removal: {removed} removed, total={len(proxies)}")
            return JSONResponse({"removed": removed, "total": len(proxies)})
        except Exception as exc:  # noqa: BLE001
            logger.error(f"Failed to bulk-remove proxies: {exc}")
            return JSONResponse({"error": str(exc)}, status_code=500)

    @staticmethod
    async def get_proxy_settings(request: Request) -> JSONResponse:
        proxy_cfg = settings.get("proxy", {})
        return JSONResponse({
            "active": bool(proxy_cfg.get("active", False)),
            "type": proxy_cfg.get("type", "http"),
        })

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

        # Global on/off toggle
        if "active" in body:
            active = body["active"]
            if not isinstance(active, bool):
                return JSONResponse({"error": "active must be boolean"}, status_code=422)
            settings.setdefault("proxy", {})["active"] = active
            _persist(lambda raw: raw.setdefault("proxy", {}).__setitem__("active", active))
            logger.info(f"Proxy usage {'enabled' if active else 'disabled'}")

        return JSONResponse({"ok": True})

    @staticmethod
    async def reload_from_file(request: Request) -> JSONResponse:
        """Replace the proxy pool with proxies from an uploaded .txt file.

        Expects JSON body ``{"proxies": "<raw file text>"}`` — the content of
        the .txt file the user picked in the browser, one proxy per line.
        """
        from core.managers import ProxyManager  # noqa: PLC0415

        logger.info("Proxy reload from uploaded file requested")
        try:
            body = await request.json()
        except Exception:  # noqa: BLE001
            return JSONResponse({"error": "Invalid request body"}, status_code=400)

        raw = body.get("proxies")
        if isinstance(raw, str):
            lines = [ln.strip() for ln in raw.splitlines() if ln.strip()]
        elif isinstance(raw, list):
            lines = [str(ln).strip() for ln in raw if str(ln).strip()]
        else:
            return JSONResponse(
                {"error": "No file content received"}, status_code=422
            )

        if not lines:
            return JSONResponse(
                {"error": "The selected file is empty — one proxy per line expected"},
                status_code=422,
            )

        invalid = [p for p in lines if not ProxyManager.is_valid_proxy(p)]
        if invalid:
            return JSONResponse(
                {"error": f"Invalid proxy in file: {_mask_proxy(invalid[0])}"},
                status_code=422,
            )

        # Persist through the panel's storage (Redis best-effort + proxies.json),
        # so the upload works even when Redis is down and stays consistent with
        # the add/remove/list endpoints.
        await _set_proxies(lines)
        logger.info(f"Proxy reload complete: {len(lines)} proxies loaded from file")
        return JSONResponse({"reloaded": len(lines)})


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
        "/admin/proxies/remove_many",
        router.remove_many,
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
