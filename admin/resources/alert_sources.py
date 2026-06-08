"""Admin resource — Monitoring alert sources (UI scaffold).

CRUD endpoints for alert destinations shown on the "Параметры мониторинга"
(Monitoring parameters) settings page. Stores only the configuration — type
(Telegram/Mail), recipient and notification frequency. Actual digest generation
and delivery are a separate phase and are NOT implemented here.
"""
from __future__ import annotations

import re

from fastapi import Request
from fastapi.responses import JSONResponse
from loguru import logger

_TYPES = ("telegram", "mail")

# Fixed frequency list (enum key → human label).
_FREQUENCIES = {
    "hourly": "Раз в час",
    "every_3h": "Раз в 3 часа",
    "daily": "Раз в день",
    "weekly": "Раз в неделю",
}

_EMAIL_RE = re.compile(r"^[^@\s]+@[^@\s]+\.[^@\s]+$")
_TG_USERNAME_RE = re.compile(r"^@?[A-Za-z0-9_]{4,32}$")


def _validate_recipient(type_: str, recipient: str) -> str | None:
    """Return an error message for an invalid recipient, or None when valid."""
    recipient = (recipient or "").strip()
    if not recipient:
        return "Укажите получателя"
    if type_ == "mail":
        if not _EMAIL_RE.match(recipient):
            return "Некорректный email"
    elif type_ == "telegram":
        # Telegram: a numeric chat id, or @username / username (4–32 chars).
        if not (recipient.lstrip("-").isdigit() or _TG_USERNAME_RE.match(recipient)):
            return "Некорректный Telegram id или username"
    return None


def get_telegram_token() -> str:
    """Return the decrypted Telegram bot token, or "" when not configured."""
    from config import settings  # noqa: PLC0415
    from services.secrets import decrypt_secret  # noqa: PLC0415

    enc = settings.get("notifications", {}).get("telegram_bot_token_enc", "") or ""
    return decrypt_secret(enc)


class AlertSourcesRouter:
    """FastAPI router for /admin/alert-sources endpoints."""

    @staticmethod
    async def list_sources(request: Request) -> JSONResponse:
        from sqlalchemy import select  # noqa: PLC0415

        from models.alert_source import AlertSource  # noqa: PLC0415
        from models.job import async_session  # noqa: PLC0415

        if async_session is None:
            return JSONResponse({"total": 0, "sources": [], "frequencies": _FREQUENCIES})

        async with async_session() as session:
            rows = (await session.execute(
                select(AlertSource).order_by(AlertSource.created_at.desc())
            )).scalars().all()
        return JSONResponse({
            "total": len(rows),
            "sources": [r.to_dict() for r in rows],
            "frequencies": _FREQUENCIES,
        })

    @staticmethod
    async def create_source(request: Request) -> JSONResponse:
        """Create an alert source. Body: {type, recipient, frequency}."""
        import uuid as _uuid  # noqa: PLC0415

        from models.alert_source import AlertSource  # noqa: PLC0415
        from models.job import async_session  # noqa: PLC0415

        body = await request.json()
        type_ = (body.get("type") or "").strip().lower()
        recipient = (body.get("recipient") or "").strip()
        frequency = (body.get("frequency") or "").strip()

        if type_ not in _TYPES:
            return JSONResponse({"error": "type must be 'telegram' or 'mail'"}, status_code=422)
        if frequency not in _FREQUENCIES:
            return JSONResponse({"error": "Некорректная частота оповещений"}, status_code=422)
        err = _validate_recipient(type_, recipient)
        if err:
            return JSONResponse({"error": err}, status_code=422)

        # A Telegram source needs the bot token configured first.
        if type_ == "telegram" and not get_telegram_token():
            return JSONResponse(
                {"error": "Сначала укажите токен Telegram-бота в настройках выше"},
                status_code=422,
            )

        if async_session is None:
            return JSONResponse({"error": "DB not initialised"}, status_code=503)

        async with async_session() as session:
            source = AlertSource(
                id=str(_uuid.uuid4()), type=type_, recipient=recipient, frequency=frequency,
            )
            session.add(source)
            await session.commit()
            data = source.to_dict()

        logger.info(f"Alert source created: {type_} -> {recipient} [{frequency}]")
        return JSONResponse({"ok": True, "source": data})

    @staticmethod
    async def delete_source(request: Request) -> JSONResponse:
        """Delete an alert source by id. Body: {"id": str}."""
        from sqlalchemy import delete  # noqa: PLC0415

        from models.alert_source import AlertSource  # noqa: PLC0415
        from models.job import async_session  # noqa: PLC0415

        if async_session is None:
            return JSONResponse({"error": "DB not initialised"}, status_code=503)

        body = await request.json()
        source_id = (body.get("id") or "").strip()
        if not source_id:
            return JSONResponse({"error": "id is required"}, status_code=422)

        async with async_session() as session:
            result = await session.execute(delete(AlertSource).where(AlertSource.id == source_id))
            await session.commit()
        if result.rowcount == 0:  # type: ignore[attr-defined]
            return JSONResponse({"error": "Source not found"}, status_code=404)
        return JSONResponse({"deleted": source_id})

    @staticmethod
    async def get_settings(request: Request) -> JSONResponse:
        """Return whether a Telegram token is set and a masked hint — never the raw token."""
        token = get_telegram_token()
        if token:
            hint = "••••" + token[-4:] if len(token) >= 4 else "••••"
        else:
            hint = ""
        return JSONResponse({"has_token": bool(token), "hint": hint})

    @staticmethod
    async def save_settings(request: Request) -> JSONResponse:
        """Set/clear the Telegram bot token (stored ENCRYPTED in settings.yml).

        Body: {"telegram_bot_token": "..."} to set, or {"clear": true} to remove.
        An empty token with no clear flag is a no-op (keeps the current value).
        """
        import os  # noqa: PLC0415

        import yaml  # noqa: PLC0415

        from config import data_path, settings  # noqa: PLC0415
        from services.secrets import encrypt_secret  # noqa: PLC0415

        body = await request.json()
        clear = bool(body.get("clear"))
        token = (body.get("telegram_bot_token") or "").strip()

        if not clear and not token:
            return JSONResponse({"ok": True, "has_token": bool(get_telegram_token())})

        enc = "" if clear else encrypt_secret(token)
        settings_file = os.path.join(data_path, "settings.yml")
        try:
            with open(settings_file, encoding="utf-8") as f:
                raw = yaml.safe_load(f) or {}
            notif = raw.setdefault("notifications", {})
            notif["telegram_bot_token_enc"] = enc
            notif.pop("telegram_bot_token", None)  # drop any legacy plaintext key
            with open(settings_file, "w", encoding="utf-8") as f:
                yaml.dump(raw, f, allow_unicode=True, default_flow_style=False, sort_keys=False)
        except Exception as exc:  # noqa: BLE001
            logger.error(f"Failed to save Telegram bot token: {exc}")
            return JSONResponse({"error": "Не удалось сохранить токен"}, status_code=500)

        notif_mem = settings.setdefault("notifications", {})
        notif_mem["telegram_bot_token_enc"] = enc
        notif_mem.pop("telegram_bot_token", None)
        logger.info(f"Telegram bot token {'cleared' if clear else 'updated'} (encrypted at rest)")
        return JSONResponse({"ok": True, "has_token": bool(enc)})


def register_alert_sources_routes(app) -> None:  # type: ignore[no-untyped-def]
    """Attach alert-source endpoints to *app*."""
    from fastapi import Depends  # noqa: PLC0415

    from admin.resources.auth import require_admin  # noqa: PLC0415
    _auth = [Depends(require_admin)]
    router = AlertSourcesRouter()

    app.add_api_route(
        "/admin/alert-sources/api", router.list_sources, methods=["GET"], tags=["admin"], dependencies=_auth,
    )
    app.add_api_route(
        "/admin/alert-sources", router.create_source, methods=["POST"], tags=["admin"], dependencies=_auth,
    )
    app.add_api_route(
        "/admin/alert-sources/delete", router.delete_source, methods=["POST"], tags=["admin"], dependencies=_auth,
    )
    app.add_api_route(
        "/admin/alert-sources/settings", router.get_settings, methods=["GET"], tags=["admin"], dependencies=_auth,
    )
    app.add_api_route(
        "/admin/alert-sources/settings", router.save_settings, methods=["POST"], tags=["admin"], dependencies=_auth,
    )
