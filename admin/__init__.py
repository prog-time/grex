"""Admin panel — starlette-admin setup."""
from __future__ import annotations

from fastapi import FastAPI
from loguru import logger
from starlette.middleware import Middleware
from starlette.middleware.sessions import SessionMiddleware
from starlette.requests import Request
from starlette.responses import Response
from starlette_admin import CustomView, DropDown
from starlette_admin.auth import AdminUser, AuthProvider
from starlette_admin.contrib.sqla import Admin
from starlette_admin.exceptions import FormValidationError

import models.parse_target  # noqa: F401 — registers ParseTarget in Base.metadata
import models.parsed_product  # noqa: F401  — registers ParseSession, ParsedProduct in Base.metadata


# ---------------------------------------------------------------------------
# Auth — credentials from data/settings.yml
# ---------------------------------------------------------------------------

class SettingsAuthProvider(AuthProvider):
    async def login(
        self,
        username: str,
        password: str,
        remember_me: bool,
        request: Request,
        response: Response,
    ) -> Response:
        from config import settings  # noqa: PLC0415
        admin_cfg = settings.get("admin", {})
        expected_user = admin_cfg.get("username", "admin")
        expected_pass = admin_cfg.get("password", "admin")

        if username == expected_user and password == expected_pass:
            request.session.update({"username": username})
            return response
        raise FormValidationError({"password": "Invalid credentials"})

    async def is_authenticated(self, request: Request) -> bool:
        return "username" in request.session

    def get_admin_user(self, request: Request) -> AdminUser:
        return AdminUser(username=request.session.get("username", ""))

    async def logout(self, request: Request, response: Response) -> Response:
        request.session.clear()
        return response


# ---------------------------------------------------------------------------
# Views
# ---------------------------------------------------------------------------

class JobsView(CustomView):
    async def render(self, request: Request, templates) -> Response:  # type: ignore[override]
        return templates.TemplateResponse("jobs.html", {"request": request})


class ProxiesView(CustomView):
    async def render(self, request: Request, templates) -> Response:  # type: ignore[override]
        import re  # noqa: PLC0415
        from core.managers import ProxyManager  # noqa: PLC0415

        def mask(p: str) -> str:
            masked = re.sub(r"(://[^:]+):([^@]+)@", r"\1:***@", p)
            if "://" not in p:
                masked = re.sub(r"^([^:]+):([^@]+)@", r"\1:***@", p)
            return masked

        try:
            raw_proxies = await ProxyManager.get_all_proxies()
            logger.debug(f"Proxies page: loaded {len(raw_proxies)} proxies")
        except Exception as exc:  # noqa: BLE001
            logger.warning(f"ProxyManager unavailable on proxies page: {exc}")
            raw_proxies = []

        proxies = [{"index": i, "proxy": mask(p)} for i, p in enumerate(raw_proxies)]
        return templates.TemplateResponse(
            "proxies.html",
            {"request": request, "proxies": proxies},
        )


class TargetsView(CustomView):
    async def render(self, request: Request, templates) -> Response:  # type: ignore[override]
        return templates.TemplateResponse("targets.html", {"request": request})


class LinksView(CustomView):
    async def render(self, request: Request, templates) -> Response:  # type: ignore[override]
        return templates.TemplateResponse("links.html", {"request": request})


class ProductsView(CustomView):
    async def render(self, request: Request, templates) -> Response:  # type: ignore[override]
        return templates.TemplateResponse("products.html", {"request": request})


class UsersView(CustomView):
    async def render(self, request: Request, templates) -> Response:  # type: ignore[override]
        return templates.TemplateResponse("users.html", {"request": request})


# ---------------------------------------------------------------------------
# Setup / teardown
# ---------------------------------------------------------------------------

async def setup_admin(app: FastAPI) -> None:
    from config import settings  # noqa: PLC0415
    from models.job import init_db  # noqa: PLC0415
    import os  # noqa: PLC0415

    db_url: str = settings.get("admin", {}).get("db_url", "sqlite:///data/admin.db")
    secret_key: str = settings.get("admin", {}).get("secret_key", "change-me")

    await init_db(db_url)

    from models.job import engine  # noqa: PLC0415

    assert engine is not None, "Database engine was not initialised by init_db"

    templates_dir = os.path.join(os.path.dirname(__file__), "templates")

    admin = Admin(
        engine,
        title="Parser Admin",
        auth_provider=SettingsAuthProvider(),
        templates_dir=templates_dir,
        middlewares=[Middleware(SessionMiddleware, secret_key=secret_key)],
    )

    admin.add_view(TargetsView(
        "Targets", icon="fa fa-crosshairs",
        path="/targets", template_path="targets.html", name="targets",
    ))
    admin.add_view(LinksView(
        "Link Extractor", icon="fa fa-link",
        path="/links", template_path="links.html", name="links",
    ))
    admin.add_view(ProductsView(
        "Products", icon="fa fa-shopping-cart",
        path="/products", template_path="products.html", name="products",
    ))
    admin.add_view(JobsView(
        "Jobs", icon="fa fa-list",
        path="/jobs", template_path="jobs.html", name="jobs",
    ))
    admin.add_view(DropDown(
        "Settings",
        icon="fa fa-sliders",
        views=[
            ProxiesView(
                "Proxies", icon="fa fa-shield",
                path="/proxies", template_path="proxies.html", name="proxies",
            ),
            UsersView(
                "Users", icon="fa fa-users",
                path="/users", template_path="users.html", name="users",
            ),
        ],
    ))

    # Register all auxiliary API routes BEFORE mounting starlette-admin so that
    # FastAPI's exact Route entries take priority over the admin Mount prefix.
    from admin.resources.proxies import register_proxies_routes  # noqa: PLC0415
    from admin.resources.jobs import register_jobs_routes  # noqa: PLC0415
    from admin.resources.targets import register_targets_routes  # noqa: PLC0415
    from admin.resources.links import register_links_routes  # noqa: PLC0415
    from admin.resources.products import register_products_routes  # noqa: PLC0415
    from admin.resources.users import register_users_routes  # noqa: PLC0415

    register_proxies_routes(app)
    register_jobs_routes(app)
    register_targets_routes(app)
    register_links_routes(app)
    register_products_routes(app)
    register_users_routes(app)

    # Stats reset endpoint
    from fastapi.responses import JSONResponse  # noqa: PLC0415

    from fastapi import Depends  # noqa: PLC0415
    from admin.resources.auth import require_admin  # noqa: PLC0415

    async def stats_reset() -> JSONResponse:
        try:
            from services.stats import StatsCollector  # noqa: PLC0415
            await StatsCollector.reset()
            return JSONResponse({"ok": True})
        except Exception as exc:  # noqa: BLE001
            return JSONResponse({"ok": False, "error": str(exc)}, status_code=500)

    app.add_api_route(
        "/admin/api/stats/reset", stats_reset,
        methods=["POST"], tags=["admin"], dependencies=[Depends(require_admin)],
    )

    logger.info("Admin API routes registered")

    # Mount starlette-admin AFTER registering all FastAPI routes so the Mount's
    # prefix match does not shadow them.
    admin.mount_to(app)
    logger.info("starlette-admin mounted at /admin")

    # Add SessionMiddleware to the FastAPI app so that request.session is
    # available on auxiliary API routes (e.g. /admin/logs/stream).
    from starlette.middleware.sessions import SessionMiddleware as _SM  # noqa: PLC0415
    app.add_middleware(_SM, secret_key=secret_key)

    # Log all /admin routes for diagnostics
    admin_routes = [
        f"{getattr(r, 'methods', {'*'})} {getattr(r, 'path', '?')}"
        for r in app.routes
        if getattr(r, "path", "").startswith("/admin")
    ]
    logger.debug(f"Registered admin routes: {admin_routes}")


async def teardown_admin() -> None:
    try:
        from models.job import close_db  # noqa: PLC0415
        await close_db()
        logger.info("DB connections closed")
    except Exception:  # noqa: BLE001
        pass
