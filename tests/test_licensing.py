"""Tests for licensing/checker.py and register.py.

All network calls and file I/O are mocked — no external dependencies required.
"""
from __future__ import annotations

import datetime
import hmac
import hashlib
import json
import uuid
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

_SECRET = "KBSox0KKob3hiCwAkdWfBzdYFRS8gIeFvWrO8dn5ipvttuW0jSdaxT3BlmmmFDHZ"


def _compute_key(mac: str) -> str:
    return hmac.new(_SECRET.encode(), mac.encode(), hashlib.sha256).hexdigest()


def _this_mac() -> str:
    mac_int = uuid.getnode()
    return ":".join(f"{(mac_int >> i) & 0xff:02x}" for i in range(40, -1, -8))


def _valid_key() -> str:
    return _compute_key(_this_mac())


# ---------------------------------------------------------------------------
# check_license — file not found
# ---------------------------------------------------------------------------

class TestCheckLicenseFileNotFound:
    def test_raises_systemexit_when_file_missing(self, tmp_path):
        """If license.json does not exist, SystemExit(1) must be raised."""
        from licensing import checker

        non_existent = tmp_path / "no_such_file.json"
        with patch.object(checker, "_LICENSE_PATH", non_existent):
            with pytest.raises(SystemExit):
                checker.check_license()


# ---------------------------------------------------------------------------
# check_license — key mismatch (wrong machine)
# ---------------------------------------------------------------------------

class TestCheckLicenseKeyMismatch:
    def test_raises_systemexit_when_key_belongs_to_different_mac(self, tmp_path):
        """A license.json whose key was generated for a different MAC must be rejected."""
        from licensing import checker

        foreign_mac = "de:ad:be:ef:00:01"
        foreign_key = _compute_key(foreign_mac)
        license_file = tmp_path / "license.json"
        license_file.write_text(json.dumps({"key": foreign_key}), encoding="utf-8")

        with patch.object(checker, "_LICENSE_PATH", license_file):
            with pytest.raises(SystemExit):
                checker.check_license()


# ---------------------------------------------------------------------------
# check_license — online status: blocked
# ---------------------------------------------------------------------------

class TestCheckLicenseBlockedOnline:
    def test_raises_systemexit_when_online_status_is_blocked(self, tmp_path):
        """An online status of 'blocked' must cause SystemExit."""
        from licensing import checker

        key = _valid_key()
        license_file = tmp_path / "license.json"
        license_file.write_text(json.dumps({"key": key}), encoding="utf-8")

        online_data = {key: {"status": "blocked", "updates_until": "2099-01-01"}}

        def fake_urlopen(url, timeout=5):
            resp = MagicMock()
            resp.read.return_value = json.dumps(online_data).encode()
            resp.__enter__ = lambda s: s
            resp.__exit__ = MagicMock(return_value=False)
            return resp

        with patch.object(checker, "_LICENSE_PATH", license_file), \
             patch("urllib.request.urlopen", side_effect=fake_urlopen):
            with pytest.raises(SystemExit):
                checker.check_license()


# ---------------------------------------------------------------------------
# check_license — online status: pending (not active, not blocked)
# ---------------------------------------------------------------------------

class TestCheckLicensePendingOnline:
    def test_raises_systemexit_when_online_status_is_pending(self, tmp_path):
        """Any status other than 'active' (e.g. 'pending') must cause SystemExit."""
        from licensing import checker

        key = _valid_key()
        license_file = tmp_path / "license.json"
        license_file.write_text(json.dumps({"key": key}), encoding="utf-8")

        online_data = {key: {"status": "pending", "updates_until": "2099-01-01"}}

        def fake_urlopen(url, timeout=5):
            resp = MagicMock()
            resp.read.return_value = json.dumps(online_data).encode()
            resp.__enter__ = lambda s: s
            resp.__exit__ = MagicMock(return_value=False)
            return resp

        with patch.object(checker, "_LICENSE_PATH", license_file), \
             patch("urllib.request.urlopen", side_effect=fake_urlopen):
            with pytest.raises(SystemExit):
                checker.check_license()


# ---------------------------------------------------------------------------
# check_license — online status: active → success
# ---------------------------------------------------------------------------

class TestCheckLicenseActiveOnline:
    def test_returns_merged_dict_when_license_is_active(self, tmp_path):
        """A valid license with 'active' online status must return a merged dict."""
        from licensing import checker

        key = _valid_key()
        license_file = tmp_path / "license.json"
        license_file.write_text(json.dumps({"key": key}), encoding="utf-8")

        online_data = {key: {"status": "active", "updates_until": "2099-01-01"}}

        def fake_urlopen(url, timeout=5):
            resp = MagicMock()
            resp.read.return_value = json.dumps(online_data).encode()
            resp.__enter__ = lambda s: s
            resp.__exit__ = MagicMock(return_value=False)
            return resp

        with patch.object(checker, "_LICENSE_PATH", license_file), \
             patch("urllib.request.urlopen", side_effect=fake_urlopen):
            result = checker.check_license()

        assert result["key"] == key
        assert result["status"] == "active"
        assert result["updates_until"] == "2099-01-01"


# ---------------------------------------------------------------------------
# check_license — key not in online registry
# ---------------------------------------------------------------------------

class TestCheckLicenseKeyNotInRegistry:
    def test_raises_systemexit_when_key_absent_from_online_registry(self, tmp_path):
        """If the key is not found in the online registry, SystemExit must be raised."""
        from licensing import checker

        key = _valid_key()
        license_file = tmp_path / "license.json"
        license_file.write_text(json.dumps({"key": key}), encoding="utf-8")

        # Registry is empty — key not present
        online_data: dict = {}

        def fake_urlopen(url, timeout=5):
            resp = MagicMock()
            resp.read.return_value = json.dumps(online_data).encode()
            resp.__enter__ = lambda s: s
            resp.__exit__ = MagicMock(return_value=False)
            return resp

        with patch.object(checker, "_LICENSE_PATH", license_file), \
             patch("urllib.request.urlopen", side_effect=fake_urlopen):
            with pytest.raises(SystemExit):
                checker.check_license()


# ---------------------------------------------------------------------------
# check_license — network failure
# ---------------------------------------------------------------------------

class TestCheckLicenseNetworkFailure:
    def test_raises_systemexit_when_network_is_unavailable(self, tmp_path):
        """A network error during online check must cause SystemExit."""
        from licensing import checker

        key = _valid_key()
        license_file = tmp_path / "license.json"
        license_file.write_text(json.dumps({"key": key}), encoding="utf-8")

        with patch.object(checker, "_LICENSE_PATH", license_file), \
             patch("urllib.request.urlopen", side_effect=OSError("Network unreachable")):
            with pytest.raises(SystemExit):
                checker.check_license()


# ---------------------------------------------------------------------------
# check_license — corrupted JSON
# ---------------------------------------------------------------------------

class TestCheckLicenseCorruptedFile:
    def test_raises_systemexit_when_license_json_is_corrupted(self, tmp_path):
        """A license.json with invalid JSON content must cause SystemExit."""
        from licensing import checker

        license_file = tmp_path / "license.json"
        license_file.write_text("not valid json {{{{", encoding="utf-8")

        with patch.object(checker, "_LICENSE_PATH", license_file):
            with pytest.raises(SystemExit):
                checker.check_license()


# ---------------------------------------------------------------------------
# check_updates_allowed
# ---------------------------------------------------------------------------

class TestCheckUpdatesAllowed:
    def _license_file_and_key(self, tmp_path) -> tuple[Path, str]:
        key = _valid_key()
        license_file = tmp_path / "license.json"
        license_file.write_text(json.dumps({"key": key}), encoding="utf-8")
        return license_file, key

    def _fake_urlopen(self, online_data: dict):
        def _open(url, timeout=5):
            resp = MagicMock()
            resp.read.return_value = json.dumps(online_data).encode()
            resp.__enter__ = lambda s: s
            resp.__exit__ = MagicMock(return_value=False)
            return resp
        return _open

    def test_returns_false_when_updates_until_is_in_the_past(self, tmp_path):
        from licensing import checker

        license_file, key = self._license_file_and_key(tmp_path)
        online_data = {key: {"status": "active", "updates_until": "2000-01-01"}}

        with patch.object(checker, "_LICENSE_PATH", license_file), \
             patch("urllib.request.urlopen", side_effect=self._fake_urlopen(online_data)):
            result = checker.check_updates_allowed()

        assert result is False

    def test_returns_true_when_updates_until_is_in_the_future(self, tmp_path):
        from licensing import checker

        license_file, key = self._license_file_and_key(tmp_path)
        online_data = {key: {"status": "active", "updates_until": "2099-12-31"}}

        with patch.object(checker, "_LICENSE_PATH", license_file), \
             patch("urllib.request.urlopen", side_effect=self._fake_urlopen(online_data)):
            result = checker.check_updates_allowed()

        assert result is True

    def test_returns_true_when_updates_until_is_today(self, tmp_path):
        from licensing import checker

        license_file, key = self._license_file_and_key(tmp_path)
        today = datetime.date.today().isoformat()
        online_data = {key: {"status": "active", "updates_until": today}}

        with patch.object(checker, "_LICENSE_PATH", license_file), \
             patch("urllib.request.urlopen", side_effect=self._fake_urlopen(online_data)):
            result = checker.check_updates_allowed()

        assert result is True

    def test_raises_systemexit_when_network_fails(self, tmp_path):
        """_fetch_license_entry calls _abort on network failure, which raises SystemExit.
        check_updates_allowed catches Exception but not SystemExit, so it propagates."""
        from licensing import checker

        license_file, key = self._license_file_and_key(tmp_path)

        with patch.object(checker, "_LICENSE_PATH", license_file), \
             patch("urllib.request.urlopen", side_effect=OSError("No network")):
            with pytest.raises(SystemExit):
                checker.check_updates_allowed()

    def test_raises_systemexit_when_license_file_missing(self, tmp_path):
        """_read_license_file calls _abort when file is absent, raising SystemExit
        which propagates out of check_updates_allowed (not caught by bare Exception)."""
        from licensing import checker

        non_existent = tmp_path / "no_license.json"

        with patch.object(checker, "_LICENSE_PATH", non_existent):
            with pytest.raises(SystemExit):
                checker.check_updates_allowed()


# ---------------------------------------------------------------------------
# register.py — generates a correct license.json
# ---------------------------------------------------------------------------

class TestRegisterGeneratesLicense:
    def test_creates_license_json_with_correct_key(self, tmp_path):
        """register.main() must create license.json whose key matches the current MAC."""
        import register

        license_file = tmp_path / "license.json"

        with patch.object(register, "_LICENSE_PATH", license_file):
            register.main()

        assert license_file.exists(), "license.json must be created"
        data = json.loads(license_file.read_text(encoding="utf-8"))
        assert "key" in data

        expected_key = _compute_key(_this_mac())
        assert data["key"] == expected_key

    def test_does_not_overwrite_existing_license_json(self, tmp_path, capsys):
        """register.main() must print the existing key and NOT overwrite the file."""
        import register

        original_key = "already-there-key"
        license_file = tmp_path / "license.json"
        license_file.write_text(json.dumps({"key": original_key}), encoding="utf-8")

        with patch.object(register, "_LICENSE_PATH", license_file):
            register.main()

        data = json.loads(license_file.read_text(encoding="utf-8"))
        assert data["key"] == original_key, "Existing key must not be overwritten"

        captured = capsys.readouterr()
        assert original_key in captured.out
