"""Request/response schemas of the public monitoring API (/v1/monitors, /v1/alerts)."""
from __future__ import annotations

from typing import List, Optional

from pydantic import BaseModel, Field, HttpUrl, field_validator

from models.product import _validate_marketplace_urls


class MonitorCreateRequest(BaseModel):
    url: HttpUrl
    # None → monitoring.default_interval_seconds; at least monitoring.min_interval_seconds.
    interval_seconds: Optional[int] = Field(default=None, ge=1)
    name: Optional[str] = Field(default=None, max_length=255)

    @field_validator('url')
    def validate_marketplace_url(cls, v):
        return _validate_marketplace_urls([v])[0]


class MonitorUpdateRequest(BaseModel):
    """Partial update: only the fields present in the body are changed.

    ``interval_seconds: null`` resets the interval to the server default.
    """
    active: Optional[bool] = None
    interval_seconds: Optional[int] = Field(default=None, ge=1)
    name: Optional[str] = Field(default=None, max_length=255)

    @field_validator('active')
    def active_not_null(cls, v):
        if v is None:
            raise ValueError("active must be true or false")
        return v


class MonitorResponse(BaseModel):
    id: str
    url: str
    marketplace: str
    name: Optional[str] = None
    active: bool
    interval_seconds: int
    current_price: Optional[float] = None
    original_price: Optional[float] = None
    price_updated_at: Optional[str] = None
    last_checked_at: Optional[str] = None
    last_status: Optional[str] = None  # "ok" | "failed" | None (not checked yet)
    next_run_at: Optional[str] = None
    created_at: Optional[str] = None


class MonitorListResponse(BaseModel):
    monitors: List[MonitorResponse]


class PricePoint(BaseModel):
    at: Optional[str] = None
    current_price: Optional[float] = None
    original_price: Optional[float] = None


class PriceHistoryResponse(BaseModel):
    monitor_id: str
    points: List[PricePoint]


class PriceAlertResponse(BaseModel):
    id: str
    monitor_id: str
    url: str
    marketplace: Optional[str] = None
    title: Optional[str] = None
    kind: str  # "drop" | "rise"
    old_price: Optional[float] = None
    new_price: Optional[float] = None
    change_pct: float
    created_at: Optional[str] = None


class PriceAlertListResponse(BaseModel):
    alerts: List[PriceAlertResponse]
