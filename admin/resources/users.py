"""Admin resource — Users (admin credentials)."""
from __future__ import annotations

from fastapi import Request
from fastapi.responses import JSONResponse
from loguru import logger


class UsersRouter:

    @staticmethod
    async def get_user(request: Request) -> JSONResponse:
        try:
            from config import settings  # noqa: PLC0415
            admin_cfg = settings.get("admin", {})
            return JSONResponse({"username": admin_cfg.get("username", "admin")})
        except Exception as exc:  # noqa: BLE001
            logger.error(f"get_user failed: {exc}")
            return JSONResponse({"error": str(exc)}, status_code=500)

    @staticmethod
    async def update_user(request: Request) -> JSONResponse:
        try:
            import os  # noqa: PLC0415

            import yaml  # noqa: PLC0415

            import config  # noqa: PLC0415
            from config import settings  # noqa: PLC0415

            body = await request.json()
            username: str = body.get("username", "").strip()
            password: str = body.get("password", "").strip()

            if not username:
                return JSONResponse({"error": "username is required"}, status_code=422)

            # The same file config.py loaded the settings from.
            settings_path = os.path.join(config.data_path, "settings.yml")

            with open(settings_path, encoding="utf-8") as f:
                data = yaml.safe_load(f) or {}

            if "admin" not in data:
                data["admin"] = {}
            data["admin"]["username"] = username
            if password:
                data["admin"]["password"] = password

            with open(settings_path, "w", encoding="utf-8") as f:
                yaml.dump(data, f, allow_unicode=True, default_flow_style=False)

            # Update in-memory settings
            settings["admin"]["username"] = username
            if password:
                settings["admin"]["password"] = password

            logger.info(f"Admin credentials updated: username={username}")
            return JSONResponse({"ok": True})
        except Exception as exc:  # noqa: BLE001
            logger.error(f"update_user failed: {exc}")
            return JSONResponse({"error": str(exc)}, status_code=500)


def register_users_routes(app) -> None:  # type: ignore[no-untyped-def]
    from fastapi import Depends  # noqa: PLC0415

    from admin.resources.auth import require_admin  # noqa: PLC0415
    _auth = [Depends(require_admin)]
    router = UsersRouter()
    app.add_api_route("/admin/users/api", router.get_user, methods=["GET"], tags=["admin"], dependencies=_auth)
    app.add_api_route("/admin/users/update", router.update_user, methods=["POST"], tags=["admin"], dependencies=_auth)
