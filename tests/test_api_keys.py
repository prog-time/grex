"""Tests for API key storage (services/api_keys.py) and the admin endpoints
(admin/resources/api_keys.py).

DB-touching tests run against a per-test temporary SQLite database via the
``db`` fixture, which restores the module-global engine/session afterwards so it
can't leak into other test modules.
"""
from __future__ import annotations

import json
from pathlib import Path

import pytest
import pytest_asyncio
import yaml
from sqlalchemy import select

from admin.resources.api_keys import ApiKeysRouter
from services import api_keys


class FakeRequest:
    """Minimal stand-in exposing the async .json() the handlers call."""

    def __init__(self, body: dict | None = None) -> None:
        self._body = body or {}

    async def json(self) -> dict:
        return self._body


def _payload(resp):
    return resp.status_code, json.loads(bytes(resp.body))


@pytest_asyncio.fixture
async def db(tmp_path):
    import models.api_key  # noqa: F401 — register the table
    from models import job as job_mod

    saved_engine, saved_session = job_mod.engine, job_mod.async_session
    await job_mod.init_db(f"sqlite:///{tmp_path}/keys.db")
    try:
        yield job_mod.async_session
    finally:
        from models.job import close_db
        await close_db()
        job_mod.async_session = saved_session
        job_mod.engine = saved_engine


async def _row(session_factory, key_id):
    from models.api_key import ApiKey

    async with session_factory() as session:
        return (await session.execute(select(ApiKey).where(ApiKey.id == key_id))).scalar_one()


# ---------------------------------------------------------------------------
# Service
# ---------------------------------------------------------------------------

class TestGenerateKey:
    def test_has_prefix_and_is_unique(self):
        a, b = api_keys.generate_key(), api_keys.generate_key()
        assert a.startswith("grx_") and b.startswith("grx_")
        assert a != b
        assert len(a) > 40


class TestVerifyKey:
    async def test_raises_without_db(self):
        from models import job as job_mod

        saved = job_mod.async_session
        job_mod.async_session = None
        try:
            with pytest.raises(api_keys.KeyStorageUnavailable):
                await api_keys.verify_key("anything")
        finally:
            job_mod.async_session = saved

    async def test_created_key_is_valid(self, db):
        plain, data = await api_keys.create_key("site")
        assert await api_keys.verify_key(plain) == data["id"]

    async def test_unknown_key_is_invalid(self, db):
        await api_keys.create_key("site")
        assert await api_keys.verify_key("grx_unknown") is None

    async def test_plaintext_is_not_stored(self, db):
        plain, data = await api_keys.create_key("site")
        row = await _row(db, data["id"])
        assert row.key_hash == api_keys.hash_key(plain)
        assert plain not in (row.key_hash, row.prefix)

    async def test_sets_last_used_at(self, db):
        plain, data = await api_keys.create_key("site")
        assert data["last_used_at"] is None
        await api_keys.verify_key(plain)
        assert (await _row(db, data["id"])).last_used_at is not None

    async def test_last_used_at_is_throttled(self, db):
        plain, data = await api_keys.create_key("site")
        await api_keys.verify_key(plain)
        first = (await _row(db, data["id"])).last_used_at
        await api_keys.verify_key(plain)
        assert (await _row(db, data["id"])).last_used_at == first

    async def test_revoked_key_is_invalid(self, db):
        plain, data = await api_keys.create_key("site")
        await ApiKeysRouter.revoke_key(FakeRequest({"id": data["id"]}))
        assert await api_keys.verify_key(plain) is None


class TestImportLegacyKey:
    async def test_imports_settings_key_once(self, db):
        await api_keys.import_legacy_key("legacy-secret")
        await api_keys.import_legacy_key("legacy-secret")
        _, body = _payload(await ApiKeysRouter.list_keys(FakeRequest()))
        assert body["total"] == 1
        assert body["keys"][0]["name"] == api_keys.LEGACY_KEY_NAME
        assert await api_keys.verify_key("legacy-secret") == body["keys"][0]["id"]

    async def test_revoked_legacy_key_is_not_reimported(self, db):
        await api_keys.import_legacy_key("legacy-secret")
        _, body = _payload(await ApiKeysRouter.list_keys(FakeRequest()))
        await ApiKeysRouter.revoke_key(FakeRequest({"id": body["keys"][0]["id"]}))

        await api_keys.import_legacy_key("legacy-secret")
        assert await api_keys.verify_key("legacy-secret") is None

    async def test_empty_value_is_ignored(self, db):
        await api_keys.import_legacy_key("")
        await api_keys.import_legacy_key(None)
        _, body = _payload(await ApiKeysRouter.list_keys(FakeRequest()))
        assert body["total"] == 0

    async def test_settings_example_seeds_no_key(self, db):
        # A fresh install copies settings.yml.example; it must not create a
        # working key that everyone knows from the public repository.
        example = Path(__file__).resolve().parent.parent / "data" / "settings.yml.example"
        seed = yaml.safe_load(example.read_text(encoding="utf-8"))["security"]["api_key"]
        assert not seed

        await api_keys.import_legacy_key(seed)
        _, body = _payload(await ApiKeysRouter.list_keys(FakeRequest()))
        assert body["total"] == 0


# ---------------------------------------------------------------------------
# Admin endpoints
# ---------------------------------------------------------------------------

class TestEndpoints:
    async def test_create_returns_plaintext_once(self, db):
        status, body = _payload(await ApiKeysRouter.create_key(FakeRequest({"name": "  1C  "})))
        assert status == 201
        assert body["key"].startswith("grx_")
        assert body["api_key"]["name"] == "1C"

        _, listed = _payload(await ApiKeysRouter.list_keys(FakeRequest()))
        assert body["key"] not in json.dumps(listed)
        assert listed["keys"][0]["prefix"] == body["key"][:8]

    async def test_create_requires_name(self, db):
        status, body = _payload(await ApiKeysRouter.create_key(FakeRequest({"name": "  "})))
        assert status == 422 and "error" in body

    async def test_create_rejects_long_name(self, db):
        status, _ = _payload(await ApiKeysRouter.create_key(FakeRequest({"name": "x" * 101})))
        assert status == 422

    async def test_list_flags_default_key_as_insecure(self, db):
        await api_keys.import_legacy_key(api_keys.DEFAULT_LEGACY_KEY)
        await api_keys.create_key("site")
        _, body = _payload(await ApiKeysRouter.list_keys(FakeRequest()))
        flags = {k["name"]: k["insecure"] for k in body["keys"]}
        assert flags == {api_keys.LEGACY_KEY_NAME: True, "site": False}

    async def test_revoke_missing_returns_404(self, db):
        status, _ = _payload(await ApiKeysRouter.revoke_key(FakeRequest({"id": "nope"})))
        assert status == 404

    async def test_revoke_is_idempotent(self, db):
        _, data = await api_keys.create_key("site")
        _, first = _payload(await ApiKeysRouter.revoke_key(FakeRequest({"id": data["id"]})))
        _, second = _payload(await ApiKeysRouter.revoke_key(FakeRequest({"id": data["id"]})))
        assert first["api_key"]["revoked_at"] == second["api_key"]["revoked_at"] is not None

    async def test_delete_active_key_is_refused(self, db):
        _, data = await api_keys.create_key("site")
        status, _ = _payload(await ApiKeysRouter.delete_key(FakeRequest({"id": data["id"]})))
        assert status == 409

    async def test_delete_revoked_key(self, db):
        _, data = await api_keys.create_key("site")
        await ApiKeysRouter.revoke_key(FakeRequest({"id": data["id"]}))
        status, _ = _payload(await ApiKeysRouter.delete_key(FakeRequest({"id": data["id"]})))
        assert status == 200
        _, body = _payload(await ApiKeysRouter.list_keys(FakeRequest()))
        assert body["total"] == 0

    async def test_endpoints_return_503_without_db(self):
        from models import job as job_mod

        saved = job_mod.async_session
        job_mod.async_session = None
        try:
            status, _ = _payload(await ApiKeysRouter.list_keys(FakeRequest()))
            assert status == 503
            status, _ = _payload(await ApiKeysRouter.create_key(FakeRequest({"name": "x"})))
            assert status == 503
        finally:
            job_mod.async_session = saved

