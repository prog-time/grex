from decimal import Decimal
from datetime import datetime
from dataclasses import dataclass, field
from typing import List, Optional, Dict, Any
from pydantic import BaseModel, HttpUrl, Field, field_validator


@dataclass
class ProductCard:
    title: str
    sku: str
    original_price: Decimal
    current_price: Decimal
    images: List[str]
    source_url: str

    description: str = ""
    characteristics: Dict[str, Any] = field(default_factory=dict)
    parsed_at: datetime = field(default_factory=datetime.now)

    def to_dict(self) -> dict:
        return {
            "title": self.title,
            "sku": self.sku,
            "source_url": self.source_url,
            "original_price": float(self.original_price) if self.original_price else None,
            "current_price": float(self.current_price) if self.current_price else None,
            "images": [image_url for image_url in self.images],
            "description": self.description,
            "characteristics": self.characteristics,
        }


class ParseRequest(BaseModel):
    url: HttpUrl

    @field_validator('url')
    def validate_marketplace_url(cls, v):
        # Validate against the live crawler registry (single source of truth)
        # instead of a hardcoded domain list, so every registered crawler
        # (mm, bigam, vseinstrumenti, …) is accepted automatically.
        from crawlers.registry import CrawlerRegistry  # noqa: PLC0415
        from core.exceptions import UnsupportedMarketplaceError  # noqa: PLC0415
        try:
            CrawlerRegistry.detect_marketplace(str(v))
        except UnsupportedMarketplaceError:
            supported = ', '.join(sorted(CrawlerRegistry.all().keys()))
            raise ValueError(f"Unsupported marketplace. Supported: {supported}")
        return v


class ParseResponse(BaseModel):
    success: bool
    data: Optional[Dict[str, Any]] = None
    error: Optional[str] = None
    marketplace: Optional[str] = None
    processing_time: Optional[float] = None


class HealthResponse(BaseModel):
    status: str
    uptime: float
    supported_marketplaces: List[str]


class BatchParseRequest(BaseModel):
    urls: List[HttpUrl] = Field(..., min_length=1, max_length=100)
    # None → use batch.concurrency from settings. Capped to batch.max_concurrency
    # by the service, so the effective limit is configurable, not hardcoded.
    concurrency: Optional[int] = Field(default=None, ge=1, le=200)

    @field_validator('urls')
    def validate_marketplace_urls(cls, v):
        from crawlers.registry import CrawlerRegistry  # noqa: PLC0415
        from core.exceptions import UnsupportedMarketplaceError  # noqa: PLC0415
        for url in v:
            try:
                CrawlerRegistry.detect_marketplace(str(url))
            except UnsupportedMarketplaceError:
                supported = ', '.join(sorted(CrawlerRegistry.all().keys()))
                raise ValueError(
                    f"Unsupported marketplace URL: {url}. Supported: {supported}"
                )
        return v


class BatchItemResult(BaseModel):
    url: str
    success: bool
    data: Optional[Dict[str, Any]] = None
    error: Optional[str] = None
    marketplace: Optional[str] = None
    processing_time: Optional[float] = None


class BatchParseResponse(BaseModel):
    total: int
    succeeded: int
    failed: int
    processing_time: float
    results: List[BatchItemResult]
