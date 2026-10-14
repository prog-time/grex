"""Admin panel POST handlers: a success case and a bad request for each.

Handlers are called directly with a fake request against a temporary SQLite
database. Parsing is replaced by fakes, so no request reaches a marketplace;
files go to a temporary data directory and Redis is treated as unavailable, so
the real data/ folder and a local Redis are never touched. Route protection
(401 without a session) is covered by test_admin_routes.py.
"""
from __future__ import annotations

import json
from datetime import datetime
from decimal import Decimal
from io import BytesIO
from unittest.mock import AsyncMock, patch

import pytest
import pytest_asyncio
from starlette.datastructures import UploadFile

from admin.resources.jobs import JobsRouter
from admin.resources.links import LinksRouter
from admin.resources.monitors import MonitorsRouter
from admin.resources.products import ProductsRouter
from admin.resources.proxies import ProxiesRouter
from admin.resources.targets import TargetsRouter
from admin.resources.users import UsersRouter
from models.product import ProductCard

WB = "https://www.wildberries.ru/catalog/{}/detail.aspx"
_CFG = {"monitoring": {"default_interval_seconds": 3600, "min_interval_seconds": 300,
                       "drop_percent": 5.0, "rise_percent": 5.0}}


class FakeRequest:
    def __init__(self, body: object = None, query: dict | None = None, form: dict | None = None) -> None:
        self._body = {} if body is None else body
        self.query_params = query or {}
        self._form = form or {}

    async def json(self) -> object:
        return self._body

    async def form(self) -> dict:
        return self._form


def _payload(resp) -> tuple[int, object]:
    try:
        return resp.status_code, json.loads(bytes(resp.body))
    except ValueError:
        return resp.status_code, bytes(resp.body)


async def _content(resp) -> bytes:
    """Body of a regular or streaming response."""
    if hasattr(resp, "body_iterator"):
        chunks = [c if isinstance(c, bytes) else c.encode() async for c in resp.body_iterator]
        return b"".join(chunks)
    return bytes(resp.body)


def _card(url: str = WB.format(1), price: str = "90") -> ProductCard:
    return ProductCard(title="Товар", sku="1", original_price=Decimal("100"), current_price=Decimal(price),
                       images=[], source_url=url)


@pytest_asyncio.fixture
async def db(tmp_path):
    import models.api_job  # noqa: F401 — register tables
    import models.api_key  # noqa: F401
    import models.parse_target  # noqa: F401
    import models.parsed_product  # noqa: F401
    import models.price_snapshot  # noqa: F401
    from models import job as job_mod

    saved_engine, saved_session = job_mod.engine, job_mod.async_session
    await job_mod.init_db(f"sqlite:///{tmp_path}/admin.db")
    try:
        with patch.dict("config.settings", _CFG, clear=False):
            yield job_mod.async_session
    finally:
        from models.job import close_db
        await close_db()
        job_mod.async_session = saved_session
        job_mod.engine = saved_engine


async def _add(db, *objs) -> None:
    async with db() as s:
        for o in objs:
            s.add(o)
        await s.commit()


async def _count(db, model) -> int:
    from sqlalchemy import func, select
    async with db() as s:
        return (await s.execute(select(func.count()).select_from(model))).scalar_one()


def _target(id_: str = "t1", url: str = WB.format(1), **kw):
    from models.parse_target import ParseTarget
    return ParseTarget(id=id_, url=url, marketplace="wildberries", **kw)


def _product(id_: str = "p1", url: str = WB.format(1)):
    from models.parsed_product import ParsedProduct
    return ParsedProduct(id=id_, url=url, marketplace="wildberries", title="Товар", sku="1",
                         original_price=100.0, current_price=90.0, images="[]", characteristics="{}")


def _job(id_: str = "j1", status: str = "success"):
    from models.job import Job
    return Job(id=id_, url=WB.format(1), marketplace="wildberries", status=status, started_at=datetime.utcnow())


# ---------------------------------------------------------------------------
# Targets
# ---------------------------------------------------------------------------

class TestTargets:
    async def test_add(self, db):
        status, body = _payload(await TargetsRouter.add_target(FakeRequest({"url": WB.format(5), "name": "A"})))
        assert status == 201 or status == 200, body
        _, listed = _payload(await TargetsRouter.list_targets(FakeRequest()))
        assert [t["url"] for t in listed["targets"]] == [WB.format(5)]

    @pytest.mark.parametrize("body", [{}, {"url": ""}, {"url": "https://example.com/item"}])
    async def test_add_rejects_bad_url(self, db, body):
        status, _ = _payload(await TargetsRouter.add_target(FakeRequest(body)))
        assert status == 422

    async def test_import_bulk(self, db):
        status, body = _payload(await TargetsRouter.import_bulk(
            FakeRequest({"urls": [WB.format(1), WB.format(2), "https://example.com/x"]})))
        assert status == 200 and body["added"] == 2 and len(body["errors"]) == 1

    async def test_import_bulk_requires_urls(self, db):
        status, _ = _payload(await TargetsRouter.import_bulk(FakeRequest({"urls": []})))
        assert status == 422

    async def test_parse_file(self, db):
        upload = UploadFile(BytesIO(f"{WB.format(1)}\n{WB.format(2)}\n".encode()), filename="links.txt")
        status, body = _payload(await TargetsRouter.parse_file(FakeRequest(form={"file": upload})))
        assert status == 200, body

    async def test_parse_file_without_file(self, db):
        status, _ = _payload(await TargetsRouter.parse_file(FakeRequest(form={})))
        assert status == 422

    async def test_delete_toggle_and_bulk(self, db):
        from models.parse_target import ParseTarget
        await _add(db, _target("t1"), _target("t2", WB.format(2)), _target("t3", WB.format(3)))

        assert _payload(await TargetsRouter.toggle_target(FakeRequest({"id": "t1"})))[0] == 200
        status, body = _payload(await TargetsRouter.bulk_toggle(FakeRequest({"ids": ["t1", "t2"], "enabled": False})))
        assert status == 200 and body["updated"] == 2
        assert _payload(await TargetsRouter.delete_target(FakeRequest({"id": "t3"})))[0] == 200
        status, body = _payload(await TargetsRouter.delete_many(FakeRequest({"ids": ["t1"]})))
        assert status == 200 and body["deleted"] == 1
        assert await _count(db, ParseTarget) == 1

        _, body = _payload(await TargetsRouter.clear_all_targets(FakeRequest()))
        assert body["deleted"] == 1 and await _count(db, ParseTarget) == 0

    @pytest.mark.parametrize("handler", ["delete_target", "toggle_target", "run_target"])
    async def test_id_required_and_unknown_id(self, db, handler):
        method = getattr(TargetsRouter, handler)
        assert _payload(await method(FakeRequest({})))[0] == 422
        assert _payload(await method(FakeRequest({"id": "missing"})))[0] == 404

    @pytest.mark.parametrize("handler", ["bulk_toggle", "delete_many", "run_selected"])
    async def test_ids_required(self, db, handler):
        status, _ = _payload(await getattr(TargetsRouter, handler)(FakeRequest({"ids": [], "enabled": True})))
        assert status == 422

    async def test_run_target_uses_parser(self, db):
        await _add(db, _target("t1"))
        with patch("services.parsing.ParsingService.parse_product", new=AsyncMock(return_value=_card())) as parse:
            status, body = _payload(await TargetsRouter.run_target(FakeRequest({"id": "t1"})))
        assert status == 200, body
        parse.assert_awaited_once()
        _, products = _payload(await ProductsRouter.list_products(FakeRequest()))
        assert [p["url"] for p in products["products"]] == [WB.format(1)]

    @pytest.mark.parametrize(("handler", "body"), [("run_selected", {"ids": ["t1"]}), ("run_all_targets", {})])
    async def test_background_runs_start_a_session(self, db, handler, body):
        await _add(db, _target("t1", enabled=True))
        with patch("admin.resources.targets._run_all_background", new=AsyncMock()) as run:
            status, payload = _payload(await getattr(TargetsRouter, handler)(FakeRequest(body)))
        assert status == 200 and payload["session_id"], payload
        run.assert_called_once()


# ---------------------------------------------------------------------------
# Products
# ---------------------------------------------------------------------------

class TestProducts:
    async def test_delete_and_clear(self, db):
        from models.parsed_product import ParsedProduct
        await _add(db, _product("p1"), _product("p2", WB.format(2)), _product("p3", WB.format(3)))
        assert _payload(await ProductsRouter.delete_product(FakeRequest({"id": "p1"})))[0] == 200
        status, body = _payload(await ProductsRouter.delete_many(FakeRequest({"ids": ["p2"]})))
        assert status == 200 and body["deleted"] == 1
        assert _payload(await ProductsRouter.clear_products(FakeRequest()))[0] == 200
        assert await _count(db, ParsedProduct) == 0

    async def test_delete_validation(self, db):
        assert _payload(await ProductsRouter.delete_product(FakeRequest({})))[0] == 422
        assert _payload(await ProductsRouter.delete_product(FakeRequest({"id": "missing"})))[0] == 404
        assert _payload(await ProductsRouter.delete_many(FakeRequest({"ids": []})))[0] == 422

    async def test_refetch(self, db):
        await _add(db, _product("p1"))
        with patch("admin.resources.products._refetch_background", new=AsyncMock()) as refetch:
            status, _ = _payload(await ProductsRouter.refetch_products(FakeRequest({"ids": ["p1"]})))
        assert status == 200
        refetch.assert_called_once()
        assert _payload(await ProductsRouter.refetch_products(FakeRequest({"ids": []})))[0] == 422
        assert _payload(await ProductsRouter.refetch_products(FakeRequest({"ids": ["missing"]})))[0] == 404

    @pytest.mark.parametrize("fmt", ["csv", "xlsx", "json"])
    async def test_export(self, db, fmt):
        await _add(db, _product("p1"))
        resp = await ProductsRouter.export_products(FakeRequest({"format": fmt}))
        assert resp.status_code == 200 and len(await _content(resp)) > 0


# ---------------------------------------------------------------------------
# Jobs history
# ---------------------------------------------------------------------------

class TestJobs:
    async def test_delete_and_clear(self, db):
        from models.job import Job
        await _add(db, _job("j1"), _job("j2"))
        assert _payload(await JobsRouter.delete_job(FakeRequest({"id": "j1"})))[0] == 200
        assert _payload(await JobsRouter.clear_all(FakeRequest()))[0] == 200
        assert await _count(db, Job) == 0

    async def test_delete_validation(self, db):
        assert _payload(await JobsRouter.delete_job(FakeRequest({})))[0] == 422
        assert _payload(await JobsRouter.delete_job(FakeRequest({"id": "missing"})))[0] == 404

    @pytest.mark.parametrize("fmt", ["csv", "xlsx", "json"])
    async def test_export(self, db, fmt):
        await _add(db, _job("j1"))
        resp = await JobsRouter.export_jobs(FakeRequest({"format": fmt}))
        assert resp.status_code == 200 and len(await _content(resp)) > 0


# ---------------------------------------------------------------------------
# Link extraction
# ---------------------------------------------------------------------------

class TestLinks:
    async def test_extract_uses_crawler(self):
        links = [WB.format(1), WB.format(2)]
        with patch("crawlers.wildberries.WildberriesCrawler.get_product_links", new=AsyncMock(return_value=links)):
            status, body = _payload(await LinksRouter.extract_links(
                FakeRequest({"url": "https://www.wildberries.ru/seller/1"})))
        assert status == 200 and body["ok"] is True and body["links"] == links, body

    @pytest.mark.parametrize("body", [{}, {"url": "https://example.com/catalog"}])
    async def test_extract_rejects_bad_url(self, body):
        assert _payload(await LinksRouter.extract_links(FakeRequest(body)))[0] == 422

    @pytest.mark.parametrize("handler", ["download_excel", "download_csv"])
    async def test_downloads(self, handler):
        resp = await getattr(LinksRouter, handler)(FakeRequest({"links": [WB.format(1)], "marketplace": "wb"}))
        assert resp.status_code == 200 and len(await _content(resp)) > 0


# ---------------------------------------------------------------------------
# Monitoring
# ---------------------------------------------------------------------------

class TestMonitors:
    async def test_toggle_and_bulk(self, db):
        await _add(db, _target("t1"), _target("t2", WB.format(2)))
        status, _ = _payload(await MonitorsRouter.toggle_monitor(
            FakeRequest({"id": "t1", "monitor_enabled": True, "interval_seconds": 600})))
        assert status == 200
        status, body = _payload(await MonitorsRouter.bulk(
            FakeRequest({"ids": ["t1", "t2"], "monitor_enabled": True})))
        assert status == 200, body
        _, listed = _payload(await MonitorsRouter.list_monitors(FakeRequest()))
        assert all(m["monitor_enabled"] for m in listed["monitors"])

    async def test_validation(self, db):
        await _add(db, _target("t1"))
        assert _payload(await MonitorsRouter.toggle_monitor(FakeRequest({})))[0] == 422
        assert _payload(await MonitorsRouter.toggle_monitor(FakeRequest({"id": "missing"})))[0] == 404
        assert _payload(await MonitorsRouter.toggle_monitor(
            FakeRequest({"id": "t1", "interval_seconds": 10})))[0] == 422
        assert _payload(await MonitorsRouter.bulk(FakeRequest({"ids": []})))[0] == 422

    async def test_run_now(self, db):
        summary = {"checked": 1, "ok": 1, "failed": 0}
        with patch("services.monitoring.run_due_once", new=AsyncMock(return_value=summary)):
            status, body = _payload(await MonitorsRouter.run_now(FakeRequest()))
        assert status == 200 and body["checked"] == 1


# ---------------------------------------------------------------------------
# Settings: users and proxies (temporary data dir, Redis unavailable)
# ---------------------------------------------------------------------------

@pytest.fixture
def data_dir(tmp_path):
    (tmp_path / "settings.yml").write_text(
        'admin:\n  username: "admin"\n  password: "old"\nproxy:\n  active: false\n  type: "http"\n',
        encoding="utf-8",
    )

    async def no_redis(*args, **kwargs):
        raise ConnectionError("Redis is not available in tests")

    with patch("config.data_path", str(tmp_path)), \
            patch("admin.resources.proxies._SETTINGS_FILE", str(tmp_path / "settings.yml")), \
            patch("admin.resources.proxies._PROXY_FILE", str(tmp_path / "proxies.json")), \
            patch("services.redis_client.get_json", new=no_redis), \
            patch("services.redis_client.set_json", new=no_redis), \
            patch.dict("config.settings", {"admin": {"username": "admin", "password": "old"},
                                           "proxy": {"active": False, "type": "http"}}):
        yield tmp_path


class TestUsers:
    async def test_update_writes_settings_file(self, data_dir):
        import yaml
        status, _ = _payload(await UsersRouter.update_user(FakeRequest({"username": "boss", "password": "new"})))
        assert status == 200
        saved = yaml.safe_load((data_dir / "settings.yml").read_text(encoding="utf-8"))
        assert saved["admin"] == {"username": "boss", "password": "new"}

    async def test_username_required(self, data_dir):
        assert _payload(await UsersRouter.update_user(FakeRequest({"username": ""})))[0] == 422


class TestProxies:
    PROXY = "user:pass@1.2.3.4:8080"

    async def test_add_list_remove(self, data_dir):
        assert _payload(await ProxiesRouter.add_proxy(FakeRequest({"proxy": self.PROXY})))[0] == 200
        assert _payload(await ProxiesRouter.add_proxy(FakeRequest({"proxy": "user:pass@5.6.7.8:8080"})))[0] == 200
        _, listed = _payload(await ProxiesRouter.list_proxies(FakeRequest()))
        assert listed["total"] == 2
        assert _payload(await ProxiesRouter.remove_proxy(FakeRequest({"index": 0})))[0] == 200
        assert _payload(await ProxiesRouter.remove_many(FakeRequest({"indexes": [0]})))[0] == 200
        assert json.loads((data_dir / "proxies.json").read_text(encoding="utf-8")) == []

    async def test_validation(self, data_dir):
        assert _payload(await ProxiesRouter.add_proxy(FakeRequest({"proxy": ""})))[0] == 422
        assert _payload(await ProxiesRouter.add_proxy(FakeRequest({"proxy": "not a proxy"})))[0] == 422
        assert _payload(await ProxiesRouter.remove_proxy(FakeRequest({})))[0] == 422
        assert _payload(await ProxiesRouter.remove_proxy(FakeRequest({"index": 5})))[0] == 422
        assert _payload(await ProxiesRouter.remove_many(FakeRequest({"indexes": []})))[0] == 422
        assert _payload(await ProxiesRouter.remove_many(FakeRequest({"indexes": ["x"]})))[0] == 422

    async def test_reload_from_file(self, data_dir):
        status, body = _payload(await ProxiesRouter.reload_from_file(
            FakeRequest({"proxies": f"{self.PROXY}\nuser:pass@5.6.7.8:8080\n"})))
        assert status == 200 and body["reloaded"] == 2
        assert _payload(await ProxiesRouter.reload_from_file(FakeRequest({"proxies": ""})))[0] == 422
        assert _payload(await ProxiesRouter.reload_from_file(FakeRequest({"proxies": "bad line"})))[0] == 422

    async def test_settings(self, data_dir):
        import yaml
        status, _ = _payload(await ProxiesRouter.update_proxy_settings(FakeRequest({"type": "socks5", "active": True})))
        assert status == 200
        saved = yaml.safe_load((data_dir / "settings.yml").read_text(encoding="utf-8"))
        assert saved["proxy"] == {"active": True, "type": "socks5"}
        assert _payload(await ProxiesRouter.update_proxy_settings(FakeRequest({"type": "ftp"})))[0] == 422
        assert _payload(await ProxiesRouter.update_proxy_settings(FakeRequest({"active": "yes"})))[0] == 422
