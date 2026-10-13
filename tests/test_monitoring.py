"""Tests for the product-monitoring engine (services/monitoring.py).

Pure helpers (pct/threshold/interval/scheduling math) are tested directly.
The DB-touching parts run against a per-test temporary SQLite database via the
``db`` fixture, which carefully restores the module-global engine/session
afterwards so it can't leak into other test modules (e.g. the readiness tests
that assert the DB session factory is None).
"""
from __future__ import annotations

from datetime import datetime, timedelta
from unittest.mock import patch

import pytest
import pytest_asyncio

from services import monitoring as M

_CFG = {"monitoring": {
    "enabled": True,
    "default_interval_seconds": 3600,
    "min_interval_seconds": 300,
    "drop_percent": 5.0,
    "rise_percent": 5.0,
    "jitter_seconds": 0,
}}


# ---------------------------------------------------------------------------
# Pure helpers
# ---------------------------------------------------------------------------

class TestPctChange:
    def test_drop(self):
        assert M.pct_change(100, 90) == pytest.approx(-10.0)

    def test_rise(self):
        assert M.pct_change(100, 125) == pytest.approx(25.0)

    def test_none_on_missing_or_zero(self):
        assert M.pct_change(None, 90) is None
        assert M.pct_change(100, None) is None
        assert M.pct_change(0, 90) is None


class TestClassifyChange:
    def test_drop_crosses_threshold(self):
        assert M.classify_change(100, 94, 5.0, 5.0) == "drop"

    def test_rise_crosses_threshold(self):
        assert M.classify_change(100, 106, 5.0, 5.0) == "rise"

    def test_below_threshold_is_none(self):
        assert M.classify_change(100, 97, 5.0, 5.0) is None
        assert M.classify_change(100, 103, 5.0, 5.0) is None

    def test_exactly_at_threshold_triggers(self):
        assert M.classify_change(100, 95, 5.0, 5.0) == "drop"
        assert M.classify_change(100, 105, 5.0, 5.0) == "rise"

    def test_asymmetric_thresholds(self):
        # 3% drop threshold, 10% rise threshold
        assert M.classify_change(100, 96, 3.0, 10.0) == "drop"
        assert M.classify_change(100, 106, 3.0, 10.0) is None


class TestIntervalAndSchedule:
    def test_effective_interval_uses_default(self):
        with patch.dict("config.settings", _CFG, clear=False):
            assert M.effective_interval(None) == 3600

    def test_effective_interval_floored_to_min(self):
        with patch.dict("config.settings", _CFG, clear=False):
            assert M.effective_interval(60) == 300

    def test_effective_interval_respects_explicit(self):
        with patch.dict("config.settings", _CFG, clear=False):
            assert M.effective_interval(7200) == 7200

    def test_compute_next_run_no_jitter(self):
        now = datetime(2026, 6, 1, 12, 0, 0)
        assert M.compute_next_run(now, 3600, 0) == now + timedelta(seconds=3600)

    def test_compute_next_run_within_jitter_window(self):
        now = datetime(2026, 6, 1, 12, 0, 0)
        nxt = M.compute_next_run(now, 3600, 30)
        assert now + timedelta(seconds=3600) <= nxt <= now + timedelta(seconds=3630)


# ---------------------------------------------------------------------------
# DB-backed parts
# ---------------------------------------------------------------------------

@pytest_asyncio.fixture
async def db(tmp_path):
    import models.parse_target  # noqa: F401 — register tables
    import models.price_snapshot  # noqa: F401
    from models import job as job_mod

    saved_engine, saved_session = job_mod.engine, job_mod.async_session
    await job_mod.init_db(f"sqlite:///{tmp_path}/mon.db")
    try:
        yield job_mod.async_session
    finally:
        from models.job import close_db
        await close_db()
        job_mod.async_session = saved_session  # restore None for other test modules
        job_mod.engine = saved_engine


class TestSnapshotAndAlert:
    async def test_record_and_previous_price(self, db):
        async with db() as s:
            await M._record_snapshot(s, target_id="t1", url="u1", marketplace="ozon",
                                     current_price=100.0, original_price=120.0)
            await s.commit()
        async with db() as s:
            assert await M._previous_price(s, {"id": "t1", "url": "u1"}) == 100.0
            assert await M._previous_price(s, {"id": "t9", "url": "missing"}) is None

    async def test_alert_logged_on_drop(self, db):
        from sqlalchemy import select

        from models.price_snapshot import PriceAlert
        with patch.dict("config.settings", _CFG, clear=False):
            async with db() as s:
                kind = await M._maybe_log_alert(s, target_id="t1", url="u1", marketplace="ozon",
                                                title="T", old_price=100.0, new_price=90.0)
                await s.commit()
            assert kind == "drop"
            async with db() as s:
                alerts = (await s.execute(select(PriceAlert))).scalars().all()
            assert len(alerts) == 1
            assert alerts[0].kind == "drop"
            assert alerts[0].change_pct == pytest.approx(-10.0)

    async def test_no_alert_below_threshold(self, db):
        from sqlalchemy import select

        from models.price_snapshot import PriceAlert
        with patch.dict("config.settings", _CFG, clear=False):
            async with db() as s:
                kind = await M._maybe_log_alert(s, target_id="t1", url="u1", marketplace="ozon",
                                                title="T", old_price=100.0, new_price=98.0)
                await s.commit()
            assert kind is None
            async with db() as s:
                alerts = (await s.execute(select(PriceAlert))).scalars().all()
            assert alerts == []


class TestColumnMigration:
    async def test_adds_monitoring_columns_to_existing_table_idempotently(self, tmp_path):
        import sqlite3

        import models.parse_target  # noqa: F401
        import models.price_snapshot  # noqa: F401
        from models import job as job_mod

        dbfile = tmp_path / "legacy.db"
        # Simulate a pre-existing admin.db with the OLD parse_targets schema.
        con = sqlite3.connect(dbfile)
        con.execute(
            "CREATE TABLE parse_targets (id TEXT PRIMARY KEY, name TEXT, url TEXT, "
            "marketplace TEXT, enabled BOOLEAN, created_at DATETIME)"
        )
        con.commit()
        con.close()

        saved_engine, saved_session = job_mod.engine, job_mod.async_session
        try:
            await job_mod.init_db(f"sqlite:///{dbfile}")
            await job_mod.init_db(f"sqlite:///{dbfile}")  # second run must be a no-op
        finally:
            from models.job import close_db
            await close_db()
            job_mod.async_session = saved_session
            job_mod.engine = saved_engine

        con = sqlite3.connect(dbfile)
        cols = {r[1] for r in con.execute("PRAGMA table_info(parse_targets)")}
        tables = {r[0] for r in con.execute("SELECT name FROM sqlite_master WHERE type='table'")}
        con.close()
        assert {"monitor_enabled", "interval_seconds", "next_run_at",
                "last_checked_at", "last_status"} <= cols
        assert {"price_snapshots", "price_alerts"} <= tables


class TestDueTargets:
    async def test_selects_only_due_and_enabled(self, db):
        from models.parse_target import ParseTarget

        now = datetime.utcnow()
        async with db() as s:
            s.add_all([
                ParseTarget(id="due-null", url="https://ozon.ru/product/a-1", marketplace="ozon",
                            monitor_enabled=True, next_run_at=None),
                ParseTarget(id="due-past", url="https://ozon.ru/product/b-2", marketplace="ozon",
                            monitor_enabled=True, next_run_at=now - timedelta(minutes=5)),
                ParseTarget(id="future", url="https://ozon.ru/product/c-3", marketplace="ozon",
                            monitor_enabled=True, next_run_at=now + timedelta(hours=1)),
                ParseTarget(id="disabled", url="https://ozon.ru/product/d-4", marketplace="ozon",
                            monitor_enabled=False, next_run_at=None),
            ])
            await s.commit()
        async with db() as s:
            due = await M._due_targets(s, limit=50)
        ids = {d["id"] for d in due}
        assert ids == {"due-null", "due-past"}
