"""Email delivery for monitoring reports (SMTP).

Configuration lives under ``notifications.smtp`` in settings.yml:
    host, port (default 587), username, password, from, use_tls (default true),
    use_ssl (default false). When ``host`` is unset, sending is skipped with a
    clear "SMTP не настроен" result so mail sources degrade gracefully.
"""
from __future__ import annotations

import asyncio

from loguru import logger

from config import settings


def _smtp_cfg() -> dict:
    """SMTP config with the password decrypted (from ``password_enc``)."""
    cfg = dict(settings.get("notifications", {}).get("smtp", {}) or {})
    enc = cfg.get("password_enc")
    if enc:
        from services.secrets import decrypt_secret
        cfg["password"] = decrypt_secret(enc)
    return cfg


async def send_report_email(to_addr: str, subject: str, body: str, file_path: str) -> tuple[bool, str | None]:
    """Send *file_path* as an .xlsx attachment to *to_addr*. (False, reason) on failure."""
    cfg = _smtp_cfg()
    if not cfg.get("host"):
        return False, "SMTP не настроен"
    loop = asyncio.get_event_loop()
    return await loop.run_in_executor(None, _send_sync, cfg, to_addr, subject, body, file_path)


def _send_sync(cfg: dict, to_addr: str, subject: str, body: str, file_path: str) -> tuple[bool, str | None]:
    import os
    import smtplib
    import ssl
    from email.message import EmailMessage

    try:
        msg = EmailMessage()
        msg["From"] = cfg.get("from") or cfg.get("username") or "grex@localhost"
        msg["To"] = to_addr
        msg["Subject"] = subject
        msg.set_content(body)
        with open(file_path, "rb") as fh:
            msg.add_attachment(
                fh.read(),
                maintype="application",
                subtype="vnd.openxmlformats-officedocument.spreadsheetml.sheet",
                filename=os.path.basename(file_path),
            )

        host = cfg["host"]
        port = int(cfg.get("port", 587))
        if cfg.get("use_ssl"):
            server = smtplib.SMTP_SSL(host, port, timeout=30, context=ssl.create_default_context())
        else:
            server = smtplib.SMTP(host, port, timeout=30)
            if cfg.get("use_tls", True):
                server.starttls(context=ssl.create_default_context())
        try:
            if cfg.get("username"):
                server.login(cfg["username"], cfg.get("password", ""))
            server.send_message(msg)
        finally:
            server.quit()
        return True, None
    except Exception as exc:  # noqa: BLE001
        logger.warning(f"SMTP send failed: {exc}")
        return False, str(exc)
