"""Tests for the monitoring alert-sources scaffold (admin/resources/alert_sources.py).

Covers the pure recipient validation and the list/create/delete endpoints. The
DB-touching endpoints run against a per-test temporary SQLite database via the
``db`` fixture, which restores the module-global engine/session afterwards so it
can't leak into other test modules.
"""
from __future__ import annotations

import json

import pytest
import pytest_asyncio

from admin.resources.alert_sources import AlertSourcesRouter, _validate_recipient


class FakeRequest:
    """Minimal stand-in exposing the async .json() the handlers call."""

    def __init__(self, body: dict | None = None) -> None:
        self._body = body or {}

    async def json(self) -> dict:
        return self._body


def _payload(resp):
    return resp.status_code, json.loads(bytes(resp.body))


# ---------------------------------------------------------------------------
# Pure recipient validation
# ---------------------------------------------------------------------------

class TestValidateRecipient:
    @pytest.mark.parametrize("value", ["a@b.co", "user.name@example.com", "x@y.io"])
    def test_valid_email(self, value):
        assert _validate_recipient("mail", value) is None

    @pytest.mark.parametrize("value", ["", "notanemail", "a@b", "a@@b.co", "no spaces@x.co"])
    def test_invalid_email(self, value):
        assert _validate_recipient("mail", value) is not None

    @pytest.mark.parametrize("value", ["@username", "username", "123456789", "-1001234567", "user_name_1"])
    def test_valid_telegram(self, value):
        assert _validate_recipient("telegram", value) is None

    @pytest.mark.parametrize("value", ["", "ab", "with space", "two@words"])
    def test_invalid_telegram(self, value):
        assert _validate_recipient("telegram", value) is not None


# ---------------------------------------------------------------------------
# Endpoints
# ---------------------------------------------------------------------------

@pytest_asyncio.fixture
async def db(tmp_path):
    import models.alert_source  # noqa: F401 — register the table
    from models import job as job_mod

    saved_engine, saved_session = job_mod.engine, job_mod.async_session
    await job_mod.init_db(f"sqlite:///{tmp_path}/alerts.db")
    try:
        yield job_mod.async_session
    finally:
        from models.job import close_db
        await close_db()
        job_mod.async_session = saved_session
        job_mod.engine = saved_engine


class TestEndpoints:
    async def test_create_then_list(self, db):
        status, body = _payload(await AlertSourcesRouter.create_source(
            FakeRequest({"type": "telegram", "recipient": "@buyer", "frequency": "daily"})
        ))
        assert status == 200 and body["ok"] is True
        assert body["source"]["type"] == "telegram"

        status, body = _payload(await AlertSourcesRouter.list_sources(FakeRequest()))
        assert status == 200
        assert body["total"] == 1
        assert body["sources"][0]["recipient"] == "@buyer"
        # the fixed frequency list is exposed for the UI dropdown
        assert "daily" in body["frequencies"]

    async def test_create_mail(self, db):
        status, body = _payload(await AlertSourcesRouter.create_source(
            FakeRequest({"type": "mail", "recipient": "boss@example.com", "frequency": "weekly"})
        ))
        assert status == 200 and body["source"]["type"] == "mail"

    async def test_rejects_bad_email(self, db):
        status, body = _payload(await AlertSourcesRouter.create_source(
            FakeRequest({"type": "mail", "recipient": "not-an-email", "frequency": "daily"})
        ))
        assert status == 422 and "error" in body

    async def test_rejects_bad_frequency(self, db):
        status, body = _payload(await AlertSourcesRouter.create_source(
            FakeRequest({"type": "telegram", "recipient": "@buyer", "frequency": "every_minute"})
        ))
        assert status == 422

    async def test_rejects_bad_type(self, db):
        status, body = _payload(await AlertSourcesRouter.create_source(
            FakeRequest({"type": "sms", "recipient": "+700000", "frequency": "daily"})
        ))
        assert status == 422

    async def test_delete(self, db):
        _, created = _payload(await AlertSourcesRouter.create_source(
            FakeRequest({"type": "mail", "recipient": "a@b.co", "frequency": "hourly"})
        ))
        sid = created["source"]["id"]

        status, body = _payload(await AlertSourcesRouter.delete_source(FakeRequest({"id": sid})))
        assert status == 200 and body["deleted"] == sid

        _, body = _payload(await AlertSourcesRouter.list_sources(FakeRequest()))
        assert body["total"] == 0

    async def test_delete_missing_returns_404(self, db):
        status, _ = _payload(await AlertSourcesRouter.delete_source(FakeRequest({"id": "nope"})))
        assert status == 404
