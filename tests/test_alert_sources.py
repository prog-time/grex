"""Tests for the monitoring alert-sources scaffold (admin/resources/alert_sources.py).

Covers the pure recipient validation and the list/create/delete endpoints. The
DB-touching endpoints run against a per-test temporary SQLite database via the
``db`` fixture, which restores the module-global engine/session afterwards so it
can't leak into other test modules.
"""
from __future__ import annotations

import json
from unittest.mock import AsyncMock, patch

import pytest
import pytest_asyncio

from admin.resources.alert_sources import AlertSourcesRouter, _validate_recipient


def _tg_ok(chat_id=12345):
    """Patch context: token set, recipient resolves, bot can send."""
    return (
        patch("admin.resources.alert_sources.get_telegram_token", return_value="T"),
        patch("services.telegram.resolve_chat_id", new=AsyncMock(return_value=(chat_id, None))),
        patch("services.telegram.can_send", new=AsyncMock(return_value=(True, None))),
    )


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
        p1, p2, p3 = _tg_ok(chat_id=12345)
        with p1, p2, p3:
            status, body = _payload(await AlertSourcesRouter.create_source(
                FakeRequest({"type": "telegram", "recipient": "@buyer", "frequency": "daily"})
            ))
        assert status == 200 and body["ok"] is True
        assert body["source"]["type"] == "telegram"
        assert body["source"]["chat_id"] == "12345"   # resolved chat id is stored

        status, body = _payload(await AlertSourcesRouter.list_sources(FakeRequest()))
        assert status == 200
        assert body["total"] == 1
        assert body["sources"][0]["recipient"] == "@buyer"
        # the fixed frequency list is exposed for the UI dropdown
        assert "daily" in body["frequencies"]

    async def test_telegram_unresolvable_recipient(self, db):
        with patch("admin.resources.alert_sources.get_telegram_token", return_value="T"), \
                patch("services.telegram.resolve_chat_id", new=AsyncMock(return_value=(None, "не найден"))):
            status, body = _payload(await AlertSourcesRouter.create_source(
                FakeRequest({"type": "telegram", "recipient": "@ghost", "frequency": "daily"})
            ))
        assert status == 422 and "error" in body

    async def test_telegram_cannot_send(self, db):
        with patch("admin.resources.alert_sources.get_telegram_token", return_value="T"), \
                patch("services.telegram.resolve_chat_id", new=AsyncMock(return_value=(777, None))), \
                patch("services.telegram.can_send", new=AsyncMock(return_value=(False, "bot was blocked"))):
            status, body = _payload(await AlertSourcesRouter.create_source(
                FakeRequest({"type": "telegram", "recipient": "@blocked", "frequency": "daily"})
            ))
        assert status == 422 and "error" in body

    async def test_update_mail_frequency(self, db):
        _, created = _payload(await AlertSourcesRouter.create_source(
            FakeRequest({"type": "mail", "recipient": "a@b.co", "frequency": "daily"})
        ))
        sid = created["source"]["id"]
        status, body = _payload(await AlertSourcesRouter.update_source(
            FakeRequest({"id": sid, "recipient": "a@b.co", "frequency": "weekly"})
        ))
        assert status == 200 and body["source"]["frequency"] == "weekly"

    async def test_update_missing_returns_404(self, db):
        status, _ = _payload(await AlertSourcesRouter.update_source(
            FakeRequest({"id": "nope", "recipient": "a@b.co", "frequency": "daily"})
        ))
        assert status == 404

    async def test_update_telegram_reresolves_on_recipient_change(self, db):
        p1, p2, p3 = _tg_ok(chat_id=111)
        with p1, p2, p3:
            _, created = _payload(await AlertSourcesRouter.create_source(
                FakeRequest({"type": "telegram", "recipient": "@oldname", "frequency": "daily"})
            ))
        sid = created["source"]["id"]
        assert created["source"]["chat_id"] == "111"

        with patch("admin.resources.alert_sources.get_telegram_token", return_value="T"), \
                patch("services.telegram.resolve_chat_id", new=AsyncMock(return_value=(222, None))), \
                patch("services.telegram.can_send", new=AsyncMock(return_value=(True, None))):
            status, body = _payload(await AlertSourcesRouter.update_source(
                FakeRequest({"id": sid, "recipient": "@newname", "frequency": "daily"})
            ))
        assert status == 200
        assert body["source"]["recipient"] == "@newname" and body["source"]["chat_id"] == "222"

    async def test_create_mail(self, db):
        status, body = _payload(await AlertSourcesRouter.create_source(
            FakeRequest({"type": "mail", "recipient": "boss@example.com", "frequency": "weekly"})
        ))
        assert status == 200 and body["source"]["type"] == "mail"

    async def test_telegram_requires_token(self, db):
        # No bot token configured → a Telegram source cannot be created.
        with patch("admin.resources.alert_sources.get_telegram_token", return_value=""):
            status, body = _payload(await AlertSourcesRouter.create_source(
                FakeRequest({"type": "telegram", "recipient": "@buyer", "frequency": "daily"})
            ))
        assert status == 422 and "error" in body

    async def test_mail_does_not_require_token(self, db):
        with patch("admin.resources.alert_sources.get_telegram_token", return_value=""):
            status, _ = _payload(await AlertSourcesRouter.create_source(
                FakeRequest({"type": "mail", "recipient": "a@b.co", "frequency": "daily"})
            ))
        assert status == 200


# ---------------------------------------------------------------------------
# Telegram token: encryption at rest + masked settings response
# ---------------------------------------------------------------------------

def test_secret_encrypt_decrypt_round_trip():
    from services.secrets import decrypt_secret, encrypt_secret

    enc = encrypt_secret("123456:ABCdef")
    assert enc and enc != "123456:ABCdef"      # stored value is not plaintext
    assert decrypt_secret(enc) == "123456:ABCdef"
    assert encrypt_secret("") == ""
    assert decrypt_secret("") == ""
    assert decrypt_secret("not-a-valid-token") == ""


class TestSettingsEndpoint:
    async def test_get_settings_masks_token(self):
        with patch("admin.resources.alert_sources.get_telegram_token", return_value="123456:ABCDEF"):
            status, body = _payload(await AlertSourcesRouter.get_settings(FakeRequest()))
        assert status == 200
        assert body["has_token"] is True
        assert body["hint"] == "••••CDEF"          # only the last 4 chars, masked
        assert "123456" not in body["hint"]
        assert "telegram_bot_token" not in body    # the raw token is never returned

    async def test_get_settings_no_token(self):
        with patch("admin.resources.alert_sources.get_telegram_token", return_value=""):
            status, body = _payload(await AlertSourcesRouter.get_settings(FakeRequest()))
        assert status == 200 and body["has_token"] is False and body["hint"] == ""

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


# ---------------------------------------------------------------------------
# Telegram chat-id resolution + deliverability (services/telegram.py)
# ---------------------------------------------------------------------------

class TestTelegramResolve:
    async def test_numeric_id_passthrough(self):
        from services.telegram import resolve_chat_id

        assert await resolve_chat_id("T", "123456") == (123456, None)
        assert await resolve_chat_id("T", "-1001234") == (-1001234, None)

    async def test_public_username_via_getchat(self):
        from services import telegram

        def fake_call(token, method, params, timeout=10):
            return {"ok": True, "result": {"id": -100999}} if method == "getChat" else {"ok": False}

        with patch("services.telegram._call", new=AsyncMock(side_effect=fake_call)):
            assert await telegram.resolve_chat_id("T", "@mychannel") == (-100999, None)

    async def test_private_user_via_getupdates(self):
        from services import telegram

        def fake_call(token, method, params, timeout=10):
            if method == "getChat":
                return {"ok": False, "description": "chat not found"}
            if method == "getUpdates":
                return {"ok": True, "result": [{"message": {"from": {"username": "Buyer"}, "chat": {"id": 555}}}]}
            return {"ok": False}

        with patch("services.telegram._call", new=AsyncMock(side_effect=fake_call)):
            cid, err = await telegram.resolve_chat_id("T", "@buyer")  # case-insensitive
        assert cid == 555 and err is None

    async def test_unresolvable_returns_error(self):
        from services import telegram

        def fake_call(token, method, params, timeout=10):
            if method == "getChat":
                return {"ok": False, "description": "chat not found"}
            if method == "getUpdates":
                return {"ok": True, "result": []}
            return {"ok": False}

        with patch("services.telegram._call", new=AsyncMock(side_effect=fake_call)):
            cid, err = await telegram.resolve_chat_id("T", "@ghost")
        assert cid is None and err

    async def test_can_send_ok_and_fail(self):
        from services import telegram

        with patch("services.telegram._call", new=AsyncMock(return_value={"ok": True})):
            assert await telegram.can_send("T", 555) == (True, None)
        with patch("services.telegram._call", new=AsyncMock(return_value={"ok": False, "description": "blocked"})):
            ok, err = await telegram.can_send("T", 555)
        assert ok is False and "blocked" in err


# ---------------------------------------------------------------------------
# SMTP settings: masking, encryption at rest, persistence, mailer decryption
# ---------------------------------------------------------------------------

class TestSmtpSettings:
    async def test_get_smtp_masks_password(self):
        cfg = {"notifications": {"smtp": {"host": "h", "username": "u", "password_enc": "ENC"}}}
        with patch.dict("config.settings", cfg, clear=False):
            status, body = _payload(await AlertSourcesRouter.get_smtp_settings(FakeRequest()))
        assert status == 200 and body["has_password"] is True
        assert body["host"] == "h" and body["username"] == "u"
        assert "password" not in body and "password_enc" not in body

    async def test_save_smtp_persists_and_encrypts(self, tmp_path):
        import yaml

        (tmp_path / "settings.yml").write_text("admin:\n  secret_key: testkey\n", encoding="utf-8")
        with patch("config.data_path", str(tmp_path)), \
                patch.dict("config.settings", {"admin": {"secret_key": "testkey"}}, clear=False):
            status, body = _payload(await AlertSourcesRouter.save_smtp_settings(FakeRequest({
                "host": "smtp.example.com", "port": 465, "username": "u@x.co",
                "password": "secretpw", "from": "Grex <u@x.co>", "use_tls": False, "use_ssl": True,
            })))
        assert status == 200 and body["has_password"] is True

        smtp = yaml.safe_load((tmp_path / "settings.yml").read_text())["notifications"]["smtp"]
        assert smtp["host"] == "smtp.example.com" and smtp["port"] == 465 and smtp["use_ssl"] is True
        assert "password" not in smtp                       # no plaintext password
        assert smtp["password_enc"] and smtp["password_enc"] != "secretpw"

    def test_mailer_decrypts_password(self):
        from services import mailer
        from services.secrets import encrypt_secret

        enc = encrypt_secret("mypw")
        with patch.dict("config.settings", {"notifications": {"smtp": {"host": "h", "password_enc": enc}}}, clear=False):
            cfg = mailer._smtp_cfg()
        assert cfg["host"] == "h" and cfg["password"] == "mypw"

    async def test_send_report_email_skips_when_unconfigured(self, tmp_path):
        from services import mailer

        report = tmp_path / "r.xlsx"
        report.write_text("x")
        with patch.dict("config.settings", {"notifications": {"smtp": {}}}, clear=False):
            ok, err = await mailer.send_report_email("a@b.co", "subj", "body", str(report))
        assert ok is False and "SMTP" in err
