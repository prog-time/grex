"""SQLAlchemy models for parsed products and parse sessions."""
from __future__ import annotations

import json
import uuid
from datetime import datetime

from sqlalchemy import Column, DateTime, Float, Integer, String, Text

from models.job import Base


class ParseSession(Base):
    __tablename__ = "parse_sessions"

    id = Column(String(36), primary_key=True, default=lambda: str(uuid.uuid4()))
    status = Column(String(16), nullable=False, default="pending")  # pending|running|completed|failed
    total = Column(Integer, nullable=False, default=0)
    succeeded = Column(Integer, nullable=False, default=0)
    failed_count = Column(Integer, nullable=False, default=0)
    started_at = Column(DateTime, nullable=False, default=datetime.utcnow)
    finished_at = Column(DateTime, nullable=True)

    def to_dict(self) -> dict:
        return {
            "id": self.id,
            "status": self.status,
            "total": self.total,
            "succeeded": self.succeeded,
            "failed": self.failed_count,
            "started_at": self.started_at.isoformat() if self.started_at else None,
            "finished_at": self.finished_at.isoformat() if self.finished_at else None,
        }


class ParsedProduct(Base):
    __tablename__ = "parsed_products"

    id = Column(String(36), primary_key=True, default=lambda: str(uuid.uuid4()))
    session_id = Column(String(36), nullable=True)
    target_id = Column(String(36), nullable=True)
    url = Column(Text, nullable=False)
    marketplace = Column(String(64), nullable=True)
    title = Column(Text, nullable=True)
    sku = Column(String(255), nullable=True)
    original_price = Column(Float, nullable=True)
    current_price = Column(Float, nullable=True)
    images = Column(Text, nullable=True)          # JSON array
    description = Column(Text, nullable=True)
    characteristics = Column(Text, nullable=True)  # JSON object {name: value}
    parsed_at = Column(DateTime, nullable=False, default=datetime.utcnow)

    def to_dict(self) -> dict:
        images = []
        if self.images:
            try:
                images = json.loads(self.images)
            except Exception:  # noqa: BLE001
                pass
        chars = {}
        if self.characteristics:
            try:
                chars = json.loads(self.characteristics)
            except Exception:  # noqa: BLE001
                pass
        return {
            "id": self.id,
            "session_id": self.session_id,
            "target_id": self.target_id,
            "url": self.url,
            "marketplace": self.marketplace,
            "title": self.title,
            "sku": self.sku,
            "original_price": self.original_price,
            "current_price": self.current_price,
            "images": images,
            "description": self.description or "",
            "characteristics": chars,
            "parsed_at": self.parsed_at.isoformat() if self.parsed_at else None,
        }
