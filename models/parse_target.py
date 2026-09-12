"""SQLAlchemy model for parse targets — pages configured for scraping."""
from __future__ import annotations

import uuid
from datetime import datetime

from sqlalchemy import Boolean, Column, DateTime, Integer, String, Text

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

    # --- Monitoring (scheduled re-parsing) ---
    # When monitor_enabled, the scheduler re-parses this URL every
    # interval_seconds (NULL → the global monitoring.default_interval_seconds).
    monitor_enabled = Column(Boolean, nullable=False, default=False)
    interval_seconds = Column(Integer, nullable=True)
    next_run_at = Column(DateTime, nullable=True)      # due when <= now (or NULL)
    last_checked_at = Column(DateTime, nullable=True)
    last_status = Column(String(16), nullable=True)    # "ok" | "failed" | None

    def to_dict(self) -> dict:
        return {
            "id": self.id,
            "name": self.name or "",
            "url": self.url,
            "marketplace": self.marketplace,
            "enabled": self.enabled,
            "created_at": self.created_at.isoformat() if self.created_at else None,
            "monitor_enabled": bool(self.monitor_enabled),
            "interval_seconds": self.interval_seconds,
            "next_run_at": self.next_run_at.isoformat() if self.next_run_at else None,
            "last_checked_at": self.last_checked_at.isoformat() if self.last_checked_at else None,
            "last_status": self.last_status,
        }
