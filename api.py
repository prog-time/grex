from __future__ import annotations

import asyncio
import traceback
from contextlib import asynccontextmanager
from datetime import datetime, timezone
from typing import Dict, List

import uvicorn
from fastapi import APIRouter, Depends, FastAPI, HTTPException, Query, status
from fastapi.responses import JSONResponse
from fastapi.security.api_key import APIKeyHeader
from fastapi.staticfiles import StaticFiles
from loguru import logger
from pydantic import HttpUrl

from config import settings
from core.exceptions import UnsupportedMarketplaceError
from crawlers.registry import CrawlerRegistry
from models import (
    BatchParseRequest,
    BatchParseResponse,
    HealthResponse,
    JobCreateRequest,
    JobListResponse,
    JobResponse,
    JobSummary,
    ParseRequest,
    ParseResponse,
)
from models.monitor import (
    MonitorCreateRequest,
    MonitorListResponse,
    MonitorResponse,
    MonitorUpdateRequest,
    PriceAlertListResponse,
    PriceHistoryResponse,
)
from services import ParsingService
from utils.logger import setup_logger

setup_logger()


# ---------------------------------------------------------------------------
# Application lifespan — Redis init/teardown
# ---------------------------------------------------------------------------

@asynccontextmanager
async def lifespan(app: FastAPI):  # type: ignore[type-arg]
    """Initialise shared resources on startup and clean up on shutdown."""
    _image_worker_task = None
    _monitor_task = None

    try:
        from services.redis_client import init_redis  # noqa: PLC0415
        await init_redis()
        from services.stats import StatsCollector  # noqa: PLC0415
        await StatsCollector.set_start_time()
        from services.image_queue import image_worker  # noqa: PLC0415
        _image_worker_task = asyncio.create_task(image_worker(), name="image_worker")
    except Exception as exc:
        logger.warning(f"Redis init failed (admin panel will be unavailable): {exc}")

    _UNSAFE_DEFAULTS = {
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

    # Start the monitoring scheduler after the DB is initialised (setup_admin
    # calls init_db). It no-ops until there are due monitored targets.
    try:
        from services.monitor_scheduler import monitor_scheduler  # noqa: PLC0415
        _monitor_task = asyncio.create_task(monitor_scheduler(), name="monitor_scheduler")
    except Exception as exc:
        logger.warning(f"Monitor scheduler start failed: {exc}")

    try:
        from datetime import datetime, timezone  # noqa: PLC0415

        from sqlalchemy import update  # noqa: PLC0415

        from models.job import async_session  # noqa: PLC0415
        from models.parsed_product import ParseSession  # noqa: PLC0415
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

    try:
        from services.api_jobs import reset_stale_jobs  # noqa: PLC0415
        await reset_stale_jobs()
    except Exception as exc:
        logger.warning(f"Failed to reset stale API jobs: {exc}")

    yield

    for _task in (_image_worker_task, _monitor_task):
        if _task is not None and not _task.done():
            _task.cancel()
            try:
                await _task
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
    version=settings["soft_version"],
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

# Public API. Endpoints on `api_router` are served under /v1 and, for clients
# written before versioning, at their original unversioned paths (marked
# deprecated). New endpoints go on `v1_router` only.
api_router = APIRouter()
v1_router = APIRouter()


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
        "health": "/v1/health",
    }


@api_router.get("/health", response_model=HealthResponse)
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

    from services.api_keys import KeyStorageUnavailable, verify_key  # noqa: PLC0415

    try:
        api_key_id = await verify_key(api_key)
    except KeyStorageUnavailable:
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail="API key storage is unavailable",
        )
    if api_key_id is None:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Invalid API key",
        )

    return api_key_id


@api_router.post("/parse", response_model=ParseResponse, dependencies=[Depends(verify_api_key)])
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


@api_router.post(
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


@api_router.get("/marketplaces", response_model=Dict[str, List[str]])
async def get_supported_marketplaces() -> Dict[str, List[str]]:
    return {
        "supported_marketplaces": list(CrawlerRegistry.all().keys()),
    }


@api_router.get("/parse/detect", response_model=Dict[str, str])
async def detect_marketplace(url: HttpUrl) -> Dict[str, str]:
    try:
        marketplace = CrawlerRegistry.detect_marketplace(str(url))
        return {
            "url": str(url),
            "marketplace": marketplace,
            "status": "supported",
        }
    except UnsupportedMarketplaceError as e:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=str(e),
        )


@v1_router.post("/jobs", response_model=JobResponse, status_code=status.HTTP_202_ACCEPTED)
async def create_job(request: JobCreateRequest, api_key_id: str = Depends(verify_api_key)) -> JobResponse:
    """Queue URLs for background parsing; poll GET /v1/jobs/{id} for results."""
    from services.api_jobs import TooManyActiveJobs  # noqa: PLC0415
    from services.api_jobs import create_job as create_api_job  # noqa: PLC0415

    try:
        job = await create_api_job(
            api_key_id, [str(url) for url in request.urls], request.concurrency, parsing_service
        )
    except TooManyActiveJobs as e:
        raise HTTPException(status_code=status.HTTP_429_TOO_MANY_REQUESTS, detail=str(e))
    return JobResponse(**job, results=[])


@v1_router.get("/jobs", response_model=JobListResponse)
async def list_jobs(
    limit: int = Query(default=20, ge=1, le=100),
    api_key_id: str = Depends(verify_api_key),
) -> JobListResponse:
    """Most recent jobs created with this API key, without results."""
    from services.api_jobs import list_jobs as list_api_jobs  # noqa: PLC0415

    return JobListResponse(jobs=[JobSummary(**j) for j in await list_api_jobs(api_key_id, limit)])


@v1_router.get("/jobs/{job_id}", response_model=JobResponse)
async def get_job(job_id: str, api_key_id: str = Depends(verify_api_key)) -> JobResponse:
    """Job status, progress and the results of the URLs finished so far."""
    from services.api_jobs import get_job as get_api_job  # noqa: PLC0415

    job = await get_api_job(api_key_id, job_id)
    if job is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Job not found")
    return JobResponse(**job)


def _monitor_error(exc) -> HTTPException:  # type: ignore[no-untyped-def]
    return HTTPException(status_code=exc.status, detail=str(exc))


@v1_router.post("/monitors", response_model=MonitorResponse, status_code=status.HTTP_201_CREATED)
async def create_monitor(request: MonitorCreateRequest, api_key_id: str = Depends(verify_api_key)) -> MonitorResponse:
    """Start tracking a product's price. The first check runs right away."""
    from services.api_monitors import MonitorError  # noqa: PLC0415
    from services.api_monitors import create_monitor as create_api_monitor  # noqa: PLC0415

    try:
        monitor = await create_api_monitor(api_key_id, str(request.url), request.interval_seconds, request.name)
    except MonitorError as e:
        raise _monitor_error(e)
    return MonitorResponse(**monitor)


@v1_router.get("/monitors", response_model=MonitorListResponse)
async def list_monitors(api_key_id: str = Depends(verify_api_key)) -> MonitorListResponse:
    """All monitors of this API key with their latest known price."""
    from services.api_monitors import list_monitors as list_api_monitors  # noqa: PLC0415

    return MonitorListResponse(monitors=[MonitorResponse(**m) for m in await list_api_monitors(api_key_id)])


@v1_router.get("/monitors/{monitor_id}", response_model=MonitorResponse)
async def get_monitor(monitor_id: str, api_key_id: str = Depends(verify_api_key)) -> MonitorResponse:
    from services.api_monitors import MonitorError  # noqa: PLC0415
    from services.api_monitors import get_monitor as get_api_monitor  # noqa: PLC0415

    try:
        return MonitorResponse(**await get_api_monitor(api_key_id, monitor_id))
    except MonitorError as e:
        raise _monitor_error(e)


@v1_router.patch("/monitors/{monitor_id}", response_model=MonitorResponse)
async def update_monitor(
    monitor_id: str, request: MonitorUpdateRequest, api_key_id: str = Depends(verify_api_key),
) -> MonitorResponse:
    """Pause/resume (`active`), change `interval_seconds` (null = default) or `name`."""
    from services.api_monitors import MonitorError  # noqa: PLC0415
    from services.api_monitors import update_monitor as update_api_monitor  # noqa: PLC0415

    try:
        monitor = await update_api_monitor(api_key_id, monitor_id, request.model_dump(exclude_unset=True))
    except MonitorError as e:
        raise _monitor_error(e)
    return MonitorResponse(**monitor)


@v1_router.delete("/monitors/{monitor_id}", status_code=status.HTTP_204_NO_CONTENT, response_model=None)
async def delete_monitor(monitor_id: str, api_key_id: str = Depends(verify_api_key)) -> None:
    """Stop tracking and delete the monitor's price history and alerts."""
    from services.api_monitors import MonitorError  # noqa: PLC0415
    from services.api_monitors import delete_monitor as delete_api_monitor  # noqa: PLC0415

    try:
        await delete_api_monitor(api_key_id, monitor_id)
    except MonitorError as e:
        raise _monitor_error(e)


@v1_router.get("/monitors/{monitor_id}/history", response_model=PriceHistoryResponse)
async def monitor_history(
    monitor_id: str,
    limit: int = Query(default=500, ge=1, le=2000),
    api_key_id: str = Depends(verify_api_key),
) -> PriceHistoryResponse:
    """Recorded prices, oldest → newest (the latest `limit` points)."""
    from services.api_monitors import MonitorError, price_history  # noqa: PLC0415

    try:
        points = await price_history(api_key_id, monitor_id, limit)
    except MonitorError as e:
        raise _monitor_error(e)
    return PriceHistoryResponse(monitor_id=monitor_id, points=points)


@v1_router.get("/alerts", response_model=PriceAlertListResponse)
async def list_alerts(
    since: datetime | None = Query(default=None, description="Only alerts after this moment (ISO 8601)"),
    limit: int = Query(default=100, ge=1, le=500),
    api_key_id: str = Depends(verify_api_key),
) -> PriceAlertListResponse:
    """Price changes beyond the server's thresholds on this key's monitors, newest first."""
    from services.api_monitors import list_alerts as list_api_alerts  # noqa: PLC0415

    # Stored timestamps are naive UTC.
    if since is not None and since.tzinfo is not None:
        since = since.astimezone(timezone.utc).replace(tzinfo=None)
    return PriceAlertListResponse(alerts=await list_api_alerts(api_key_id, since, limit))


app.include_router(api_router, prefix="/v1", tags=["v1"])
app.include_router(v1_router, prefix="/v1", tags=["v1"])
app.include_router(api_router, tags=["deprecated"], deprecated=True)


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
