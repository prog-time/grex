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
    recipient = Column(Text, nullable=False)        # telegram id/username or email (as entered)
    # Resolved Telegram chat id (bots can only message a numeric chat id).
    # NULL for mail sources.
    chat_id = Column(String(32), nullable=True)
    frequency = Column(String(16), nullable=False)  # hourly | every_3h | daily | weekly
    created_at = Column(DateTime, nullable=False, default=datetime.utcnow)
    # When a digest was last sent to this source. Set by the delivery phase;
    # NULL until the first send.
    last_sent_at = Column(DateTime, nullable=True)

    def to_dict(self) -> dict:
        return {
            "id": self.id,
            "type": self.type,
            "recipient": self.recipient,
            "chat_id": self.chat_id,
            "frequency": self.frequency,
            "created_at": self.created_at.isoformat() if self.created_at else None,
            "last_sent_at": self.last_sent_at.isoformat() if self.last_sent_at else None,
        }
