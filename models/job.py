"""SQLAlchemy model for parsing job history."""
from __future__ import annotations

import uuid
from enum import Enum

from sqlalchemy import Column, DateTime, Float, String, Text, event
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

    # SQLite serialises writers; under concurrent batch parsing (up to
    # batch.max_concurrency simultaneous _save_job commits) the default rollback
    # journal raises "database is locked". Enable WAL (concurrent readers +
    # single writer) and a generous busy_timeout so writers wait instead of
    # failing. synchronous=NORMAL is safe and fast under WAL.
    if db_url.startswith("sqlite"):
        @event.listens_for(engine.sync_engine, "connect")
        def _set_sqlite_pragmas(dbapi_conn, _record):  # noqa: ANN001
            cursor = dbapi_conn.cursor()
            cursor.execute("PRAGMA journal_mode=WAL")
            cursor.execute("PRAGMA busy_timeout=30000")
            cursor.execute("PRAGMA synchronous=NORMAL")
            cursor.close()

    async_session = async_sessionmaker(engine, expire_on_commit=False)

    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)


async def close_db() -> None:
    global engine  # noqa: PLW0603
    if engine is not None:
        await engine.dispose()
        engine = None
