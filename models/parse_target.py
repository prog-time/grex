"""SQLAlchemy model for parse targets — pages configured for scraping."""
from __future__ import annotations

import uuid
from datetime import datetime

from sqlalchemy import Boolean, Column, DateTime, String, Text

from models.job import Base


class ParseTarget(Base):
    __tablename__ = "parse_targets"

    id = Column(String(36), primary_key=True, default=lambda: str(uuid.uuid4()))
    name = Column(String(255), nullable=True)       # optional human label
    url = Column(Text, nullable=False)
    # Marketplace is detected from the URL at creation time and cached here.
    marketplace = Column(String(64), nullable=False)
    enabled = Column(Boolean, nullable=False, default=True)
    created_at = Column(DateTime, nullable=False, default=datetime.utcnow)

    def to_dict(self) -> dict:
        return {
            "id": self.id,
            "name": self.name or "",
            "url": self.url,
            "marketplace": self.marketplace,
            "enabled": self.enabled,
            "created_at": self.created_at.isoformat() if self.created_at else None,
        }
