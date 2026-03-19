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

    parsed_at: datetime = field(default_factory=datetime.now)

    def to_dict(self) -> dict:
        return {
            "title": self.title,
            "sku": self.sku,
            "source_url": self.source_url,
            "original_price": float(self.original_price) if self.original_price else None,
            "current_price": float(self.current_price) if self.current_price else None,
            "images": [
                image_url for image_url in self.images
            ],
        }


class ParseRequest(BaseModel):
    url: HttpUrl

    @field_validator('url')
    def validate_marketplace_url(cls, v):
        url_str = str(v).lower()
        supported_domains = [
            'wildberries.ru', 'wildberries.by', 'wildberries.kz',
            'ozon.ru', 'ozon.by', 'ozon.kz',
            'market.yandex.ru', 'market.yandex.by',
            'megamarket.ru'
        ]

        if not any(domain in url_str for domain in supported_domains):
            raise ValueError(f"Unsupported marketplace. Supported: {', '.join(supported_domains)}")
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
    concurrency: int = Field(default=10, ge=1, le=50)

    @field_validator('urls')
    def validate_marketplace_urls(cls, v):
        supported_domains = [
            'wildberries.ru', 'wildberries.by', 'wildberries.kz',
            'ozon.ru', 'ozon.by', 'ozon.kz',
            'market.yandex.ru', 'market.yandex.by',
            'megamarket.ru'
        ]
        for url in v:
            url_str = str(url).lower()
            if not any(domain in url_str for domain in supported_domains):
                raise ValueError(f"Unsupported marketplace URL: {url}. Supported: {', '.join(supported_domains)}")
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
