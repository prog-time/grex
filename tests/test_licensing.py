"""Tests for licensing/checker.py.

All network calls and file I/O are mocked — no external dependencies required.
"""
from __future__ import annotations

import json
import urllib.error
from unittest.mock import MagicMock, patch

import pytest

# ---------------------------------------------------------------------------
# check_license — disabled
# ---------------------------------------------------------------------------

class TestCheckLicenseDisabled:
    def test_raises_systemexit_when_api_key_is_default_placeholder(self):
        """check_license must abort when api_key is the default placeholder value."""
        from licensing import checker

        with patch.dict("config.settings", {"licensing": {
            "service_url": "https://grex.iliya-code.ru/api",
            "api_key": "your-client-api-key-here",
        }}):
            with pytest.raises(SystemExit):
                checker.check_license()


# ---------------------------------------------------------------------------
# check_license — missing config
# ---------------------------------------------------------------------------

class TestCheckLicenseMissingConfig:
    def test_raises_systemexit_when_service_url_missing(self):
        from licensing import checker

        with patch.dict("config.settings", {"licensing": {"enabled": True, "api_key": "key", "service_url": ""}}):
            with pytest.raises(SystemExit):
                checker.check_license()

    def test_raises_systemexit_when_api_key_missing(self):
        from licensing import checker

        with patch.dict("config.settings", {
            "licensing": {"enabled": True, "api_key": "", "service_url": "https://example.com"},
        }):
            with pytest.raises(SystemExit):
                checker.check_license()


# ---------------------------------------------------------------------------
# check_license — HTTP 200 success
# ---------------------------------------------------------------------------

class TestCheckLicenseSuccess:
    def _fake_urlopen(self, response_body: dict):
        # Startup flow is two-step: POST /activate then POST /config. /activate
        # must report {"activated": True}; /config returns the license payload.
        def _open(req, timeout=10):
            resp = MagicMock()
            if req.full_url.endswith("/activate"):
                resp.read.return_value = json.dumps({"activated": True}).encode()
            else:
                resp.read.return_value = json.dumps(response_body).encode()
            resp.__enter__ = lambda s: s
            resp.__exit__ = MagicMock(return_value=False)
            return resp
        return _open

    def test_returns_response_dict_on_success(self, tmp_path):
        from licensing import checker

        response = {"client_id": "uuid-1", "issued_at": "2026-03-30T10:00:00Z", "tokens": ["tok1"]}

        with patch.dict("config.settings", {"licensing": {
            "enabled": True,
            "service_url": "https://grex.iliya-code.ru/api",
            "api_key": "test-key",
            "timeout_seconds": 5,
            "cache_file": str(tmp_path / "cache.json"),
        }}), patch("urllib.request.urlopen", side_effect=self._fake_urlopen(response)):
            result = checker.check_license()

        assert result["client_id"] == "uuid-1"
        assert result["tokens"] == ["tok1"]

    def test_saves_cache_on_success(self, tmp_path):
        from licensing import checker

        cache_file = tmp_path / "cache.json"
        response = {"client_id": "uuid-2", "issued_at": "2026-03-30T10:00:00Z", "tokens": ["tok-a", "tok-b"]}

        with patch.dict("config.settings", {"licensing": {
            "enabled": True,
            "service_url": "https://grex.iliya-code.ru/api",
            "api_key": "test-key",
            "timeout_seconds": 5,
            "cache_file": str(cache_file),
        }}), patch("urllib.request.urlopen", side_effect=self._fake_urlopen(response)):
            checker.check_license()

        assert cache_file.exists()
        data = json.loads(cache_file.read_text(encoding="utf-8"))
        assert data["client_id"] == "uuid-2"
        assert data["tokens"] == ["tok-a", "tok-b"]
        assert "fetched_at" in data

    def test_posts_to_config_endpoint(self, tmp_path):
        from licensing import checker

        captured_reqs = []
        response = {"client_id": "x", "tokens": []}

        def fake_urlopen(req, timeout=10):
            captured_reqs.append(req)
            resp = MagicMock()
            if req.full_url.endswith("/activate"):
                resp.read.return_value = json.dumps({"activated": True}).encode()
            else:
                resp.read.return_value = json.dumps(response).encode()
            resp.__enter__ = lambda s: s
            resp.__exit__ = MagicMock(return_value=False)
            return resp

        with patch.dict("config.settings", {"licensing": {
            "enabled": True,
            "service_url": "https://grex.iliya-code.ru/api",
            "api_key": "test-key",
            "timeout_seconds": 5,
            "cache_file": str(tmp_path / "cache.json"),
        }}), patch("urllib.request.urlopen", side_effect=fake_urlopen):
            checker.check_license()

        assert any(
            r.full_url == "https://grex.iliya-code.ru/api/config" for r in captured_reqs
        )

    def test_sends_api_key_header(self, tmp_path):
        from licensing import checker

        captured_reqs = []
        response = {"client_id": "x", "tokens": []}

        def fake_urlopen(req, timeout=10):
            captured_reqs.append(req)
            resp = MagicMock()
            if req.full_url.endswith("/activate"):
                resp.read.return_value = json.dumps({"activated": True}).encode()
            else:
                resp.read.return_value = json.dumps(response).encode()
            resp.__enter__ = lambda s: s
            resp.__exit__ = MagicMock(return_value=False)
            return resp

        with patch.dict("config.settings", {"licensing": {
            "enabled": True,
            "service_url": "https://grex.iliya-code.ru/api",
            "api_key": "my-secret-key",
            "timeout_seconds": 5,
            "cache_file": str(tmp_path / "cache.json"),
        }}), patch("urllib.request.urlopen", side_effect=fake_urlopen):
            checker.check_license()

        assert captured_reqs[0].get_header("X-api-key") == "my-secret-key"

    def test_request_body_contains_mac_address(self, tmp_path):
        from licensing import checker

        captured_reqs = []
        response = {"client_id": "x", "tokens": []}

        def fake_urlopen(req, timeout=10):
            captured_reqs.append(req)
            resp = MagicMock()
            if req.full_url.endswith("/activate"):
                resp.read.return_value = json.dumps({"activated": True}).encode()
            else:
                resp.read.return_value = json.dumps(response).encode()
            resp.__enter__ = lambda s: s
            resp.__exit__ = MagicMock(return_value=False)
            return resp

        cfg = {"licensing": {
            "service_url": "https://grex.iliya-code.ru/api",
            "api_key": "test-key",
            "timeout_seconds": 5,
            "cache_file": str(tmp_path / "cache.json"),
        }}
        with patch.dict("config.settings", cfg), \
                patch("urllib.request.urlopen", side_effect=fake_urlopen), \
                patch.object(checker, "_get_mac_address", return_value="AA:BB:CC:DD:EE:FF"):
            checker.check_license()

        body = json.loads(captured_reqs[0].data.decode())
        assert body["mac_address"] == "AA:BB:CC:DD:EE:FF"


# ---------------------------------------------------------------------------
# check_license — HTTP 401
# ---------------------------------------------------------------------------

class TestCheckLicense401:
    def test_raises_systemexit_on_401(self, tmp_path):
        from licensing import checker

        http_error = urllib.error.HTTPError(
            url="https://grex.iliya-code.ru/api/config",
            code=401,
            msg="Unauthorized",
            hdrs=MagicMock(),
            fp=None,
        )

        with patch.dict("config.settings", {"licensing": {
            "enabled": True,
            "service_url": "https://grex.iliya-code.ru/api",
            "api_key": "bad-key",
            "timeout_seconds": 5,
            "cache_file": str(tmp_path / "cache.json"),
        }}), patch("urllib.request.urlopen", side_effect=http_error):
            with pytest.raises(SystemExit):
                checker.check_license()


# ---------------------------------------------------------------------------
# check_license — HTTP 403
# ---------------------------------------------------------------------------

class TestCheckLicense403:
    def _make_http_error(self, code: int, body: dict | None = None):
        fp = MagicMock()
        fp.read.return_value = json.dumps(body).encode() if body else b""
        return urllib.error.HTTPError(
            url="https://grex.iliya-code.ru/api/config",
            code=code,
            msg="Forbidden",
            hdrs=MagicMock(),
            fp=fp,
        )

    def test_raises_systemexit_on_403_generic(self, tmp_path):
        from licensing import checker

        with patch.dict("config.settings", {"licensing": {
            "service_url": "https://grex.iliya-code.ru/api",
            "api_key": "expired-key",
            "timeout_seconds": 5,
            "cache_file": str(tmp_path / "cache.json"),
        }}), patch("urllib.request.urlopen", side_effect=self._make_http_error(403)):
            with pytest.raises(SystemExit):
                checker.check_license()

    def test_raises_systemexit_on_403_blocked(self, tmp_path):
        from licensing import checker

        with patch.dict("config.settings", {"licensing": {
            "service_url": "https://grex.iliya-code.ru/api",
            "api_key": "blocked-key",
            "timeout_seconds": 5,
            "cache_file": str(tmp_path / "cache.json"),
        }}), patch("urllib.request.urlopen", side_effect=self._make_http_error(403, {"error": "Device blocked"})):
            with pytest.raises(SystemExit):
                checker.check_license()

    def test_raises_systemexit_on_403_expired(self, tmp_path):
        from licensing import checker

        with patch.dict("config.settings", {"licensing": {
            "service_url": "https://grex.iliya-code.ru/api",
            "api_key": "expired-key",
            "timeout_seconds": 5,
            "cache_file": str(tmp_path / "cache.json"),
        }}), patch("urllib.request.urlopen", side_effect=self._make_http_error(403, {"error": "Subscription expired"})):
            with pytest.raises(SystemExit):
                checker.check_license()

    def test_activates_device_before_fetching_config(self, tmp_path):
        """Startup must POST /activate before POST /config and return the config payload."""
        from licensing import checker

        config_response = {"client_id": "uuid-act", "tokens": ["tok1"]}
        activate_response = {"activated": True, "message": "Устройство активировано."}

        calls = []

        def fake_urlopen(req, timeout=10):
            calls.append(req.full_url)
            resp = MagicMock()
            if req.full_url.endswith("/activate"):
                resp.read.return_value = json.dumps(activate_response).encode()
            else:
                resp.read.return_value = json.dumps(config_response).encode()
            resp.__enter__ = lambda s: s
            resp.__exit__ = MagicMock(return_value=False)
            return resp

        cfg = {"licensing": {
            "service_url": "https://grex.iliya-code.ru/api",
            "api_key": "new-key",
            "timeout_seconds": 5,
            "cache_file": str(tmp_path / "cache.json"),
        }}
        with patch.dict("config.settings", cfg), \
                patch("urllib.request.urlopen", side_effect=fake_urlopen), \
                patch.object(checker, "_get_mac_address", return_value="AA:BB:CC:DD:EE:FF"):
            result = checker.check_license()

        activate_idx = next(i for i, u in enumerate(calls) if u.endswith("/activate"))
        config_idx = next(i for i, u in enumerate(calls) if u.endswith("/config"))
        assert activate_idx < config_idx, "Expected /activate to be called before /config"
        assert result["client_id"] == "uuid-act"

    def test_raises_systemexit_when_activate_returns_401(self, tmp_path):
        from licensing import checker

        def fake_urlopen(req, timeout=10):
            if req.full_url.endswith("/config"):
                fp = MagicMock()
                fp.read.return_value = json.dumps({"error": "Device not activated"}).encode()
                raise urllib.error.HTTPError(req.full_url, 403, "Forbidden", MagicMock(), fp)
            # /activate returns 401
            fp = MagicMock()
            fp.read.return_value = b""
            raise urllib.error.HTTPError(req.full_url, 401, "Unauthorized", MagicMock(), fp)

        cfg = {"licensing": {
            "service_url": "https://grex.iliya-code.ru/api",
            "api_key": "bad-key",
            "timeout_seconds": 5,
            "cache_file": str(tmp_path / "cache.json"),
        }}
        with patch.dict("config.settings", cfg), \
                patch("urllib.request.urlopen", side_effect=fake_urlopen), \
                patch.object(checker, "_get_mac_address", return_value="AA:BB:CC:DD:EE:FF"):
            with pytest.raises(SystemExit):
                checker.check_license()


# ---------------------------------------------------------------------------
# check_license — network error
# ---------------------------------------------------------------------------

class TestCheckLicenseNetworkError:
    def test_raises_systemexit_when_no_network_and_no_cache(self, tmp_path):
        from licensing import checker

        with patch.dict("config.settings", {"licensing": {
            "enabled": True,
            "service_url": "https://grex.iliya-code.ru/api",
            "api_key": "key",
            "timeout_seconds": 5,
            "cache_file": str(tmp_path / "no_cache.json"),
        }}), patch("urllib.request.urlopen", side_effect=OSError("Network unreachable")):
            with pytest.raises(SystemExit):
                checker.check_license()

    def test_does_not_raise_when_no_network_but_cache_exists(self, tmp_path):
        from licensing import checker

        cache_file = tmp_path / "cache.json"
        cache_file.write_text(
            json.dumps({"client_id": "uuid-3", "tokens": ["tok1"], "fetched_at": "2026-03-30T10:00:00Z"}),
            encoding="utf-8",
        )

        with patch.dict("config.settings", {"licensing": {
            "enabled": True,
            "service_url": "https://grex.iliya-code.ru/api",
            "api_key": "key",
            "timeout_seconds": 5,
            "cache_file": str(cache_file),
        }}), patch("urllib.request.urlopen", side_effect=OSError("Network unreachable")):
            result = checker.check_license()

        assert result == {}


# ---------------------------------------------------------------------------
# _read_license_file / cache I/O
# ---------------------------------------------------------------------------

class TestReadLicenseFile:
    def test_returns_cache_contents(self, tmp_path):
        from licensing import checker

        cache_file = tmp_path / "cache.json"
        payload = {"client_id": "uuid-4", "tokens": ["t1"], "fetched_at": "2026-03-30T10:00:00Z"}
        cache_file.write_text(json.dumps(payload), encoding="utf-8")

        with patch.object(checker, "_cache_file_path", return_value=cache_file):
            result = checker._read_license_file()

        assert result["client_id"] == "uuid-4"

    def test_returns_empty_dict_when_no_cache(self, tmp_path):
        from licensing import checker

        non_existent = tmp_path / "no_cache.json"
        with patch.object(checker, "_cache_file_path", return_value=non_existent):
            result = checker._read_license_file()

        assert result == {}
