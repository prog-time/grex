"""Monitoring report generation (Excel) and scheduled delivery.

A single Excel report covering ALL active monitored products is regenerated on
an interval (default hourly) and stored at ``data/reports/monitoring_latest.xlsx``.
Alert sources then receive *that already-built* report on their own frequency —
the report is never regenerated per delivery.
"""
from __future__ import annotations

import os
import time
from datetime import datetime

from loguru import logger

from config import data_path, settings

_REPORTS_DIR = os.path.join(data_path, "reports")
_LATEST = os.path.join(_REPORTS_DIR, "monitoring_latest.xlsx")

# Notification frequency keys → seconds (kept in sync with alert_sources._FREQUENCIES).
FREQUENCY_SECONDS = {
    "every_10m": 600,
    "hourly": 3600,
    "every_3h": 10800,
    "daily": 86400,
    "weekly": 604800,
}


def _report_interval() -> int:
    return int(settings.get("notifications", {}).get("report_interval_seconds", 3600))


def latest_report_path() -> str | None:
    """Path of the stored report, or None when none has been generated yet."""
    return _LATEST if os.path.exists(_LATEST) else None


def report_age_seconds() -> float | None:
    """Seconds since the stored report was generated, or None when absent."""
    if not os.path.exists(_LATEST):
        return None
    return time.time() - os.path.getmtime(_LATEST)


async def _active_monitored_rows() -> list[dict]:
    """Rows for the report: every active (monitor_enabled) target + latest price."""
    from sqlalchemy import select  # noqa: PLC0415

    from models.job import async_session  # noqa: PLC0415
    from models.parse_target import ParseTarget  # noqa: PLC0415
    from models.parsed_product import ParsedProduct  # noqa: PLC0415

    if async_session is None:
        return []

    async with async_session() as session:
        targets = (await session.execute(
            select(ParseTarget).where(ParseTarget.monitor_enabled.is_(True))
            .order_by(ParseTarget.created_at.asc())
        )).scalars().all()
        prices = (await session.execute(
            select(ParsedProduct.url, ParsedProduct.title,
                   ParsedProduct.current_price, ParsedProduct.original_price)
        )).all()

    price_by_url = {r[0]: {"title": r[1], "current_price": r[2], "original_price": r[3]} for r in prices}
    rows = []
    for t in targets:
        p = price_by_url.get(t.url, {})
        rows.append({
            "title": p.get("title") or t.name or t.url,
            "marketplace": t.marketplace,
            "url": t.url,
            "current_price": p.get("current_price"),
            "original_price": p.get("original_price"),
            "last_checked_at": t.last_checked_at.strftime("%Y-%m-%d %H:%M") if t.last_checked_at else "",
            "status": t.last_status or "",
        })
    return rows


async def generate_report() -> str | None:
    """Build the monitoring Excel report and store it. Returns the path or None."""
    rows = await _active_monitored_rows()
    try:
        from openpyxl import Workbook  # noqa: PLC0415

        os.makedirs(_REPORTS_DIR, exist_ok=True)
        wb = Workbook()
        ws = wb.active
        ws.title = "Мониторинг"
        ws.append([f"Отчёт мониторинга цен — сформирован {datetime.utcnow().strftime('%Y-%m-%d %H:%M')} UTC"])
        ws.append([])
        headers = ["Товар", "Маркетплейс", "URL", "Текущая цена", "Старая цена", "Последняя проверка", "Статус"]
        ws.append(headers)
        for r in rows:
            ws.append([
                r["title"], r["marketplace"], r["url"],
                r["current_price"], r["original_price"], r["last_checked_at"], r["status"],
            ])
        wb.save(_LATEST)
        logger.info(f"Monitoring report generated: {len(rows)} product(s) -> {_LATEST}")
        return _LATEST
    except Exception as exc:  # noqa: BLE001
        logger.error(f"Failed to generate monitoring report: {exc}")
        return None


async def ensure_report_fresh() -> None:
    """Regenerate the stored report when it is missing or older than the interval."""
    age = report_age_seconds()
    if age is None or age >= _report_interval():
        await generate_report()


async def _send_one(source: dict, path: str) -> bool:
    """Send the report file to a single alert source via its channel."""
    caption = "Отчёт мониторинга цен"
    if source["type"] == "telegram":
        from admin.resources.alert_sources import get_telegram_token  # noqa: PLC0415
        from services.telegram import send_document  # noqa: PLC0415

        token = get_telegram_token()
        if not token or not source.get("chat_id"):
            logger.warning(f"Report skip (no token/chat_id): {source['recipient']}")
            return False
        ok, err = await send_document(token, source["chat_id"], path, caption)
        if not ok:
            logger.warning(f"Telegram report failed for {source['recipient']}: {err}")
        return ok

    return False


async def deliver_due() -> int:
    """Send the stored report to every alert source whose frequency is due.

    Uses the already-built report (does not regenerate it). Updates each source's
    ``last_sent_at`` only on a successful send. Returns the number sent.
    """
    path = latest_report_path()
    if not path:
        return 0

    from sqlalchemy import select, update  # noqa: PLC0415

    from models.alert_source import AlertSource  # noqa: PLC0415
    from models.job import async_session  # noqa: PLC0415
    if async_session is None:
        return 0

    now = datetime.utcnow()
    async with async_session() as session:
        sources = (await session.execute(select(AlertSource))).scalars().all()
        due = [
            {"id": s.id, "type": s.type, "recipient": s.recipient, "chat_id": s.chat_id}
            for s in sources
            if FREQUENCY_SECONDS.get(s.frequency)
            and (s.last_sent_at is None or (now - s.last_sent_at).total_seconds() >= FREQUENCY_SECONDS[s.frequency])
        ]

    sent = 0
    for source in due:
        if await _send_one(source, path):
            async with async_session() as session:
                await session.execute(
                    update(AlertSource).where(AlertSource.id == source["id"]).values(last_sent_at=datetime.utcnow())
                )
                await session.commit()
            sent += 1

    if sent:
        logger.info(f"Monitoring report delivered to {sent} source(s)")
    return sent
