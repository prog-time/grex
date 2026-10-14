"""Every admin API route requires an admin session.

Builds an app with all admin route groups (as setup_admin does, without the
starlette-admin UI and the database) and calls each route without logging in.
"""
from __future__ import annotations

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient
from starlette.middleware.sessions import SessionMiddleware

from admin.resources.alert_sources import register_alert_sources_routes
from admin.resources.api_keys import register_api_keys_routes
from admin.resources.jobs import register_jobs_routes
from admin.resources.links import register_links_routes
from admin.resources.logs import register_logs_routes
from admin.resources.monitors import register_monitors_routes
from admin.resources.products import register_products_routes
from admin.resources.proxies import register_proxies_routes
from admin.resources.targets import register_targets_routes
from admin.resources.users import register_users_routes

REGISTER = [
    register_alert_sources_routes, register_api_keys_routes, register_jobs_routes, register_links_routes,
    register_logs_routes, register_monitors_routes, register_products_routes, register_proxies_routes,
    register_targets_routes, register_users_routes,
]


def _app() -> FastAPI:
    app = FastAPI()
    app.add_middleware(SessionMiddleware, secret_key="test-secret")
    for register in REGISTER:
        register(app)
    return app


APP = _app()
ROUTES = sorted(
    (method, route.path)
    for route in APP.routes
    if route.path.startswith("/admin/")
    for method in getattr(route, "methods", ())
    if method != "HEAD"
)


def test_all_admin_route_groups_are_registered():
    assert len(ROUTES) >= 50


@pytest.mark.parametrize(("method", "path"), ROUTES, ids=lambda v: v)
def test_route_requires_admin_session(method, path):
    url = path.replace("{session_id}", "unknown")
    response = TestClient(APP).request(method, url, json={})
    assert response.status_code == 401, response.text
