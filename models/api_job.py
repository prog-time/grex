"""SQLAlchemy models for asynchronous parse jobs of the public API (``/v1/jobs``).

A job belongs to the API key that created it. Each URL's outcome is stored as
its own :class:`ApiJobItem` row as soon as it finishes, so progress is visible
while the job runs.
"""
from __future__ import annotations

import json
import uuid
from datetime import datetime

from sqlalchemy import Column, DateTime, Float, ForeignKey, Integer, String, Text

from models.job import Base


class ApiJob(Base):
    __tablename__ = "api_jobs"

    id = Column(String(36), primary_key=True, default=lambda: str(uuid.uuid4()))
    api_key_id = Column(String(36), nullable=False, index=True)
    status = Column(String(16), nullable=False, default="pending")  # pending|running|completed|failed
    total = Column(Integer, nullable=False)
    done = Column(Integer, nullable=False, default=0)
    succeeded = Column(Integer, nullable=False, default=0)
    failed_count = Column(Integer, nullable=False, default=0)
    error = Column(Text, nullable=True)  # set when the whole job failed
    created_at = Column(DateTime, nullable=False, default=datetime.utcnow)
    started_at = Column(DateTime, nullable=True)
    finished_at = Column(DateTime, nullable=True)

    def to_dict(self) -> dict:
        return {
            "id": self.id,
            "status": self.status,
            "total": self.total,
            "done": self.done,
            "succeeded": self.succeeded,
            "failed": self.failed_count,
            "error": self.error,
            "created_at": self.created_at.isoformat() if self.created_at else None,
            "started_at": self.started_at.isoformat() if self.started_at else None,
            "finished_at": self.finished_at.isoformat() if self.finished_at else None,
        }


class ApiJobItem(Base):
    __tablename__ = "api_job_items"

    id = Column(Integer, primary_key=True, autoincrement=True)
    job_id = Column(String(36), ForeignKey("api_jobs.id", ondelete="CASCADE"), nullable=False, index=True)
    position = Column(Integer, nullable=False)  # index of the URL in the request
    url = Column(Text, nullable=False)
    success = Column(Integer, nullable=False)  # 0/1
    marketplace = Column(String(64), nullable=True)
    data = Column(Text, nullable=True)  # JSON-encoded product card
    error = Column(Text, nullable=True)
    processing_time = Column(Float, nullable=True)

    def to_dict(self) -> dict:
        return {
            "url": self.url,
            "success": bool(self.success),
            "data": json.loads(self.data) if self.data else None,
            "error": self.error,
            "marketplace": self.marketplace,
            "processing_time": self.processing_time,
        }
