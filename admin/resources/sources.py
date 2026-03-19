"""Admin resource — Sources (TASK-9).

Displays all registered crawlers and allows enabling/disabling them,
adjusting rate limits, and running a test parse.
"""
from __future__ import annotations

from fastapi import Request
from loguru import logger


# Redis key templates
_KEY_ENABLED = "source:{name}:enabled"
_KEY_RATE_LIMIT = "source:{name}:rate_limit"
_KEY_FIELDS = "source:{name}:fields"


async def _redis():  # type: ignore[return]
    from services.redis_client import get_redis_client  # noqa: PLC0415
    return get_redis_client()


async def get_source_fields(name: str) -> list[str]:
    """Return selected fields for *name* from Redis; empty list = use all defaults."""
    import json  # noqa: PLC0415
    try:
        client = await _redis()
        raw = await client.get(_KEY_FIELDS.format(name=name))
        if raw:
            return json.loads(raw)
    except BaseException as exc:  # noqa: BLE001
        if isinstance(exc, (SystemExit, KeyboardInterrupt)):
            raise
        logger.warning(f"Redis unavailable reading fields for '{name}': {type(exc).__name__}: {exc}")
    return []


async def set_source_fields(name: str, fields: list[str]) -> None:
    """Persist field selection for *name* in Redis."""
    import json  # noqa: PLC0415
    try:
        client = await _redis()
        await client.set(_KEY_FIELDS.format(name=name), json.dumps(fields))
        logger.info(f"Source '{name}' fields set to {fields}")
    except Exception as exc:  # noqa: BLE001
        logger.warning(f"Redis unavailable, fields for '{name}' not persisted: {exc}")


async def get_source_state(name: str) -> dict:
    """Return the current Redis state for *name*.

    Fails open (returns defaults) when Redis is unavailable.
    """
    try:
        client = await _redis()
        enabled_raw = await client.get(_KEY_ENABLED.format(name=name))
        rate_limit_raw = await client.get(_KEY_RATE_LIMIT.format(name=name))
        enabled = True if enabled_raw is None else enabled_raw.lower() != "false"
        rate_limit = int(rate_limit_raw) if rate_limit_raw else 60
        logger.debug(f"Source '{name}': enabled={enabled}, rate_limit={rate_limit}")
    except BaseException as exc:  # noqa: BLE001
        if isinstance(exc, (SystemExit, KeyboardInterrupt)):
            raise
        logger.warning(f"Redis unavailable for source '{name}', using defaults: {type(exc).__name__}: {exc}")
        enabled = True
        rate_limit = 60

    fields = await get_source_fields(name)
    return {"name": name, "enabled": enabled, "rate_limit": rate_limit, "fields": fields}


async def toggle_source(name: str) -> bool:
    """Flip the enabled flag for *name*; return new state."""
    state = await get_source_state(name)
    new_enabled = not state["enabled"]
    try:
        client = await _redis()
        await client.set(_KEY_ENABLED.format(name=name), str(new_enabled).lower())
        logger.info(f"Source '{name}' toggled to enabled={new_enabled}")
    except Exception as exc:  # noqa: BLE001
        logger.warning(f"Redis unavailable, toggle for '{name}' not persisted: {exc}")
    return new_enabled


async def set_rate_limit(name: str, rate_limit: int) -> None:
    """Persist the rate limit for *name*."""
    try:
        client = await _redis()
        await client.set(_KEY_RATE_LIMIT.format(name=name), str(rate_limit))
        logger.info(f"Source '{name}' rate limit set to {rate_limit}")
    except Exception as exc:  # noqa: BLE001
        logger.warning(f"Redis unavailable, rate limit for '{name}' not persisted: {exc}")


class SourcesRouter:
    """FastAPI router providing /admin/sources endpoints.

    Registered in admin/__init__.py and mounted on the main app.
    """

    @staticmethod
    async def list_sources(request: Request):  # type: ignore[return]
        from fastapi.responses import JSONResponse  # noqa: PLC0415
        from crawlers.registry import CrawlerRegistry  # noqa: PLC0415

        try:
            crawlers = CrawlerRegistry.all()
            sources = []
            for name in crawlers:
                sources.append(await get_source_state(name))
            logger.debug(f"Listed {len(sources)} sources")
            return JSONResponse({"sources": sources})
        except Exception as exc:  # noqa: BLE001
            logger.error(f"Failed to list sources: {exc}")
            return JSONResponse({"sources": []})

    @staticmethod
    async def toggle(request: Request):  # type: ignore[return]
        from fastapi.responses import JSONResponse  # noqa: PLC0415

        body = await request.json()
        name: str = body.get("name", "")
        if not name:
            return JSONResponse({"error": "name is required"}, status_code=422)
        new_state = await toggle_source(name)
        return JSONResponse({"name": name, "enabled": new_state})

    @staticmethod
    async def update_rate_limit(request: Request):  # type: ignore[return]
        from fastapi.responses import JSONResponse  # noqa: PLC0415

        body = await request.json()
        name: str = body.get("name", "")
        rate_limit = body.get("rate_limit")
        if not name or rate_limit is None:
            return JSONResponse({"error": "name and rate_limit are required"}, status_code=422)
        try:
            rl = int(rate_limit)
            if rl < 1:
                raise ValueError
        except (ValueError, TypeError):
            return JSONResponse({"error": "rate_limit must be a positive integer"}, status_code=422)

        await set_rate_limit(name, rl)
        return JSONResponse({"name": name, "rate_limit": rl})

    @staticmethod
    async def update_fields(request: Request):  # type: ignore[return]
        from fastapi.responses import JSONResponse  # noqa: PLC0415

        body = await request.json()
        name: str = body.get("name", "")
        fields = body.get("fields")
        if not name:
            return JSONResponse({"error": "name is required"}, status_code=422)
        if not isinstance(fields, list):
            return JSONResponse({"error": "fields must be a list"}, status_code=422)
        await set_source_fields(name, fields)
        return JSONResponse({"name": name, "fields": fields})

    @staticmethod
    async def test_source(request: Request):  # type: ignore[return]
        from fastapi.responses import JSONResponse  # noqa: PLC0415
        from services.parsing import ParsingService  # noqa: PLC0415

        body = await request.json()
        url: str = body.get("url", "")
        if not url:
            return JSONResponse({"error": "url is required"}, status_code=422)

        logger.info(f"Test parse requested for URL: {url}")
        svc = ParsingService()
        try:
            product = await svc.parse_product(url)
            logger.info(f"Test parse succeeded for URL: {url}")
            return JSONResponse({"success": True, "data": product.to_dict()})
        except Exception as exc:  # noqa: BLE001
            logger.warning(f"Test parse failed for URL {url}: {exc}")
            return JSONResponse({"success": False, "error": str(exc)}, status_code=200)


def register_sources_routes(app) -> None:  # type: ignore[no-untyped-def]
    """Attach source management endpoints to *app*."""
    router = SourcesRouter()
    app.add_api_route(
        "/admin/sources/api",
        router.list_sources,
        methods=["GET"],
        tags=["admin"],
    )
    app.add_api_route(
        "/admin/sources/toggle",
        router.toggle,
        methods=["POST"],
        tags=["admin"],
    )
    app.add_api_route(
        "/admin/sources/rate-limit",
        router.update_rate_limit,
        methods=["POST"],
        tags=["admin"],
    )
    app.add_api_route(
        "/admin/sources/fields",
        router.update_fields,
        methods=["POST"],
        tags=["admin"],
    )
    app.add_api_route(
        "/admin/sources/test",
        router.test_source,
        methods=["POST"],
        tags=["admin"],
    )
