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

_TYPES = ("telegram",)

# Fixed frequency list (enum key → human label).
_FREQUENCIES = {
    "every_10m": "Раз в 10 минут",
    "hourly": "Раз в час",
    "every_3h": "Раз в 3 часа",
    "daily": "Раз в день",
    "weekly": "Раз в неделю",
}

_TG_USERNAME_RE = re.compile(r"^@?[A-Za-z0-9_]{4,32}$")


def _validate_recipient(type_: str, recipient: str) -> str | None:
    """Return an error message for an invalid recipient, or None when valid."""
    recipient = (recipient or "").strip()
    if not recipient:
        return "Укажите получателя"
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
            return JSONResponse({"error": "type must be 'telegram'"}, status_code=422)
        if frequency not in _FREQUENCIES:
            return JSONResponse({"error": "Некорректная частота оповещений"}, status_code=422)
        err = _validate_recipient(type_, recipient)
        if err:
            return JSONResponse({"error": err}, status_code=422)

        chat_id: str | None = None
        # Telegram: the bot can only message a numeric chat id, so resolve the
        # recipient to one and verify the bot can actually deliver to it.
        if type_ == "telegram":
            token = get_telegram_token()
            if not token:
                return JSONResponse(
                    {"error": "Сначала укажите токен Telegram-бота в настройках выше"},
                    status_code=422,
                )
            from services.telegram import can_send, resolve_chat_id  # noqa: PLC0415
            resolved, err = await resolve_chat_id(token, recipient)
            if err:
                return JSONResponse({"error": err}, status_code=422)
            ok, send_err = await can_send(token, resolved)
            if not ok:
                return JSONResponse(
                    {"error": f"Бот не может отправлять сообщения этому получателю: {send_err}"},
                    status_code=422,
                )
            chat_id = str(resolved)

        if async_session is None:
            return JSONResponse({"error": "DB not initialised"}, status_code=503)

        async with async_session() as session:
            source = AlertSource(
                id=str(_uuid.uuid4()), type=type_, recipient=recipient,
                chat_id=chat_id, frequency=frequency,
            )
            session.add(source)
            await session.commit()
            data = source.to_dict()

        logger.info(f"Alert source created: {type_} -> {recipient} (chat_id={chat_id}) [{frequency}]")
        return JSONResponse({"ok": True, "source": data})

    @staticmethod
    async def update_source(request: Request) -> JSONResponse:
        """Edit an existing source. Body: {id, recipient, frequency}.

        The type is immutable. For Telegram, the chat id is re-resolved and
        delivery re-verified only when the recipient changed (or was never set).
        """
        from sqlalchemy import select  # noqa: PLC0415

        from models.alert_source import AlertSource  # noqa: PLC0415
        from models.job import async_session  # noqa: PLC0415

        body = await request.json()
        source_id = (body.get("id") or "").strip()
        recipient = (body.get("recipient") or "").strip()
        frequency = (body.get("frequency") or "").strip()

        if not source_id:
            return JSONResponse({"error": "id is required"}, status_code=422)
        if frequency not in _FREQUENCIES:
            return JSONResponse({"error": "Некорректная частота оповещений"}, status_code=422)
        if async_session is None:
            return JSONResponse({"error": "DB not initialised"}, status_code=503)

        async with async_session() as session:
            source = (await session.execute(
                select(AlertSource).where(AlertSource.id == source_id)
            )).scalar_one_or_none()
            if source is None:
                return JSONResponse({"error": "Source not found"}, status_code=404)

            type_ = source.type
            err = _validate_recipient(type_, recipient)
            if err:
                return JSONResponse({"error": err}, status_code=422)

            chat_id = source.chat_id
            if type_ == "telegram" and (recipient != source.recipient or not source.chat_id):
                token = get_telegram_token()
                if not token:
                    return JSONResponse(
                        {"error": "Сначала укажите токен Telegram-бота в настройках выше"},
                        status_code=422,
                    )
                from services.telegram import can_send, resolve_chat_id  # noqa: PLC0415
                resolved, rerr = await resolve_chat_id(token, recipient)
                if rerr:
                    return JSONResponse({"error": rerr}, status_code=422)
                ok, send_err = await can_send(token, resolved)
                if not ok:
                    return JSONResponse(
                        {"error": f"Бот не может отправлять сообщения этому получателю: {send_err}"},
                        status_code=422,
                    )
                chat_id = str(resolved)

            source.recipient = recipient
            source.frequency = frequency
            source.chat_id = chat_id
            await session.commit()
            data = source.to_dict()

        logger.info(f"Alert source updated: {source_id} -> {recipient} (chat_id={chat_id}) [{frequency}]")
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
        """Return Telegram token presence (masked hint) and the report period."""
        from services.reporting import report_period_days  # noqa: PLC0415

        token = get_telegram_token()
        if token:
            hint = "••••" + token[-4:] if len(token) >= 4 else "••••"
        else:
            hint = ""
        return JSONResponse({
            "has_token": bool(token),
            "hint": hint,
            "report_period_days": report_period_days(),
        })

    @staticmethod
    async def save_settings(request: Request) -> JSONResponse:
        """Persist monitoring settings to settings.yml.

        Body fields (all optional, applied if present):
          * ``telegram_bot_token`` — set the bot token (stored ENCRYPTED), or
          * ``clear: true``        — remove the stored token.
          * ``report_period_days`` — report look-back window (1–365 days).
        A request that carries none of these is a no-op.
        """
        import os  # noqa: PLC0415

        import yaml  # noqa: PLC0415

        from config import data_path, settings  # noqa: PLC0415
        from services.secrets import encrypt_secret  # noqa: PLC0415

        body = await request.json()
        clear = bool(body.get("clear"))
        token = (body.get("telegram_bot_token") or "").strip()
        set_token = clear or bool(token)

        period: int | None = None
        period_raw = body.get("report_period_days")
        if period_raw is not None and str(period_raw).strip() != "":
            try:
                period = int(period_raw)
            except (TypeError, ValueError):
                return JSONResponse({"error": "Период должен быть целым числом дней"}, status_code=422)
            if not 1 <= period <= 365:
                return JSONResponse({"error": "Период должен быть от 1 до 365 дней"}, status_code=422)

        if not set_token and period is None:
            return JSONResponse({"ok": True, "has_token": bool(get_telegram_token())})

        settings_file = os.path.join(data_path, "settings.yml")
        try:
            with open(settings_file, encoding="utf-8") as f:
                raw = yaml.safe_load(f) or {}
            notif = raw.setdefault("notifications", {})
            if set_token:
                notif["telegram_bot_token_enc"] = "" if clear else encrypt_secret(token)
                notif.pop("telegram_bot_token", None)  # drop any legacy plaintext key
            if period is not None:
                notif["report_period_days"] = period
            with open(settings_file, "w", encoding="utf-8") as f:
                yaml.dump(raw, f, allow_unicode=True, default_flow_style=False, sort_keys=False)
        except Exception as exc:  # noqa: BLE001
            logger.error(f"Failed to save monitoring settings: {exc}")
            return JSONResponse({"error": "Не удалось сохранить настройки"}, status_code=500)

        notif_mem = settings.setdefault("notifications", {})
        if set_token:
            notif_mem["telegram_bot_token_enc"] = notif["telegram_bot_token_enc"]
            notif_mem.pop("telegram_bot_token", None)
        if period is not None:
            notif_mem["report_period_days"] = period
        logger.info(f"Monitoring settings updated (token={'changed' if set_token else 'kept'}, period={period})")
        return JSONResponse({"ok": True, "has_token": bool(get_telegram_token()), "report_period_days": period})


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
        "/admin/alert-sources/update", router.update_source, methods=["POST"], tags=["admin"], dependencies=_auth,
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
