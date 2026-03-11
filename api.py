import uvicorn
import traceback
from loguru import logger
from pydantic import HttpUrl
from datetime import datetime
from typing import List, Dict
from fastapi.security.api_key import APIKeyHeader
from fastapi import FastAPI, Depends, HTTPException, status

from config import settings
from models import (
    ParseRequest,
    ParseResponse,
    HealthResponse
)
from services import ParsingService
from utils.logger import setup_logger


setup_logger()

app = FastAPI(
    title="Marketplace Parsing API",
    description="Universal API for parsing product cards from RU marketplaces",
    version=settings.get("soft_version", "1.0.0"),
    docs_url="/docs" if settings.get("debug", {}).get("active", False) else None,
    redoc_url="/redoc" if settings.get("debug", {}).get("active", False) else None,
)
X_API_KEY = APIKeyHeader(name="X-API-Key", auto_error=False)

parsing_service = ParsingService()


@app.get("/", response_model=Dict[str, str])
async def root():
    return {
        "message": "Marketplace Parsing API",
        "documentation": "/docs",
        "health": "/health"
    }


@app.get("/health", response_model=HealthResponse)
async def health_check():
    return HealthResponse(
        status="healthy",
        uptime=parsing_service.get_uptime(),
        supported_marketplaces=parsing_service.factory.get_supported_marketplaces()
    )


async def verify_api_key(api_key: str = Depends(X_API_KEY)) -> str:
    if not api_key:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="API key is required. Add 'X-API-Key' header"
        )

    if api_key != settings.get("security", {}).get("api_key"):
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Invalid API key"
        )

    return api_key


@app.post("/parse", response_model=ParseResponse, dependencies=[Depends(verify_api_key)])
async def parse_product(
        request: ParseRequest
):
    start_time = datetime.now()

    try:
        marketplace = parsing_service.factory.detect_marketplace(str(request.url))

        logger.info(f"Starting parse for {marketplace}: {request.url}")

        product_card = await parsing_service.parse_product(
            url=str(request.url),
        )

        processing_time = (datetime.now() - start_time).total_seconds()

        logger.success(f"Successfully parsed {request.url} in {processing_time:.2f}s")

        return ParseResponse(
            success=True,
            data=product_card.to_dict(),
            marketplace=marketplace,
            processing_time=processing_time
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
            processing_time=processing_time
        )


@app.get("/marketplaces", response_model=Dict[str, List[str]])
async def get_supported_marketplaces():
    return {
        "supported_marketplaces": parsing_service.factory.get_supported_marketplaces(),
        "example_urls": [
            "https://www.wildberries.ru/catalog/17386348/detail.aspx",
            "https://ozon.ru/product/shumin-maska-dlya-sna-chernaya-1563221610",
            "https://market.yandex.ru/card/espander-dlya-paltsev-ruk-kistevoy-95-kg-s-krepleniyem-na-zapyastye-tsvet---chernyy/103757317666",
            "https://megamarket.ru/catalog/details/puhovik-detskiy-poivre-blanc-w21-1201-jrgl-2122-mega-pink-128-600011555038"
        ]
    }


@app.get("/parse/detect", response_model=Dict[str, str])
async def detect_marketplace(url: HttpUrl):
    try:
        marketplace = parsing_service.factory.detect_marketplace(str(url))
        return {
            "url": str(url),
            "marketplace": marketplace,
            "status": "supported"
        }
    except ValueError as e:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=str(e)
        )


@app.middleware("http")
async def log_requests(request, call_next):
    start_time = datetime.now()

    logger.debug(f"Incoming request: {request.method} {request.url}")

    response = await call_next(request)

    process_time = (datetime.now() - start_time).total_seconds()
    logger.debug(f"Request completed: {request.method} {request.url} - {response.status_code} - {process_time:.3f}s")

    return response


@app.exception_handler(Exception)
async def general_exception_handler(request, exc):
    logger.error(f"Unhandled exception for {request.url}: {exc}")

    return ParseResponse(
        success=False,
        error="Internal server error occurred",
        marketplace=None,
        processing_time=0.0
    )


if __name__ == "__main__":
    config = {
        "host": settings.get("app", {}).get("host", "localhost"),
        "port": settings.get("app", {}).get("port", 8000),
        "reload": False,
        "log_level": "debug" if settings.get("debug", {}).get("active", False) else "info",
        "loop": "asyncio"
    }

    logger.info("Starting Marketplace Parsing API")
    logger.info(f"Supported marketplaces: {parsing_service.factory.get_supported_marketplaces()}")

    if settings.get("debug", {}).get("active", False):
        logger.info(f"Documentation: http://{config['host']}:{config['port']}/docs")

    uvicorn.run("__main__:app", **config)
