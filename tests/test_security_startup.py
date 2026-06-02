"""Tests for start-time security hardening.

Covers two behaviours:
  1. licensing.checker._get_mac_address persists a STABLE device id so a flaky
     uuid.getnode() can't re-register the device on every restart.
  2. api._enforce_secure_config refuses to start in production with default
     secrets, but only warns elsewhere (so the dev setup keeps working).
"""
from __future__ import annotations

import os
import sys
from unittest.mock import patch

import pytest

PROJECT_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.argv[0] = os.path.join(PROJECT_ROOT, "main.py")

with patch("licensing.checker.check_license", return_value={"key": "test-key"}):
    import api  # noqa: E402  (module-level check runs once here, dev → warn only)

from licensing import checker  # noqa: E402

# ---------------------------------------------------------------------------
# Stable device id
# ---------------------------------------------------------------------------

class TestStableDeviceId:
    def test_returns_persisted_value_when_file_exists(self, tmp_path):
        device_file = tmp_path / "device_id"
        device_file.write_text("AA:BB:CC:DD:EE:FF", encoding="utf-8")
        with patch.object(checker, "_device_id_path", return_value=device_file):
            assert checker._get_mac_address() == "AA:BB:CC:DD:EE:FF"

    def test_persists_and_is_stable_across_calls(self, tmp_path):
        device_file = tmp_path / "device_id"
        with patch.object(checker, "_device_id_path", return_value=device_file):
            first = checker._get_mac_address()
            assert device_file.exists()
            # A subsequent call must return the same persisted value, even if
            # uuid.getnode() would now report something different.
            with patch("licensing.checker.uuid.getnode", return_value=0x123456789ABC):
                second = checker._get_mac_address()
        assert first == second

    def test_ignores_flaky_getnode_after_first_persist(self, tmp_path):
        device_file = tmp_path / "device_id"
        with patch.object(checker, "_device_id_path", return_value=device_file):
            with patch("licensing.checker.uuid.getnode", return_value=0x111111111111):
                first = checker._get_mac_address()
            with patch("licensing.checker.uuid.getnode", return_value=0x222222222222):
                second = checker._get_mac_address()
        assert first == second  # persisted value wins over the changed MAC


# ---------------------------------------------------------------------------
# Production secret enforcement
# ---------------------------------------------------------------------------

_DEFAULTS = {
    "security": {"api_key": "4353456dfvbdfg34"},
    "admin": {"password": "admin", "secret_key": "change-me-in-production-secret-key"},
}
_SECURE = {
    "security": {"api_key": "a-real-secret"},
    "admin": {"password": "s3cret", "secret_key": "another-real-secret", "environment": ""},
}


class TestEnforceSecureConfig:
    def test_aborts_in_production_with_default_secrets(self):
        with patch.dict("config.settings", _DEFAULTS, clear=False), \
                patch.dict(os.environ, {"GREX_ENV": "production"}):
            with pytest.raises(SystemExit):
                api._enforce_secure_config()

    def test_only_warns_outside_production(self):
        env = dict(os.environ)
        env.pop("GREX_ENV", None)
        with patch.dict("config.settings", _DEFAULTS, clear=False), \
                patch.dict(os.environ, env, clear=True):
            api._enforce_secure_config()  # must not raise

    def test_allows_production_with_real_secrets(self):
        with patch.dict("config.settings", _SECURE, clear=False), \
                patch.dict(os.environ, {"GREX_ENV": "production"}):
            api._enforce_secure_config()  # must not raise

    def test_is_production_reads_env_first(self):
        with patch.dict(os.environ, {"GREX_ENV": "PRODUCTION"}):
            assert api._is_production() is True
        env = dict(os.environ)
        env.pop("GREX_ENV", None)
        with patch.dict(os.environ, env, clear=True), \
                patch.dict("config.settings", {"app": {"environment": "production"}}, clear=False):
            assert api._is_production() is True

    def test_is_production_false_by_default(self):
        env = dict(os.environ)
        env.pop("GREX_ENV", None)
        with patch.dict(os.environ, env, clear=True), \
                patch.dict("config.settings", {"app": {"environment": "dev"}}, clear=False):
            assert api._is_production() is False
