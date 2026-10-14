"""Tests for public-API price monitors (services/api_monitors.py, /v1/monitors,
/v1/alerts) and their isolation from the admin panel.

Service and isolation tests run against a per-test temporary SQLite database
via the ``db`` fixture. HTTP tests use the TestClient (no lifespan → no DB)
with the monitor service patched.
"""
from __future__ import annotations

import asyncio
import json
from datetime import datetime, timedelta
from unittest.mock import AsyncMock, patch

import pytest
import pytest_asyncio
from fastapi.testclient import TestClient

import api
from services import api_monitors

_KEY = "key-a"
_OTHER_KEY = "key-b"
_URL = "https://www.wildberries.ru/catalog/17386348/detail.aspx"
_CFG = {"monitoring": {"default_interval_seconds": 3600, "min_interval_seconds": 300,
                       "drop_percent": 5.0, "rise_percent": 5.0}}


class FakeRequest:
    def __init__(self, body: dict | None = None, query: dict | None = None) -> None:
        self._body = body or {}
        self.query_params = query or {}

    async def json(self) -> dict:
        return self._body


def _payload(resp):
    return resp.status_code, json.loads(bytes(resp.body))


@pytest_asyncio.fixture
async def db(tmp_path):
    import models.api_job  # noqa: F401 — register tables
    import models.api_key  # noqa: F401
    import models.parse_target  # noqa: F401
    import models.parsed_product  # noqa: F401
    import models.price_snapshot  # noqa: F401
    from models import job as job_mod

    saved_engine, saved_session = job_mod.engine, job_mod.async_session
    await job_mod.init_db(f"sqlite:///{tmp_path}/mon.db")
    try:
        with patch.dict("config.settings", _CFG, clear=False):
            yield job_mod.async_session
    finally:
        from models.job import close_db
        await close_db()
        job_mod.async_session = saved_session
        job_mod.engine = saved_engine


async def _add(db, *objs):
    async with db() as s:
        for o in objs:
            s.add(o)
        await s.commit()


def _snapshot(target_id, price, minutes_ago=0, url=_URL):
    from models.price_snapshot import PriceSnapshot
    return PriceSnapshot(target_id=target_id, url=url, marketplace="wildberries", current_price=price,
                         original_price=price + 10, parsed_at=datetime.utcnow() - timedelta(minutes=minutes_ago))


def _alert(target_id, minutes_ago=0, url=_URL):
    from models.price_snapshot import PriceAlert
    return PriceAlert(target_id=target_id, url=url, marketplace="wildberries", title="T", kind="drop",
                      old_price=100.0, new_price=90.0, change_pct=-10.0,
                      created_at=datetime.utcnow() - timedelta(minutes=minutes_ago))


def _admin_target(id_="admin-t", url=_URL, **kw):
    from models.parse_target import ParseTarget
    return ParseTarget(id=id_, url=url, marketplace="wildberries", monitor_enabled=True, **kw)


# ---------------------------------------------------------------------------
# Service
# ---------------------------------------------------------------------------

class TestCreate:
    async def test_creates_active_monitor_due_now(self, db):
        m = await api_monitors.create_monitor(_KEY, _URL, None, "Матрас")
        assert m["active"] is True and m["name"] == "Матрас"
        assert m["interval_seconds"] == 3600            # server default
        assert m["marketplace"] == "wildberries"
        assert m["current_price"] is None and m["last_status"] is None
        assert datetime.fromisoformat(m["next_run_at"]) <= datetime.utcnow()

    async def test_duplicate_url_after_normalization_is_409(self, db):
        await api_monitors.create_monitor(_KEY, _URL, None, None)
        with pytest.raises(api_monitors.MonitorError) as e:
            await api_monitors.create_monitor(_KEY, _URL + "#reviews", None, None)
        assert e.value.status == 409

    async def test_same_url_allowed_for_another_key(self, db):
        await api_monitors.create_monitor(_KEY, _URL, None, None)
        await api_monitors.create_monitor(_OTHER_KEY, _URL, None, None)

    async def test_interval_below_minimum_is_422(self, db):
        with pytest.raises(api_monitors.MonitorError) as e:
            await api_monitors.create_monitor(_KEY, _URL, 60, None)
        assert e.value.status == 422

    async def test_limit_per_key_is_429(self, db):
        with patch.object(api_monitors, "MAX_MONITORS_PER_KEY", 1):
            await api_monitors.create_monitor(_KEY, _URL, None, None)
            with pytest.raises(api_monitors.MonitorError) as e:
                await api_monitors.create_monitor(_KEY, _URL.replace("17386348", "1"), None, None)
        assert e.value.status == 429


class TestConcurrentCreate:
    async def test_same_url_in_parallel_creates_one_monitor(self, db):
        results = await asyncio.gather(
            *[api_monitors.create_monitor(_KEY, _URL, None, None) for _ in range(10)], return_exceptions=True,
        )
        assert len([r for r in results if isinstance(r, dict)]) == 1
        assert all(r.status == 409 for r in results if isinstance(r, api_monitors.MonitorError))
        assert len(await api_monitors.list_monitors(_KEY)) == 1

    async def test_limit_holds_for_parallel_requests(self, db):
        with patch.object(api_monitors, "MAX_MONITORS_PER_KEY", 3):
            results = await asyncio.gather(
                *[api_monitors.create_monitor(_KEY, _URL.replace("17386348", str(i)), None, None)
                  for i in range(1, 11)],
                return_exceptions=True,
            )
        assert len([r for r in results if isinstance(r, dict)]) == 3
        assert all(r.status == 429 for r in results if isinstance(r, api_monitors.MonitorError))


class TestReadUpdateDelete:
    async def test_list_has_latest_price_and_only_own_monitors(self, db):
        mine = await api_monitors.create_monitor(_KEY, _URL, None, None)
        await api_monitors.create_monitor(_OTHER_KEY, _URL, None, None)
        await _add(db, _snapshot(mine["id"], 120.0, minutes_ago=10), _snapshot(mine["id"], 99.0))

        monitors = await api_monitors.list_monitors(_KEY)
        assert [m["id"] for m in monitors] == [mine["id"]]
        assert monitors[0]["current_price"] == 99.0 and monitors[0]["original_price"] == 109.0

    async def test_foreign_monitor_is_404_everywhere(self, db):
        m = await api_monitors.create_monitor(_KEY, _URL, None, None)
        calls = [
            api_monitors.get_monitor(_OTHER_KEY, m["id"]),
            api_monitors.update_monitor(_OTHER_KEY, m["id"], {"active": False}),
            api_monitors.delete_monitor(_OTHER_KEY, m["id"]),
            api_monitors.price_history(_OTHER_KEY, m["id"], 10),
        ]
        for call in calls:
            with pytest.raises(api_monitors.MonitorError) as e:
                await call
            assert e.value.status == 404
        assert (await api_monitors.get_monitor(_KEY, m["id"]))["active"] is True

    async def test_update_pause_interval_and_name(self, db):
        m = await api_monitors.create_monitor(_KEY, _URL, 600, None)
        m = await api_monitors.update_monitor(_KEY, m["id"], {"active": False, "name": "X"})
        assert m["active"] is False and m["name"] == "X" and m["next_run_at"] is None
        assert m["interval_seconds"] == 600                     # untouched
        m = await api_monitors.update_monitor(_KEY, m["id"], {"interval_seconds": None})
        assert m["interval_seconds"] == 3600                    # null → default

    async def test_update_interval_below_minimum_is_422(self, db):
        m = await api_monitors.create_monitor(_KEY, _URL, None, None)
        with pytest.raises(api_monitors.MonitorError) as e:
            await api_monitors.update_monitor(_KEY, m["id"], {"interval_seconds": 10})
        assert e.value.status == 422

    async def test_history_oldest_first(self, db):
        m = await api_monitors.create_monitor(_KEY, _URL, None, None)
        await _add(db, _snapshot(m["id"], 120.0, minutes_ago=10), _snapshot(m["id"], 99.0))
        points = await api_monitors.price_history(_KEY, m["id"], 10)
        assert [p["current_price"] for p in points] == [120.0, 99.0]
        assert [p["current_price"] for p in await api_monitors.price_history(_KEY, m["id"], 1)] == [99.0]

    async def test_delete_removes_history_and_alerts(self, db):
        from sqlalchemy import func, select

        from models.price_snapshot import PriceAlert, PriceSnapshot
        m = await api_monitors.create_monitor(_KEY, _URL, None, None)
        await _add(db, _snapshot(m["id"], 100.0), _alert(m["id"]))
        await api_monitors.delete_monitor(_KEY, m["id"])
        async with db() as s:
            for model in (PriceSnapshot, PriceAlert):
                assert (await s.execute(select(func.count()).select_from(model))).scalar_one() == 0
        assert await api_monitors.list_monitors(_KEY) == []


class TestAlerts:
    async def test_only_own_alerts_newest_first(self, db):
        mine = await api_monitors.create_monitor(_KEY, _URL, None, None)
        other = await api_monitors.create_monitor(_OTHER_KEY, _URL, None, None)
        await _add(db, _alert(mine["id"], minutes_ago=30), _alert(mine["id"], minutes_ago=5),
                   _alert(other["id"]), _admin_target(), _alert("admin-t"))

        alerts = await api_monitors.list_alerts(_KEY, None, 100)
        assert len(alerts) == 2 and {a["monitor_id"] for a in alerts} == {mine["id"]}
        assert alerts[0]["created_at"] > alerts[1]["created_at"]

    async def test_since_filter(self, db):
        m = await api_monitors.create_monitor(_KEY, _URL, None, None)
        await _add(db, _alert(m["id"], minutes_ago=30), _alert(m["id"], minutes_ago=5))
        since = datetime.utcnow() - timedelta(minutes=10)
        assert len(await api_monitors.list_alerts(_KEY, since, 100)) == 1


class TestKeyLifecycle:
    async def test_revoke_pauses_and_delete_purges(self, db):
        from sqlalchemy import func, select

        from admin.resources.api_keys import ApiKeysRouter
        from models.api_job import ApiJob
        from models.price_snapshot import PriceSnapshot
        from services import api_keys

        _, key = await api_keys.create_key("client")
        m = await api_monitors.create_monitor(key["id"], _URL, None, None)
        await _add(db, _snapshot(m["id"], 100.0), ApiJob(api_key_id=key["id"], total=1))

        with patch("services.api_jobs.cancel_jobs_for_key", new=AsyncMock(return_value=0)) as cancel:
            await ApiKeysRouter.revoke_key(FakeRequest({"id": key["id"]}))
        cancel.assert_awaited_once_with(key["id"])
        assert (await api_monitors.get_monitor(key["id"], m["id"]))["active"] is False

        status, _ = _payload(await ApiKeysRouter.delete_key(FakeRequest({"id": key["id"]})))
        assert status == 200
        assert await api_monitors.list_monitors(key["id"]) == []
        async with db() as s:
            for model in (PriceSnapshot, ApiJob):
                assert (await s.execute(select(func.count()).select_from(model))).scalar_one() == 0


# ---------------------------------------------------------------------------
# Isolation from the admin panel
# ---------------------------------------------------------------------------

class TestAdminIsolation:
    @pytest_asyncio.fixture
    async def mixed(self, db):
        """One admin target and one API monitor on the same URL, each with history."""
        api_m = await api_monitors.create_monitor(_KEY, _URL, None, None)
        await _add(db, _admin_target(),
                   _snapshot("admin-t", 100.0, minutes_ago=20), _snapshot(api_m["id"], 50.0, minutes_ago=10),
                   _alert("admin-t"), _alert(api_m["id"]))
        return api_m["id"]

    async def test_admin_targets_list(self, mixed):
        from admin.resources.targets import TargetsRouter
        _, body = _payload(await TargetsRouter.list_targets(FakeRequest()))
        assert [t["id"] for t in body["targets"]] == ["admin-t"]

    async def test_admin_monitors_list(self, mixed):
        from admin.resources.monitors import MonitorsRouter
        _, body = _payload(await MonitorsRouter.list_monitors(FakeRequest()))
        assert [m["id"] for m in body["monitors"]] == ["admin-t"]

    async def test_admin_history_excludes_api_snapshots(self, mixed):
        from admin.resources.monitors import MonitorsRouter
        _, body = _payload(await MonitorsRouter.history(FakeRequest(query={"target_id": "admin-t"})))
        assert [p["current_price"] for p in body["points"]] == [100.0]

    async def test_admin_cannot_open_api_monitor(self, mixed):
        from admin.resources.monitors import MonitorsRouter
        from admin.resources.targets import TargetsRouter
        status, _ = _payload(await MonitorsRouter.history(FakeRequest(query={"target_id": mixed})))
        assert status == 404
        status, _ = _payload(await TargetsRouter.delete_target(FakeRequest({"id": mixed})))
        assert status == 404

    async def test_admin_alerts(self, mixed):
        from admin.resources.monitors import MonitorsRouter
        _, body = _payload(await MonitorsRouter.alerts(FakeRequest()))
        assert [a["target_id"] for a in body["alerts"]] == ["admin-t"]

    async def test_admin_clear_all_keeps_api_monitors(self, mixed):
        from admin.resources.targets import TargetsRouter
        _, body = _payload(await TargetsRouter.clear_all_targets(FakeRequest()))
        assert body["deleted"] == 1
        assert [m["id"] for m in await api_monitors.list_monitors(_KEY)] == [mixed]

    async def test_admin_clear_monitoring_keeps_targets_and_api_monitors(self, db, mixed):
        from admin.resources.monitors import MonitorsRouter
        status, body = _payload(await MonitorsRouter.clear_all(FakeRequest()))
        assert status == 200
        assert (body["targets"], body["snapshots"], body["alerts"]) == (1, 1, 1)

        _, listed = _payload(await MonitorsRouter.list_monitors(FakeRequest()))
        assert [(m["id"], m["monitor_enabled"], m["next_run_at"]) for m in listed["monitors"]] == \
            [("admin-t", False, None)]
        _, alerts = _payload(await MonitorsRouter.alerts(FakeRequest()))
        assert alerts["alerts"] == []

        api_m = await api_monitors.get_monitor(_KEY, mixed)
        assert api_m["active"] is True and api_m["current_price"] == 50.0
        assert len(await api_monitors.list_alerts(_KEY, None, 100)) == 1

    async def test_report_excludes_api_data(self, mixed):
        from services.reporting import _collect_report_data
        data = await _collect_report_data(30)
        assert len(data["products"]) == 1
        assert [h["current_price"] for h in data["history"]] == [100.0]
        assert len(data["alerts"]) == 1

    async def test_previous_price_is_scoped_by_owner(self, db, mixed):
        from services import monitoring as M
        async with db() as s:
            assert await M._previous_price(s, {"id": "admin-t", "url": _URL}) == 100.0
            assert await M._previous_price(s, {"id": mixed, "url": _URL, "api_key_id": _KEY}) == 50.0

    async def test_check_skips_admin_products_for_api_monitor(self, db, mixed):
        from decimal import Decimal

        from sqlalchemy import func, select

        from models.parsed_product import ParsedProduct
        from models.product import ProductCard
        from services import monitoring as M

        card = ProductCard(title="T", sku="1", original_price=Decimal("90"), current_price=Decimal("80"),
                           images=[], source_url=_URL)
        with patch("services.parsing.ParsingService.parse_product", new=AsyncMock(return_value=card)):
            status = await M._check_one({"id": mixed, "url": _URL, "marketplace": "wildberries",
                                         "api_key_id": _KEY})
        assert status == "ok"
        async with db() as s:
            assert (await s.execute(select(func.count()).select_from(ParsedProduct))).scalar_one() == 0
        assert (await api_monitors.get_monitor(_KEY, mixed))["current_price"] == 80.0


# ---------------------------------------------------------------------------
# HTTP
# ---------------------------------------------------------------------------

_API_KEY = "grx_test-key"
_HEADERS = {"X-API-Key": _API_KEY}
_MONITOR = {
    "id": "m1", "url": _URL, "marketplace": "wildberries", "name": None, "active": True,
    "interval_seconds": 3600, "current_price": None, "original_price": None, "price_updated_at": None,
    "last_checked_at": None, "last_status": None, "next_run_at": None, "created_at": "2026-10-06T10:00:00",
}


@pytest.fixture()
def client():
    async def fake_verify(key: str):
        return _KEY if key == _API_KEY else None

    with patch("services.api_keys.verify_key", new=fake_verify):
        yield TestClient(api.app, raise_server_exceptions=False)


class TestMonitorEndpoints:
    def test_create_returns_201(self, client):
        with patch("services.api_monitors.create_monitor", new=AsyncMock(return_value=_MONITOR)) as create:
            r = client.post("/v1/monitors", json={"url": _URL, "interval_seconds": 600}, headers=_HEADERS)
        assert r.status_code == 201 and r.json()["id"] == "m1"
        assert create.call_args.args == (_KEY, _URL, 600, None)

    def test_requires_api_key(self, client):
        assert client.get("/v1/monitors").status_code == 401
        assert client.get("/v1/alerts").status_code == 401

    def test_create_rejects_unsupported_url(self, client):
        r = client.post("/v1/monitors", json={"url": "https://amazon.com/dp/1"}, headers=_HEADERS)
        assert r.status_code == 422

    def test_service_errors_map_to_status(self, client):
        err = api_monitors.MonitorError(409, "This URL is already monitored: m1")
        with patch("services.api_monitors.create_monitor", new=AsyncMock(side_effect=err)):
            r = client.post("/v1/monitors", json={"url": _URL}, headers=_HEADERS)
        assert r.status_code == 409 and "already monitored" in r.json()["detail"]

    def test_get_unknown_is_404(self, client):
        err = api_monitors.MonitorError(404, "Monitor not found")
        with patch("services.api_monitors.get_monitor", new=AsyncMock(side_effect=err)):
            assert client.get("/v1/monitors/nope", headers=_HEADERS).status_code == 404

    def test_patch_passes_only_sent_fields(self, client):
        with patch("services.api_monitors.update_monitor", new=AsyncMock(return_value=_MONITOR)) as upd:
            r = client.patch("/v1/monitors/m1", json={"interval_seconds": None}, headers=_HEADERS)
        assert r.status_code == 200
        assert upd.call_args.args == (_KEY, "m1", {"interval_seconds": None})

    def test_patch_rejects_null_active(self, client):
        assert client.patch("/v1/monitors/m1", json={"active": None}, headers=_HEADERS).status_code == 422

    def test_delete_returns_204(self, client):
        with patch("services.api_monitors.delete_monitor", new=AsyncMock(return_value=None)) as dele:
            r = client.delete("/v1/monitors/m1", headers=_HEADERS)
        assert r.status_code == 204 and r.content == b""
        assert dele.call_args.args == (_KEY, "m1")

    def test_history(self, client):
        points = [{"at": "2026-10-06T10:00:00", "current_price": 99.0, "original_price": 109.0}]
        with patch("services.api_monitors.price_history", new=AsyncMock(return_value=points)) as hist:
            r = client.get("/v1/monitors/m1/history?limit=50", headers=_HEADERS)
        assert r.status_code == 200 and r.json() == {"monitor_id": "m1", "points": points}
        assert hist.call_args.args == (_KEY, "m1", 50)

    def test_alerts_since_is_converted_to_naive_utc(self, client):
        with patch("services.api_monitors.list_alerts", new=AsyncMock(return_value=[])) as lst:
            r = client.get("/v1/alerts", params={"since": "2026-10-06T13:00:00+03:00"}, headers=_HEADERS)
        assert r.status_code == 200 and r.json() == {"alerts": []}
        assert lst.call_args.args == (_KEY, datetime(2026, 10, 6, 10, 0, 0), 100)
