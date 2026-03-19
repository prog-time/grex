"""Admin resource — Proxies (TASK-11).

Provides read/write access to the proxy pool stored in Redis via ProxyManager.
Passwords are masked in all responses.
"""
from __future__ import annotations

import re
from fastapi import Request
from fastapi.responses import JSONResponse
from loguru import logger


_PROXY_REDIS_KEY = "proxy:pool"


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
        from core.managers import ProxyManager  # noqa: PLC0415

        try:
            proxies = await ProxyManager.get_all_proxies()
            logger.debug(f"Proxy list API: {len(proxies)} proxies")
        except Exception as exc:  # noqa: BLE001
            logger.warning(f"ProxyManager unavailable on list: {exc}")
            proxies = []

        masked = [{"index": i, "proxy": _mask_proxy(p)} for i, p in enumerate(proxies)]
        return JSONResponse({"total": len(masked), "proxies": masked})

    @staticmethod
    async def add_proxy(request: Request) -> JSONResponse:
        from services.redis_client import get_json, set_json  # noqa: PLC0415
        from core.managers import ProxyManager  # noqa: PLC0415

        body = await request.json()
        proxy_str: str = body.get("proxy", "").strip()
        if not proxy_str:
            return JSONResponse({"error": "proxy is required"}, status_code=422)

        if not ProxyManager.is_valid_proxy(proxy_str):
            return JSONResponse(
                {"error": "Invalid proxy format. Expected: login:password@host:port"},
                status_code=422,
            )

        try:
            proxies: list[str] = await get_json(_PROXY_REDIS_KEY) or []
            proxies.append(proxy_str)
            await set_json(_PROXY_REDIS_KEY, proxies)
            logger.info(f"Proxy added: {_mask_proxy(proxy_str)}, total={len(proxies)}")
            return JSONResponse({"added": _mask_proxy(proxy_str), "total": len(proxies)})
        except Exception as exc:  # noqa: BLE001
            logger.error(f"Failed to add proxy: {exc}")
            return JSONResponse({"error": f"Redis unavailable: {exc}"}, status_code=503)

    @staticmethod
    async def remove_proxy(request: Request) -> JSONResponse:
        from services.redis_client import get_json, set_json  # noqa: PLC0415

        body = await request.json()
        index = body.get("index")
        if index is None:
            return JSONResponse({"error": "index is required"}, status_code=422)

        try:
            proxies: list[str] = await get_json(_PROXY_REDIS_KEY) or []
            try:
                removed = proxies.pop(int(index))
            except (IndexError, ValueError):
                return JSONResponse({"error": f"No proxy at index {index}"}, status_code=422)

            await set_json(_PROXY_REDIS_KEY, proxies)
            logger.info(f"Proxy removed at index {index}: {_mask_proxy(removed)}, total={len(proxies)}")
            return JSONResponse({"removed": _mask_proxy(removed), "total": len(proxies)})
        except Exception as exc:  # noqa: BLE001
            logger.error(f"Failed to remove proxy at index {index}: {exc}")
            return JSONResponse({"error": f"Redis unavailable: {exc}"}, status_code=503)

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
    router = ProxiesRouter()
    app.add_api_route(
        "/admin/proxies/api",
        router.list_proxies,
        methods=["GET"],
        tags=["admin"],
    )
    app.add_api_route(
        "/admin/proxies/add",
        router.add_proxy,
        methods=["POST"],
        tags=["admin"],
    )
    app.add_api_route(
        "/admin/proxies/remove",
        router.remove_proxy,
        methods=["POST"],
        tags=["admin"],
    )
    app.add_api_route(
        "/admin/proxies/reload",
        router.reload_from_file,
        methods=["POST"],
        tags=["admin"],
    )
