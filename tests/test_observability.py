"""Tests for the observability endpoints: /health, /health/ready, /metrics.

Mirrors tests/test_api.py: check_license is patched before api.py is imported so
the module-level license check is a no-op. The TestClient is created WITHOUT a
lifespan context, so Redis is never initialised and the DB session factory stays
None — i.e. the readiness probe sees both dependencies as DOWN, which is exactly
the failure path we want to assert.
"""
from __future__ import annotations

import os
import sys
from unittest.mock import AsyncMock, patch

import pytest

PROJECT_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.argv[0] = os.path.join(PROJECT_ROOT, "main.py")

with patch("licensing.checker.check_license", return_value={"key": "test-key"}):
    from fastapi.testclient import TestClient

    import api  # noqa: E402  (import after patch is intentional)


@pytest.fixture()
def client() -> TestClient:
    return TestClient(api.app, raise_server_exceptions=False)


# ---------------------------------------------------------------------------
# GET /health — liveness (must stay dependency-free)
# ---------------------------------------------------------------------------

class TestLiveness:
    def test_returns_200(self, client):
        r = client.get("/health")
        assert r.status_code == 200

    def test_status_is_healthy_regardless_of_dependencies(self, client):
        # Redis/DB are down in this harness, but liveness must still be healthy.
        r = client.get("/health")
        assert r.json()["status"] == "healthy"


# ---------------------------------------------------------------------------
# GET /health/ready — readiness (deep dependency checks)
# ---------------------------------------------------------------------------

class TestReadiness:
    def test_returns_503_when_dependencies_down(self, client):
        r = client.get("/health/ready")
        assert r.status_code == 503

    def test_reports_per_component_status(self, client):
        body = client.get("/health/ready").json()
        assert body["status"] == "not_ready"
        assert body["checks"]["database"] == "down"
        assert body["checks"]["redis"] == "down"

    def test_returns_200_when_all_checks_pass(self, client):
        with patch("api._check_database", new=AsyncMock(return_value=True)), \
                patch("api._check_redis", new=AsyncMock(return_value=True)):
            r = client.get("/health/ready")
        assert r.status_code == 200
        body = r.json()
        assert body["status"] == "ready"
        assert body["checks"] == {"database": "up", "redis": "up"}


# ---------------------------------------------------------------------------
# GET /metrics — Prometheus text exposition
# ---------------------------------------------------------------------------

class TestMetrics:
    def test_returns_200_text_plain(self, client):
        r = client.get("/metrics")
        assert r.status_code == 200
        assert r.headers["content-type"].startswith("text/plain")

    def test_exposes_core_metric_names(self, client):
        body = client.get("/metrics").text
        for name in (
            "grex_uptime_seconds",
            "grex_parsed_total",
            "grex_failed_total",
            "grex_avg_processing_seconds",
        ):
            assert name in body

    def test_includes_help_and_type_lines(self, client):
        body = client.get("/metrics").text
        assert "# HELP grex_parsed_total" in body
        assert "# TYPE grex_parsed_total counter" in body

    def test_renders_per_marketplace_rows_when_present(self, client):
        fake_summary = {
            "total_parsed": 5,
            "total_failed": 1,
            "avg_processing_time": 1.5,
            "by_marketplace": {"ozon": 3, "wildberries": 2},
        }
        with patch("services.stats.StatsCollector.get_summary", new=AsyncMock(return_value=fake_summary)), \
                patch("services.stats.StatsCollector.get_uptime", new=AsyncMock(return_value=42.0)):
            body = client.get("/metrics").text
        assert 'grex_parsed_by_marketplace_total{marketplace="ozon"} 3' in body
        assert 'grex_parsed_by_marketplace_total{marketplace="wildberries"} 2' in body
        assert "grex_uptime_seconds gauge\ngrex_uptime_seconds 42.0" in body
