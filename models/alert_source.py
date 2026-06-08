"""SQLAlchemy model for monitoring alert sources (UI scaffold).

An *alert source* is a destination — Telegram or Email — that will, in a later
phase, receive a periodic Excel digest of all active monitored products. This
model stores only the configuration (type, recipient, frequency); the actual
digest generation and delivery are intentionally out of scope here.
"""
from __future__ import annotations

import uuid
from datetime import datetime

from sqlalchemy import Column, DateTime, String, Text

from models.job import Base


class AlertSource(Base):
    __tablename__ = "alert_sources"

    id = Column(String(36), primary_key=True, default=lambda: str(uuid.uuid4()))
    type = Column(String(16), nullable=False)       # "telegram" | "mail"
    recipient = Column(Text, nullable=False)        # telegram id/username or email
    frequency = Column(String(16), nullable=False)  # hourly | every_3h | daily | weekly
    created_at = Column(DateTime, nullable=False, default=datetime.utcnow)

    def to_dict(self) -> dict:
        return {
            "id": self.id,
            "type": self.type,
            "recipient": self.recipient,
            "frequency": self.frequency,
            "created_at": self.created_at.isoformat() if self.created_at else None,
        }
