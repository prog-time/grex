"""Tests for async parse jobs (services/api_jobs.py) and the /v1 API surface.

Service tests run against a per-test temporary SQLite database via the ``db``
fixture and a fake parsing service, so no crawler is ever invoked. HTTP tests
use the TestClient (no lifespan → no DB) with the job service patched.
"""
from __future__ import annotations

import asyncio
from unittest.mock import AsyncMock, patch

import pytest
import pytest_asyncio
from fastapi.testclient import TestClient

import api
from models.product import BatchItemResult
from services import api_jobs

_KEY = "key-a"
_OTHER_KEY = "key-b"
_WB = "https://www.wildberries.ru/catalog/{}/detail.aspx"


class FakeParsingService:
    """Reports a result per URL through on_result, like the real service.

    URLs containing "fail" produce a failed item. If *gate* is set, parsing
    waits on it — lets tests observe a job while it is still running.
    """

    def __init__(self, gate: asyncio.Event | None = None, crash: bool = False) -> None:
        self.gate = gate
        self.crash = crash

    async def parse_products_batch(self, urls, concurrency=None, on_result=None):
        if self.gate is not None:
            await self.gate.wait()
        if self.crash:
            raise RuntimeError("boom")
        results = []
        # Finish in reverse order to check results are returned in request order.
        for i in reversed(range(len(urls))):
            ok = "fail" not in urls[i]
            r = BatchItemResult(
                url=urls[i], success=ok, marketplace="wildberries",
                data={"title": f"item {i}"} if ok else None,
                error=None if ok else "parse error", processing_time=0.1,
            )
            await on_result(i, r)
            results.append(r)
        return results


async def _wait_finished(job_id: str, key: str = _KEY) -> dict:
    for _ in range(200):
        job = await api_jobs.get_job(key, job_id)
        if job["status"] in ("completed", "failed"):
            return job
        await asyncio.sleep(0.01)
    raise AssertionError("job did not finish")


@pytest_asyncio.fixture
async def db(tmp_path):
    import models.api_job  # noqa: F401 — register the tables
    from models import job as job_mod

    saved_engine, saved_session = job_mod.engine, job_mod.async_session
    await job_mod.init_db(f"sqlite:///{tmp_path}/jobs.db")
    try:
        yield job_mod.async_session
    finally:
        # Stop leftover background jobs and let them unwind before the DB
        # closes — otherwise a job cancelled mid-write leaks its connection.
        tasks = list(api_jobs._running)
        for task in tasks:
            task.cancel()
        await asyncio.gather(*tasks, return_exceptions=True)
        from models.job import close_db
        await close_db()
        job_mod.async_session = saved_session
        job_mod.engine = saved_engine


# ---------------------------------------------------------------------------
# Service
# ---------------------------------------------------------------------------

class TestJobService:
    async def test_job_completes_with_ordered_results(self, db):
        urls = [_WB.format(1), _WB.format("fail"), _WB.format(3)]
        created = await api_jobs.create_job(_KEY, urls, None, FakeParsingService())
        assert created["status"] == "pending" and created["total"] == 3

        job = await _wait_finished(created["id"])
        assert job["status"] == "completed"
        assert (job["done"], job["succeeded"], job["failed"]) == (3, 2, 1)
        assert [r["url"] for r in job["results"]] == urls
        assert job["results"][0]["data"] == {"title": "item 0"}
        assert job["results"][1]["success"] is False and job["results"][1]["error"] == "parse error"
        assert job["started_at"] and job["finished_at"]

    async def test_progress_is_visible_while_running(self, db):
        gate = asyncio.Event()
        created = await api_jobs.create_job(_KEY, [_WB.format(1)], None, FakeParsingService(gate))
        await asyncio.sleep(0.05)
        job = await api_jobs.get_job(_KEY, created["id"])
        assert job["status"] == "running" and job["done"] == 0 and job["results"] == []
        gate.set()
        assert (await _wait_finished(created["id"]))["done"] == 1

    async def test_job_is_private_to_its_key(self, db):
        created = await api_jobs.create_job(_KEY, [_WB.format(1)], None, FakeParsingService())
        await _wait_finished(created["id"])
        assert await api_jobs.get_job(_OTHER_KEY, created["id"]) is None
        assert await api_jobs.list_jobs(_OTHER_KEY, 20) == []

    async def test_list_jobs_newest_first_without_results(self, db):
        first = await api_jobs.create_job(_KEY, [_WB.format(1)], None, FakeParsingService())
        await _wait_finished(first["id"])
        second = await api_jobs.create_job(_KEY, [_WB.format(2)], None, FakeParsingService())
        await _wait_finished(second["id"])

        jobs = await api_jobs.list_jobs(_KEY, 20)
        assert [j["id"] for j in jobs] == [second["id"], first["id"]]
        assert "results" not in jobs[0]
        assert len(await api_jobs.list_jobs(_KEY, 1)) == 1

    async def test_crash_marks_job_failed(self, db):
        created = await api_jobs.create_job(_KEY, [_WB.format(1)], None, FakeParsingService(crash=True))
        job = await _wait_finished(created["id"])
        assert job["status"] == "failed" and job["error"] == "boom"

    async def test_active_job_limit_per_key(self, db):
        gate = asyncio.Event()
        for i in range(api_jobs.MAX_ACTIVE_JOBS_PER_KEY):
            await api_jobs.create_job(_KEY, [_WB.format(i)], None, FakeParsingService(gate))
        with pytest.raises(api_jobs.TooManyActiveJobs):
            await api_jobs.create_job(_KEY, [_WB.format(99)], None, FakeParsingService(gate))
        # The limit is per key.
        await api_jobs.create_job(_OTHER_KEY, [_WB.format(1)], None, FakeParsingService(gate))
        gate.set()

    async def test_active_job_limit_holds_for_concurrent_requests(self, db):
        gate = asyncio.Event()
        results = await asyncio.gather(
            *[api_jobs.create_job(_KEY, [_WB.format(i)], None, FakeParsingService(gate)) for i in range(20)],
            return_exceptions=True,
        )
        created = [r for r in results if not isinstance(r, Exception)]
        rejected = [r for r in results if isinstance(r, api_jobs.TooManyActiveJobs)]
        assert len(created) == api_jobs.MAX_ACTIVE_JOBS_PER_KEY
        assert len(rejected) == 20 - api_jobs.MAX_ACTIVE_JOBS_PER_KEY
        gate.set()

    async def test_cancel_jobs_for_key_stops_running_jobs(self, db):
        gate = asyncio.Event()  # never set: the jobs would run forever
        mine = await api_jobs.create_job(_KEY, [_WB.format(1)], None, FakeParsingService(gate))
        other = await api_jobs.create_job(_OTHER_KEY, [_WB.format(2)], None, FakeParsingService(gate))
        await asyncio.sleep(0.05)
        assert (await api_jobs.get_job(_KEY, mine["id"]))["status"] == "running"

        assert await api_jobs.cancel_jobs_for_key(_KEY) == 1

        job = await api_jobs.get_job(_KEY, mine["id"])
        assert job["status"] == "failed" and job["error"] == api_jobs.REVOKED_ERROR
        assert job["finished_at"] is not None
        # Another key's job is untouched.
        assert (await api_jobs.get_job(_OTHER_KEY, other["id"]))["status"] == "running"
        gate.set()

    async def test_reset_stale_jobs(self, db):
        gate = asyncio.Event()
        created = await api_jobs.create_job(_KEY, [_WB.format(1)], None, FakeParsingService(gate))
        await asyncio.sleep(0.05)
        await api_jobs.reset_stale_jobs()
        job = await api_jobs.get_job(_KEY, created["id"])
        assert job["status"] == "failed" and job["error"] == "Interrupted by server restart"


# ---------------------------------------------------------------------------
# HTTP
# ---------------------------------------------------------------------------

_API_KEY = "grx_test-key"
_HEADERS = {"X-API-Key": _API_KEY}
_JOB = {
    "id": "job-1", "status": "pending", "total": 1, "done": 0, "succeeded": 0, "failed": 0,
    "error": None, "created_at": "2026-10-06T10:00:00", "started_at": None, "finished_at": None,
}


@pytest.fixture()
def client():
    async def fake_verify(key: str):
        return _KEY if key == _API_KEY else None

    with patch("services.api_keys.verify_key", new=fake_verify):
        yield TestClient(api.app, raise_server_exceptions=False)


class TestVersioning:
    @pytest.mark.parametrize("path", ["/v1/health", "/v1/marketplaces", "/health", "/marketplaces"])
    def test_public_endpoints_served_with_and_without_prefix(self, client, path):
        assert client.get(path).status_code == 200

    def test_v1_parse_requires_api_key(self, client):
        assert client.post("/v1/parse", json={"url": _WB.format(1)}).status_code == 401

    def test_unversioned_paths_are_deprecated_in_openapi(self, client):
        paths = api.app.openapi()["paths"]
        assert paths["/parse"]["post"].get("deprecated") is True
        assert not paths["/v1/parse"]["post"].get("deprecated")

    def test_jobs_exist_only_under_v1(self, client):
        assert client.get("/jobs", headers=_HEADERS).status_code == 404


class TestJobsEndpoints:
    def test_create_returns_202(self, client):
        with patch("services.api_jobs.create_job", new=AsyncMock(return_value=_JOB)) as create:
            r = client.post("/v1/jobs", json={"urls": [_WB.format(1)], "concurrency": 3}, headers=_HEADERS)
        assert r.status_code == 202
        assert r.json()["id"] == "job-1"
        key_id, urls, concurrency, _ = create.call_args.args
        assert (key_id, urls, concurrency) == (_KEY, [_WB.format(1)], 3)

    def test_create_requires_api_key(self, client):
        assert client.post("/v1/jobs", json={"urls": [_WB.format(1)]}).status_code == 401

    def test_create_rejects_unsupported_url(self, client):
        r = client.post("/v1/jobs", json={"urls": ["https://amazon.com/dp/1"]}, headers=_HEADERS)
        assert r.status_code == 422

    def test_create_rejects_more_than_1000_urls(self, client):
        urls = [_WB.format(i) for i in range(1001)]
        assert client.post("/v1/jobs", json={"urls": urls}, headers=_HEADERS).status_code == 422

    def test_create_returns_429_over_active_limit(self, client):
        with patch("services.api_jobs.create_job", new=AsyncMock(side_effect=api_jobs.TooManyActiveJobs("limit"))):
            r = client.post("/v1/jobs", json={"urls": [_WB.format(1)]}, headers=_HEADERS)
        assert r.status_code == 429

    def test_get_job(self, client):
        job = {**_JOB, "status": "completed", "done": 1, "succeeded": 1, "results": [
            {"url": _WB.format(1), "success": True, "data": {"title": "x"}, "error": None,
             "marketplace": "wildberries", "processing_time": 0.1},
        ]}
        with patch("services.api_jobs.get_job", new=AsyncMock(return_value=job)) as get:
            r = client.get("/v1/jobs/job-1", headers=_HEADERS)
        assert r.status_code == 200
        assert r.json()["results"][0]["data"] == {"title": "x"}
        assert get.call_args.args == (_KEY, "job-1")

    def test_get_unknown_or_foreign_job_returns_404(self, client):
        with patch("services.api_jobs.get_job", new=AsyncMock(return_value=None)):
            assert client.get("/v1/jobs/nope", headers=_HEADERS).status_code == 404

    def test_list_jobs_omits_results(self, client):
        with patch("services.api_jobs.list_jobs", new=AsyncMock(return_value=[_JOB])) as lst:
            r = client.get("/v1/jobs?limit=5", headers=_HEADERS)
        assert r.status_code == 200
        assert r.json()["jobs"][0]["id"] == "job-1"
        assert "results" not in r.json()["jobs"][0]
        assert r.json()["jobs"][0]["error"] is None  # null fields are kept
        assert lst.call_args.args == (_KEY, 5)
