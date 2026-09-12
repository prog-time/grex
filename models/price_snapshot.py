"""SQLAlchemy models for price-monitoring history and detected price changes.

PriceSnapshot is an append-only time series: one row per monitored parse, so
the price history of a product can be charted over time. PriceAlert logs a
detected price change that crossed a configured percentage threshold (drop or
rise). Both are written by the monitoring scheduler.
"""
from __future__ import annotations

import uuid
from datetime import datetime

from sqlalchemy import Column, DateTime, Float, String, Text

from models.job import Base


class PriceSnapshot(Base):
    __tablename__ = "price_snapshots"

    id = Column(String(36), primary_key=True, default=lambda: str(uuid.uuid4()))
    target_id = Column(String(36), nullable=True, index=True)
    url = Column(Text, nullable=False)
    marketplace = Column(String(64), nullable=True)
    current_price = Column(Float, nullable=True)
    original_price = Column(Float, nullable=True)
    available = Column(String(16), nullable=True)  # reserved for stock tracking (future)
    parsed_at = Column(DateTime, nullable=False, default=datetime.utcnow, index=True)

    def to_dict(self) -> dict:
        return {
            "id": self.id,
            "target_id": self.target_id,
            "url": self.url,
            "marketplace": self.marketplace,
            "current_price": self.current_price,
            "original_price": self.original_price,
            "parsed_at": self.parsed_at.isoformat() if self.parsed_at else None,
        }


class PriceAlert(Base):
    __tablename__ = "price_alerts"

    id = Column(String(36), primary_key=True, default=lambda: str(uuid.uuid4()))
    target_id = Column(String(36), nullable=True, index=True)
    url = Column(Text, nullable=False)
    marketplace = Column(String(64), nullable=True)
    title = Column(Text, nullable=True)
    kind = Column(String(16), nullable=False)  # "drop" | "rise"
    old_price = Column(Float, nullable=True)
    new_price = Column(Float, nullable=True)
    change_pct = Column(Float, nullable=False)  # signed: negative = drop, positive = rise
    created_at = Column(DateTime, nullable=False, default=datetime.utcnow, index=True)

    def to_dict(self) -> dict:
        return {
            "id": self.id,
            "target_id": self.target_id,
            "url": self.url,
            "marketplace": self.marketplace,
            "title": self.title,
            "kind": self.kind,
            "old_price": self.old_price,
            "new_price": self.new_price,
            "change_pct": round(self.change_pct, 2),
            "created_at": self.created_at.isoformat() if self.created_at else None,
        }
