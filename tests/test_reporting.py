"""Tests for monitoring report generation and scheduled delivery.

The report-file path globals are patched to a tmp dir; DB-touching code runs
against a per-test temporary SQLite database (globals restored afterwards).
Channel sending is mocked — these tests never hit Telegram/SMTP.
"""
from __future__ import annotations

from datetime import datetime
from unittest.mock import AsyncMock, patch

import pytest_asyncio

from services import reporting


@pytest_asyncio.fixture
async def db(tmp_path):
    import models.alert_source  # noqa: F401 — register tables
    import models.parse_target  # noqa: F401
    import models.parsed_product  # noqa: F401
    from models import job as job_mod

    saved_engine, saved_session = job_mod.engine, job_mod.async_session
    await job_mod.init_db(f"sqlite:///{tmp_path}/rep.db")
    try:
        yield job_mod.async_session
    finally:
        from models.job import close_db
        await close_db()
        job_mod.async_session = saved_session
        job_mod.engine = saved_engine


def test_frequency_seconds_matches_alert_frequencies():
    from admin.resources.alert_sources import _FREQUENCIES

    assert set(reporting.FREQUENCY_SECONDS) == set(_FREQUENCIES)


class TestGenerate:
    async def test_generate_includes_only_active_monitored(self, db, tmp_path):
        from models.parse_target import ParseTarget
        from models.parsed_product import ParsedProduct

        async with db() as s:
            s.add(ParseTarget(id="t1", url="https://ozon.ru/product/x-1", marketplace="ozon", monitor_enabled=True))
            s.add(ParseTarget(id="t2", url="https://ozon.ru/product/y-2", marketplace="ozon", monitor_enabled=False))
            s.add(ParsedProduct(url="https://ozon.ru/product/x-1", title="Товар X",
                                current_price=100.0, original_price=150.0, marketplace="ozon"))
            await s.commit()

        latest = tmp_path / "monitoring_latest.xlsx"
        with patch.object(reporting, "_REPORTS_DIR", str(tmp_path)), \
                patch.object(reporting, "_LATEST", str(latest)):
            path = await reporting.generate_report()

        assert path == str(latest) and latest.exists()
        from openpyxl import load_workbook
        ws = load_workbook(latest).active
        values = [[c.value for c in row] for row in ws.iter_rows()]
        assert values[2][0] == "Товар"            # header row (after title + blank)
        data_rows = [r for r in values[3:] if r[0]]
        assert len(data_rows) == 1                 # only the active monitored target
        assert data_rows[0][0] == "Товар X"
        assert data_rows[0][3] == 100.0 and data_rows[0][4] == 150.0


class TestEnsureFresh:
    async def test_generates_when_missing(self, tmp_path):
        latest = tmp_path / "none.xlsx"
        with patch.object(reporting, "_LATEST", str(latest)), \
                patch.object(reporting, "generate_report", new=AsyncMock(return_value=str(latest))) as gen:
            await reporting.ensure_report_fresh()
        gen.assert_awaited_once()

    async def test_skips_when_fresh(self, tmp_path):
        latest = tmp_path / "fresh.xlsx"
        latest.write_text("x")  # just created → age ~0 < interval
        with patch.object(reporting, "_LATEST", str(latest)), \
                patch.object(reporting, "generate_report", new=AsyncMock()) as gen:
            await reporting.ensure_report_fresh()
        gen.assert_not_awaited()


class TestDeliver:
    async def test_delivers_to_due_only_and_updates_last_sent(self, db, tmp_path):
        from sqlalchemy import select

        from models.alert_source import AlertSource

        report = tmp_path / "r.xlsx"
        report.write_text("x")
        now = datetime.utcnow()
        async with db() as s:
            s.add(AlertSource(id="a1", type="telegram", recipient="@u", chat_id="111",
                              frequency="hourly", last_sent_at=None))           # due
            s.add(AlertSource(id="a2", type="telegram", recipient="@v", chat_id="222",
                              frequency="hourly", last_sent_at=now))            # not due
            await s.commit()

        with patch.object(reporting, "_LATEST", str(report)), \
                patch.object(reporting, "_send_one", new=AsyncMock(return_value=True)) as send:
            sent = await reporting.deliver_due()

        assert sent == 1 and send.await_count == 1
        async with db() as s:
            by_id = {r.id: r.last_sent_at for r in (await s.execute(select(AlertSource))).scalars().all()}
        assert by_id["a1"] is not None        # due source marked as sent
        assert by_id["a2"] == now             # not-due source untouched

    async def test_does_not_update_last_sent_on_failure(self, db, tmp_path):
        from sqlalchemy import select

        from models.alert_source import AlertSource

        report = tmp_path / "r.xlsx"
        report.write_text("x")
        async with db() as s:
            s.add(AlertSource(id="a1", type="telegram", recipient="@u", chat_id="1", frequency="hourly", last_sent_at=None))
            await s.commit()

        with patch.object(reporting, "_LATEST", str(report)), \
                patch.object(reporting, "_send_one", new=AsyncMock(return_value=False)):
            sent = await reporting.deliver_due()

        assert sent == 0
        async with db() as s:
            src = (await s.execute(select(AlertSource))).scalars().first()
        assert src.last_sent_at is None       # not marked sent when delivery failed

    async def test_no_report_means_no_delivery(self, db, tmp_path):
        from models.alert_source import AlertSource

        async with db() as s:
            s.add(AlertSource(id="a1", type="telegram", recipient="@u", chat_id="1", frequency="hourly"))
            await s.commit()

        missing = tmp_path / "absent.xlsx"
        with patch.object(reporting, "_LATEST", str(missing)), \
                patch.object(reporting, "_send_one", new=AsyncMock(return_value=True)) as send:
            sent = await reporting.deliver_due()
        assert sent == 0 and send.await_count == 0
