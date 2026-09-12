"""SQLAlchemy model for parsing job history."""
from __future__ import annotations

import uuid
from enum import Enum

from sqlalchemy import Column, DateTime, Float, String, Text
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine
from sqlalchemy.orm import DeclarativeBase


class Base(DeclarativeBase):
    pass


class JobStatus(str, Enum):
    success = "success"
    failed = "failed"


class Job(Base):
    __tablename__ = "jobs"

    id = Column(String(36), primary_key=True, default=lambda: str(uuid.uuid4()))
    url = Column(Text, nullable=False)
    marketplace = Column(String(64), nullable=True)
    status = Column(String(16), nullable=False)
    started_at = Column(DateTime, nullable=False)
    finished_at = Column(DateTime, nullable=True)
    processing_time = Column(Float, nullable=True)
    error = Column(Text, nullable=True)

    def to_dict(self) -> dict:
        return {
            "id": self.id,
            "url": self.url,
            "marketplace": self.marketplace,
            "status": self.status,
            "started_at": self.started_at.isoformat() if self.started_at else None,
            "finished_at": self.finished_at.isoformat() if self.finished_at else None,
            "processing_time": self.processing_time,
            "error": self.error,
        }


# Module-level engine and session factory — initialised by init_db().
engine = None
async_session: async_sessionmaker[AsyncSession] | None = None


async def init_db(db_url: str) -> None:
    """Create async engine, session factory, and all tables."""
    global engine, async_session  # noqa: PLW0603

    # Convert plain sqlite:// to async variant
    if db_url.startswith("sqlite:///") and "+aiosqlite" not in db_url:
        db_url = db_url.replace("sqlite:///", "sqlite+aiosqlite:///")

    engine = create_async_engine(db_url, echo=False)
    async_session = async_sessionmaker(engine, expire_on_commit=False)

    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
        await conn.run_sync(_migrate_added_columns)


# Lightweight, dependency-free column migration. create_all() creates missing
# TABLES but never ALTERs existing ones, so columns added to a model after its
# table already exists (e.g. the monitoring fields on parse_targets) must be
# added explicitly. Idempotent — only adds columns that are absent.
_ADDED_COLUMNS: dict[str, dict[str, str]] = {
    "parse_targets": {
        "monitor_enabled": "BOOLEAN NOT NULL DEFAULT 0",
        "interval_seconds": "INTEGER",
        "next_run_at": "DATETIME",
        "last_checked_at": "DATETIME",
        "last_status": "VARCHAR(16)",
    },
    "alert_sources": {
        "last_sent_at": "DATETIME",
        "chat_id": "VARCHAR(32)",
    },
}


def _migrate_added_columns(sync_conn) -> None:  # type: ignore[no-untyped-def]
    from sqlalchemy import inspect, text  # noqa: PLC0415

    inspector = inspect(sync_conn)
    for table, columns in _ADDED_COLUMNS.items():
        try:
            existing = {c["name"] for c in inspector.get_columns(table)}
        except Exception:
            continue  # table not present yet — create_all will have made it complete
        for name, ddl in columns.items():
            if name not in existing:
                sync_conn.execute(text(f"ALTER TABLE {table} ADD COLUMN {name} {ddl}"))


async def close_db() -> None:
    global engine  # noqa: PLW0603
    if engine is not None:
        await engine.dispose()
        engine = None
