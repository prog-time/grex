from __future__ import annotations

import asyncio
import uvicorn
import traceback
from contextlib import asynccontextmanager
from loguru import logger
from pydantic import HttpUrl
from datetime import datetime
from typing import List, Dict
from fastapi.security.api_key import APIKeyHeader
from fastapi import FastAPI, Depends, HTTPException, status
from fastapi.responses import JSONResponse
from fastapi.staticfiles import StaticFiles

from config import settings
from services.license import load_config_from_response, load_config_from_cache, get_allowed_sources
from services.license_client import activate_device, fetch_license_config
from models import (
    ParseRequest,
    ParseResponse,
    HealthResponse,
    BatchParseRequest,
    BatchParseResponse,
)
from crawlers.registry import CrawlerRegistry
from services import ParsingService
from utils.logger import setup_logger
from licensing import check_license


setup_logger()
check_license()


# ---------------------------------------------------------------------------
# Application lifespan — Redis init/teardown
# ---------------------------------------------------------------------------

@asynccontextmanager
async def lifespan(app: FastAPI):  # type: ignore[type-arg]
    """Initialise shared resources on startup and clean up on shutdown."""
    _image_worker_task = None

    try:
        from services.redis_client import init_redis  # noqa: PLC0415
        await init_redis()
        from services.stats import StatsCollector  # noqa: PLC0415
        await StatsCollector.set_start_time()
        from services.image_queue import image_worker  # noqa: PLC0415
        _image_worker_task = asyncio.create_task(image_worker(), name="image_worker")
    except Exception as exc:
        logger.warning(f"Redis init failed (admin panel will be unavailable): {exc}")

    licensing_cfg = settings.get("licensing", {})
    if licensing_cfg.get("enabled", False):
        try:
            import uuid as _uuid  # noqa: PLC0415
            _mac_int = _uuid.getnode()
            _mac_address = ":".join(f"{(_mac_int >> i) & 0xff:02x}" for i in range(40, -1, -8)).upper()
            await activate_device(
                service_url=licensing_cfg.get("service_url", ""),
                api_key=licensing_cfg.get("api_key", ""),
                mac_address=_mac_address,
                timeout=licensing_cfg.get("timeout_seconds", 5),
            )
            response = await fetch_license_config(
                service_url=licensing_cfg.get("service_url", ""),
                api_key=licensing_cfg.get("api_key", ""),
                mac_address=_mac_address,
                timeout=licensing_cfg.get("timeout_seconds", 5),
            )
            if response:
                await load_config_from_response(response)
                logger.info(f"License config loaded: {get_allowed_sources()}")
            else:
                loaded = await load_config_from_cache()
                if loaded:
                    logger.warning("License service unavailable, using cached config")
                else:
                    logger.error("No license config available, all sources disabled")
        except Exception as exc:
            logger.warning(f"License init failed: {exc}")

    _UNSAFE_DEFAULTS = {
        "security.api_key": ("4353456dfvbdfg34", settings.get("security", {}).get("api_key", "")),
        "admin.password": ("admin", settings.get("admin", {}).get("password", "")),
        "admin.secret_key": ("change-me-in-production-secret-key", settings.get("admin", {}).get("secret_key", "")),
    }
    for key, (bad, current) in _UNSAFE_DEFAULTS.items():
        if current == bad:
            logger.warning(
                f"SECURITY: '{key}' is set to an insecure default value"
                " — change it in data/settings.yml before going to production!"
            )

    try:
        from admin import setup_admin  # noqa: PLC0415
        await setup_admin(app)
    except Exception as exc:
        logger.warning(f"Admin setup failed: {exc}")

    try:
        from models.job import async_session  # noqa: PLC0415
        from models.parsed_product import ParseSession  # noqa: PLC0415
        from sqlalchemy import update  # noqa: PLC0415
        from datetime import datetime, timezone  # noqa: PLC0415
        if async_session is not None:
            async with async_session() as s:
                await s.execute(
                    update(ParseSession)
                    .where(ParseSession.status.in_(["pending", "running"]))
                    .values(status="failed", finished_at=datetime.now(timezone.utc))
                )
                await s.commit()
            logger.info("Stale parse sessions reset to 'failed' on startup")
    except Exception as exc:
        logger.warning(f"Failed to reset stale parse sessions: {exc}")

    yield

    if _image_worker_task is not None and not _image_worker_task.done():
        _image_worker_task.cancel()
        try:
            await _image_worker_task
        except asyncio.CancelledError:
            pass

    try:
        from admin import teardown_admin  # noqa: PLC0415
        await teardown_admin()
    except Exception:
        pass

    try:
        from services.redis_client import close_redis  # noqa: PLC0415
        await close_redis()
    except Exception:
        pass


app = FastAPI(
    title="Marketplace Parsing API",
    description="Universal API for parsing product cards from RU marketplaces",
    version=settings.get("soft_version", "1.0.0"),
    docs_url="/docs" if settings.get("debug", {}).get("active", False) else None,
    redoc_url="/redoc" if settings.get("debug", {}).get("active", False) else None,
    lifespan=lifespan,
)

from starlette.middleware.sessions import SessionMiddleware as _SessionMiddleware  # noqa: E402
app.add_middleware(_SessionMiddleware, secret_key=settings.get("admin", {}).get("secret_key", "change-me"))

import os as _os  # noqa: E402
_static_dir = _os.path.join(_os.path.dirname(__file__), "static")
app.mount("/static", StaticFiles(directory=_static_dir), name="static")

X_API_KEY = APIKeyHeader(name="X-API-Key", auto_error=False)

parsing_service = ParsingService()


# ---------------------------------------------------------------------------
# Routes
# ---------------------------------------------------------------------------

@app.get("/favicon.ico", include_in_schema=False)
async def favicon():
    from fastapi.responses import FileResponse  # noqa: PLC0415
    path = _os.path.join(_os.path.dirname(__file__), "static", "favicon.svg")
    return FileResponse(path, media_type="image/svg+xml")


@app.get("/", response_model=Dict[str, str])
async def root() -> Dict[str, str]:
    return {
        "message": "Marketplace Parsing API",
        "documentation": "/docs",
        "health": "/health",
    }


@app.get("/health", response_model=HealthResponse)
async def health_check() -> HealthResponse:
    return HealthResponse(
        status="healthy",
        uptime=parsing_service.get_uptime(),
        supported_marketplaces=list(CrawlerRegistry.all().keys()),
    )


async def verify_api_key(api_key: str = Depends(X_API_KEY)) -> str:
    if not api_key:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="API key is required. Add 'X-API-Key' header",
        )

    import hmac  # noqa: PLC0415
    expected = settings.get("security", {}).get("api_key") or ""
    if not hmac.compare_digest(str(api_key), str(expected)):
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Invalid API key",
        )

    return api_key


@app.post("/parse", response_model=ParseResponse, dependencies=[Depends(verify_api_key)])
async def parse_product(request: ParseRequest) -> ParseResponse:
    start_time = datetime.now()

    try:
        marketplace = CrawlerRegistry.detect_marketplace(str(request.url))

        logger.info(f"Starting parse for {marketplace}: {request.url}")

        product_card = await parsing_service.parse_product(url=str(request.url))

        processing_time = (datetime.now() - start_time).total_seconds()

        logger.success(f"Successfully parsed {request.url} in {processing_time:.2f}s")

        return ParseResponse(
            success=True,
            data=product_card.to_dict(),
            marketplace=marketplace,
            processing_time=processing_time,
        )

    except HTTPException:
        raise

    except Exception as e:
        processing_time = (datetime.now() - start_time).total_seconds()

        logger.error(f"Unexpected error parsing {request.url}: {e}")
        traceback.print_exc()

        return ParseResponse(
            success=False,
            error=str(e),
            marketplace=None,
            processing_time=processing_time,
        )


@app.post(
    "/parse/batch",
    response_model=BatchParseResponse,
    dependencies=[Depends(verify_api_key)],
)
async def parse_products_batch(request: BatchParseRequest) -> BatchParseResponse:
    start_time = datetime.now()

    results = await parsing_service.parse_products_batch(
        urls=[str(url) for url in request.urls],
        concurrency=request.concurrency,
    )

    processing_time = (datetime.now() - start_time).total_seconds()
    succeeded = sum(1 for r in results if r.success)
    failed = sum(1 for r in results if not r.success)

    return BatchParseResponse(
        total=len(results),
        succeeded=succeeded,
        failed=failed,
        processing_time=processing_time,
        results=results,
    )


@app.get("/marketplaces", response_model=Dict[str, List[str]])
async def get_supported_marketplaces() -> Dict[str, List[str]]:
    return {
        "supported_marketplaces": list(CrawlerRegistry.all().keys()),
        "example_urls": [
            "https://www.wildberries.ru/catalog/17386348/detail.aspx",
            "https://ozon.ru/product/shumin-maska-dlya-sna-chernaya-1563221610",
            "https://market.yandex.ru/card/espander-dlya-paltsev-ruk-kistevoy-95-kg-s-krepleniyem-na-zapyastye-tsvet---chernyy/103757317666",  # noqa: E501
            "https://megamarket.ru/catalog/details/puhovik-detskiy-poivre-blanc-w21-1201-jrgl-2122-mega-pink-128-600011555038",  # noqa: E501
        ],
    }


@app.get("/parse/detect", response_model=Dict[str, str])
async def detect_marketplace(url: HttpUrl) -> Dict[str, str]:
    try:
        marketplace = CrawlerRegistry.detect_marketplace(str(url))
        return {
            "url": str(url),
            "marketplace": marketplace,
            "status": "supported",
        }
    except ValueError as e:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=str(e),
        )


# ---------------------------------------------------------------------------
# Middleware & error handlers
# ---------------------------------------------------------------------------

@app.middleware("http")
async def log_requests(request, call_next):  # type: ignore[no-untyped-def]
    start_time = datetime.now()

    logger.debug(f"Incoming request: {request.method} {request.url}")

    response = await call_next(request)

    process_time = (datetime.now() - start_time).total_seconds()
    logger.debug(
        f"Request completed: {request.method} {request.url} "
        f"- {response.status_code} - {process_time:.3f}s"
    )

    return response


@app.exception_handler(Exception)
async def general_exception_handler(request, exc):  # type: ignore[no-untyped-def]
    logger.error(f"Unhandled exception for {request.url}: {exc}")

    return JSONResponse(
        status_code=500,
        content=ParseResponse(
            success=False,
            error="Internal server error occurred",
            marketplace=None,
            processing_time=0.0,
        ).model_dump(),
    )


# ---------------------------------------------------------------------------
# Entry point
# ---------------------------------------------------------------------------

if __name__ == "__main__":
    config = {
        "host": settings.get("app", {}).get("host", "localhost"),
        "port": settings.get("app", {}).get("port", 8000),
        "reload": False,
        "log_level": "debug" if settings.get("debug", {}).get("active", False) else "info",
        "loop": "asyncio",
    }

    logger.info("Starting Marketplace Parsing API")
    logger.info(f"Supported marketplaces: {list(CrawlerRegistry.all().keys())}")

    if settings.get("debug", {}).get("active", False):
        logger.info(f"Documentation: http://{config['host']}:{config['port']}/docs")

    uvicorn.run("__main__:app", **config)
