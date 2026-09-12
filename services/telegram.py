"""Minimal Telegram Bot API helpers: resolve a chat target and verify delivery.

Telegram bots can only message a numeric ``chat_id``. A bare ``@username`` of a
*private* user cannot be resolved through the Bot API unless that user has
already messaged the bot (then the id is available via ``getUpdates``). Public
channels/groups resolve through ``getChat``. ``can_send`` probes deliverability
with a transient ``sendChatAction`` (no visible message is sent).
"""
from __future__ import annotations

from loguru import logger

_API = "https://api.telegram.org/bot{token}/{method}"


async def _call(token: str, method: str, params: dict, timeout: int = 10) -> dict:
    """POST to the Bot API and return the parsed JSON (or an ok=False dict)."""
    from curl_cffi.requests import AsyncSession  # noqa: PLC0415
    try:
        async with AsyncSession() as session:
            resp = await session.post(_API.format(token=token, method=method), json=params, timeout=timeout)
        return resp.json()
    except Exception as exc:  # noqa: BLE001
        logger.warning(f"Telegram {method} request failed: {exc}")
        return {"ok": False, "description": f"сеть недоступна ({exc})"}


async def resolve_chat_id(token: str, identifier: str) -> tuple[int | None, str | None]:
    """Resolve *identifier* (numeric id or @username) to a chat_id.

    Returns ``(chat_id, None)`` on success, ``(None, error_message)`` otherwise.
    """
    ident = (identifier or "").strip()
    if not ident:
        return None, "Пустой получатель"

    # A numeric chat id (negative for groups/channels) is used as-is.
    if ident.lstrip("-").isdigit():
        return int(ident), None

    uname = ident.lstrip("@")

    # Public channel / group: getChat accepts @username.
    chat = await _call(token, "getChat", {"chat_id": "@" + uname})
    if chat.get("ok"):
        return chat["result"]["id"], None

    # Private user: the only Bot-API way is to find a chat where this user has
    # written to the bot (so we can read their chat_id from recent updates).
    updates = await _call(token, "getUpdates", {"limit": 100, "timeout": 0})
    if updates.get("ok"):
        for upd in reversed(updates.get("result", [])):
            msg = upd.get("message") or upd.get("edited_message") or {}
            frm = msg.get("from") or {}
            if (frm.get("username") or "").lower() == uname.lower():
                chat_id = (msg.get("chat") or {}).get("id")
                if chat_id is not None:
                    return chat_id, None
    elif "webhook" in (updates.get("description") or "").lower():
        return None, "У бота установлен webhook — снимите его, чтобы определять chat_id по username."

    return None, (
        f"Не удалось определить chat_id для @{uname}. Попросите пользователя написать боту "
        "команду /start, либо укажите числовой chat_id."
    )


async def can_send(token: str, chat_id: int | str) -> tuple[bool, str | None]:
    """Return (True, None) if the bot can message *chat_id*, else (False, reason)."""
    res = await _call(token, "sendChatAction", {"chat_id": chat_id, "action": "typing"})
    if res.get("ok"):
        return True, None
    return False, res.get("description") or "бот не может отправить сообщение этому получателю"


async def send_document(token: str, chat_id: int | str, file_path: str, caption: str = "") -> tuple[bool, str | None]:
    """Send a file to *chat_id* via sendDocument. Returns (True, None) or (False, error)."""
    import os  # noqa: PLC0415

    from curl_cffi import CurlMime  # noqa: PLC0415
    from curl_cffi.requests import AsyncSession  # noqa: PLC0415

    mp = None
    try:
        # curl_cffi needs a CurlMime for multipart uploads (it has no requests-style files=).
        mp = CurlMime()
        mp.addpart(
            name="document",
            filename=os.path.basename(file_path),
            content_type="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
            local_path=file_path,
        )
        params = {"chat_id": str(chat_id)}
        if caption:
            params["caption"] = caption
        async with AsyncSession() as session:
            resp = await session.post(
                _API.format(token=token, method="sendDocument"), params=params, multipart=mp, timeout=60,
            )
        res = resp.json()
        if res.get("ok"):
            return True, None
        return False, res.get("description") or "ошибка отправки документа"
    except Exception as exc:  # noqa: BLE001
        logger.warning(f"Telegram sendDocument failed: {exc}")
        return False, f"ошибка: {exc}"
    finally:
        if mp is not None:
            try:
                mp.close()
            except Exception:  # noqa: BLE001
                pass
