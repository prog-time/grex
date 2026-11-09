"""Every action is visible in the log: who, what, result (ADR 0010)."""
from __future__ import annotations

import json
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from fastapi import FastAPI, Request
from fastapi.testclient import TestClient
from loguru import logger

import api
import utils.logger as log_mod
from api import _request_log_level

_API_KEY = "test-audit-key"
_API_KEY_ID = "key-42"


@pytest.fixture
def events():
    log_mod.setup_logger()
    raw: list[str] = []
    sink_id = logger.add(raw.append, format=log_mod._json_format, level="DEBUG")
    yield lambda: [json.loads(line) for line in raw]
    logger.remove(sink_id)


@pytest.fixture(autouse=True)
def _api_keys():
    async def fake_verify(key: str) -> str | None:
        return _API_KEY_ID if key == _API_KEY else None

    with patch("services.api_keys.verify_key", new=fake_verify):
        yield


@pytest.mark.parametrize(
    "method, path, status, level",
    [
        ("POST", "/admin/targets/delete", 200, "INFO"),
        ("DELETE", "/v1/monitors/1", 204, "INFO"),
        ("GET", "/v1/jobs", 200, "INFO"),
        ("GET", "/v1/jobs", 403, "WARNING"),
        ("POST", "/admin/proxies/add", 401, "WARNING"),
        ("GET", "/admin/targets", 200, "DEBUG"),
        ("GET", "/v1/health", 200, "DEBUG"),
        ("GET", "/static/app.js", 200, "DEBUG"),
        ("GET", "/admin/targets/api", 500, "ERROR"),
    ],
)
def test_request_log_level(method, path, status, level):
    assert _request_log_level(method, path, status) == level


def _http_events(docs):
    return [d for d in docs if d.get("event") == "http.request"]


def test_api_call_is_logged_with_actor_and_request_id(events):
    product = MagicMock()
    product.to_dict.return_value = {"title": "x"}
    client = TestClient(api.app, raise_server_exceptions=False)
    with patch.object(api.parsing_service, "parse_product", new=AsyncMock(return_value=product)):
        resp = client.post("/v1/parse", json={"url": "https://ozon.ru/product/1"}, headers={"X-API-Key": _API_KEY})

    assert resp.status_code == 200
    (http,) = _http_events(events())
    assert http["actor"] == f"api_key_id:{_API_KEY_ID}"
    assert http["http_method"] == "POST" and http["path"] == "/v1/parse" and http["status"] == 200
    assert http["level"] == "info"
    assert http["request_id"] == resp.headers["X-Request-ID"]
    assert isinstance(http["duration_ms"], float)


def test_rejected_api_key_is_a_warning_from_anonymous(events):
    client = TestClient(api.app, raise_server_exceptions=False)
    resp = client.post("/v1/parse", json={"url": "https://ozon.ru/product/1"}, headers={"X-API-Key": "wrong"})

    assert resp.status_code == 403
    (http,) = _http_events(events())
    assert http["actor"] == "anonymous"
    assert http["level"] == "warning"


def test_lines_inside_request_carry_actor_set_by_dependency(events):
    from admin.resources.auth import require_admin

    app = FastAPI()

    @app.get("/x")
    async def handler(request: Request) -> dict:
        logger.info("deleting target 5")
        return {}

    async def fake_admin(request: Request) -> None:
        request.state.actor = "admin:boss"
        log_mod.set_actor("admin:boss")

    app.dependency_overrides[require_admin] = fake_admin
    app.add_api_route("/y", handler, dependencies=[__import__("fastapi").Depends(require_admin)])
    TestClient(app).get("/y")

    (doc,) = [d for d in events() if d["msg"] == "deleting target 5"]
    assert doc["actor"] == "admin:boss"


def test_lines_outside_requests_are_system(events):
    logger.info("Monitor tick: checked 0")
    assert events()[0]["actor"] == "system"


def test_audit_helper_sets_event_and_fields(events):
    log_mod.audit("api_key.created", "API key created: ci", key_prefix="grex_ab")
    (doc,) = events()
    assert doc["event"] == "api_key.created"
    assert doc["key_prefix"] == "grex_ab"
    assert doc["level"] == "info"
    assert doc["function"] == "test_audit_helper_sets_event_and_fields"


@pytest.mark.asyncio
async def test_admin_login_logout_and_failure_are_audited(events):
    from starlette_admin.exceptions import FormValidationError

    from admin import SettingsAuthProvider

    provider = SettingsAuthProvider()
    request = MagicMock()
    request.session = {}
    request.client.host = "10.0.0.7"
    with patch.dict("config.settings", {"admin": {"username": "boss", "password": "pw-123456"}}):
        await provider.login("boss", "pw-123456", False, request, MagicMock())
        await provider.logout(request, MagicMock())
        with pytest.raises(FormValidationError):
            await provider.login("boss", "wrong", False, request, MagicMock())

    docs = [d for d in events() if "event" in d]
    assert [(d["event"], d["level"]) for d in docs] == [
        ("admin.login", "info"), ("admin.logout", "info"), ("admin.login_failed", "warning"),
    ]
    assert docs[0]["actor"] == "admin:boss"
    assert docs[2]["client_ip"] == "10.0.0.7"
    assert all("wrong" not in json.dumps(d) and "pw-123456" not in json.dumps(d) for d in docs)
