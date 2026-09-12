"""Monitoring report generation (Excel) and scheduled delivery.

A single Excel report covering ALL active monitored products is regenerated on
an interval (default hourly) and stored at ``data/reports/monitoring_latest.xlsx``.
Alert sources then receive *that already-built* report on their own frequency —
the report is never regenerated per delivery.

The workbook is built for analysts and has four sheets:

* **Товары**       — one enriched row per monitored product (current state plus
  min/max/avg/median price, discount and price change over the period).
* **История цен**  — the raw price time series (one row per snapshot) — the long
  fact table to pivot/chart in Excel or any BI tool.
* **Алерты**       — the log of detected price-change events (drop/rise).
* **Сводка**       — aggregate KPIs (per-marketplace discounts, alert counts,
  top movers).

The look-back window for the history/alerts sheets and the period statistics is
``notifications.report_period_days`` (default 30 days).
"""
from __future__ import annotations

import os
import statistics
import time
from datetime import datetime, timedelta

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


def report_period_days() -> int:
    """Look-back window (days) for history, alerts and period statistics."""
    try:
        days = int(settings.get("notifications", {}).get("report_period_days", 30))
    except (TypeError, ValueError):
        return 30
    return days if 1 <= days <= 365 else 30


def _discount_pct(original: float | None, current: float | None) -> float | None:
    """Discount of *current* against *original*, in percent (None when N/A)."""
    if not original or current is None or original <= 0:
        return None
    return round((original - current) / original * 100, 2)


def latest_report_path() -> str | None:
    """Path of the stored report, or None when none has been generated yet."""
    return _LATEST if os.path.exists(_LATEST) else None


def report_age_seconds() -> float | None:
    """Seconds since the stored report was generated, or None when absent."""
    if not os.path.exists(_LATEST):
        return None
    return time.time() - os.path.getmtime(_LATEST)


async def _collect_report_data(period_days: int) -> dict:
    """Gather everything the four report sheets need, in one DB round-trip.

    Returns a dict with ``products`` (enriched per-target rows), ``history`` (the
    price snapshots inside the window) and ``alerts`` (price-change events inside
    the window). All price statistics are computed from the in-window snapshots.
    """
    from sqlalchemy import select  # noqa: PLC0415

    from models.job import async_session  # noqa: PLC0415
    from models.parse_target import ParseTarget  # noqa: PLC0415
    from models.parsed_product import ParsedProduct  # noqa: PLC0415
    from models.price_snapshot import PriceAlert, PriceSnapshot  # noqa: PLC0415

    empty = {"products": [], "history": [], "alerts": []}
    if async_session is None:
        return empty

    cutoff = datetime.utcnow() - timedelta(days=period_days)
    async with async_session() as session:
        targets = (await session.execute(
            select(ParseTarget).where(ParseTarget.monitor_enabled.is_(True))
            .order_by(ParseTarget.created_at.asc())
        )).scalars().all()
        # Latest parsed product per URL — order ascending so the newest wins.
        prices = (await session.execute(
            select(ParsedProduct.url, ParsedProduct.title, ParsedProduct.sku,
                   ParsedProduct.current_price, ParsedProduct.original_price)
            .order_by(ParsedProduct.parsed_at.asc())
        )).all()
        snaps = (await session.execute(
            select(PriceSnapshot.url, PriceSnapshot.marketplace, PriceSnapshot.current_price,
                   PriceSnapshot.original_price, PriceSnapshot.available, PriceSnapshot.parsed_at)
            .where(PriceSnapshot.parsed_at >= cutoff)
            .order_by(PriceSnapshot.parsed_at.asc())
        )).all()
        alerts = (await session.execute(
            select(PriceAlert.title, PriceAlert.url, PriceAlert.marketplace, PriceAlert.kind,
                   PriceAlert.old_price, PriceAlert.new_price, PriceAlert.change_pct, PriceAlert.created_at)
            .where(PriceAlert.created_at >= cutoff)
            .order_by(PriceAlert.created_at.desc())
        )).all()

    price_by_url = {r[0]: {"title": r[1], "sku": r[2], "current_price": r[3], "original_price": r[4]} for r in prices}

    # Group in-window snapshot prices per URL (already ordered oldest → newest).
    series: dict[str, list[float]] = {}
    first_seen: dict[str, datetime] = {}
    for s in snaps:
        url, cur = s[0], s[2]
        first_seen.setdefault(url, s[5])
        if cur is not None:
            series.setdefault(url, []).append(cur)

    # Alert counts per URL by kind.
    drops: dict[str, int] = {}
    rises: dict[str, int] = {}
    for a in alerts:
        bucket = drops if a[3] == "drop" else rises
        bucket[a[1]] = bucket.get(a[1], 0) + 1

    products = []
    for t in targets:
        p = price_by_url.get(t.url, {})
        cur = p.get("current_price")
        orig = p.get("original_price")
        prices_window = series.get(t.url, [])
        lo = min(prices_window) if prices_window else None
        hi = max(prices_window) if prices_window else None
        avg = round(statistics.mean(prices_window), 2) if prices_window else None
        med = round(statistics.median(prices_window), 2) if prices_window else None
        spread = round((hi - lo) / lo * 100, 2) if lo and hi and lo > 0 else None
        first = prices_window[0] if prices_window else None
        change = round((cur - first) / first * 100, 2) if first and cur is not None and first > 0 else None
        products.append({
            "id": t.id,
            "title": p.get("title") or t.name or t.url,
            "sku": p.get("sku") or "",
            "marketplace": t.marketplace,
            "url": t.url,
            "current_price": cur,
            "original_price": orig,
            "discount_rub": round(orig - cur, 2) if orig is not None and cur is not None else None,
            "discount_pct": _discount_pct(orig, cur),
            "min_price": lo,
            "max_price": hi,
            "avg_price": avg,
            "median_price": med,
            "spread_pct": spread,
            "change_pct": change,
            "observations": len(prices_window),
            "alerts_drop": drops.get(t.url, 0),
            "alerts_rise": rises.get(t.url, 0),
            "first_seen": _fmt_dt(first_seen.get(t.url)),
            "last_checked_at": _fmt_dt(t.last_checked_at),
            "status": t.last_status or "",
        })

    history = [{
        "parsed_at": _fmt_dt(s[5]),
        "url": s[0],
        "title": (price_by_url.get(s[0]) or {}).get("title") or s[0],
        "marketplace": s[1],
        "current_price": s[2],
        "original_price": s[3],
        "discount_pct": _discount_pct(s[3], s[2]),
        "available": s[4] or "",
    } for s in snaps]

    alert_rows = [{
        "created_at": _fmt_dt(a[7]),
        "title": a[0] or (price_by_url.get(a[1]) or {}).get("title") or a[1],
        "marketplace": a[2],
        "kind": "Падение" if a[3] == "drop" else "Рост",
        "old_price": a[4],
        "new_price": a[5],
        "delta_rub": round(a[5] - a[4], 2) if a[4] is not None and a[5] is not None else None,
        "change_pct": round(a[6], 2),
        "url": a[1],
    } for a in alerts]

    return {"products": products, "history": history, "alerts": alert_rows}


def _fmt_dt(dt: datetime | None) -> str:
    return dt.strftime("%Y-%m-%d %H:%M") if dt else ""


def _style_header(ws, ncols: int) -> None:
    """Bold the header row (last appended) and widen columns a little."""
    from openpyxl.styles import Font  # noqa: PLC0415

    for cell in ws[ws.max_row]:
        cell.font = Font(bold=True)
    for i in range(1, ncols + 1):
        ws.column_dimensions[ws.cell(row=1, column=i).column_letter].width = 18


def _build_products_sheet(ws, products: list[dict]) -> None:
    headers = [
        "ID товара", "Товар", "Артикул", "Маркетплейс", "URL",
        "Текущая цена", "Старая цена", "Скидка, ₽", "Скидка, %",
        "Мин. цена", "Макс. цена", "Средняя цена", "Медианная цена",
        "Размах, %", "Изменение за период, %", "Наблюдений",
        "Алертов ↓", "Алертов ↑", "Первое наблюдение", "Последняя проверка", "Статус",
    ]
    ws.append(headers)
    _style_header(ws, len(headers))
    for r in products:
        ws.append([
            r["id"], r["title"], r["sku"], r["marketplace"], r["url"],
            r["current_price"], r["original_price"], r["discount_rub"], r["discount_pct"],
            r["min_price"], r["max_price"], r["avg_price"], r["median_price"],
            r["spread_pct"], r["change_pct"], r["observations"],
            r["alerts_drop"], r["alerts_rise"], r["first_seen"], r["last_checked_at"], r["status"],
        ])


def _build_history_sheet(ws, history: list[dict]) -> None:
    headers = ["Дата-время", "Товар", "Маркетплейс", "Текущая цена", "Старая цена", "Скидка, %", "Доступность"]
    ws.append(headers)
    _style_header(ws, len(headers))
    for r in history:
        ws.append([
            r["parsed_at"], r["title"], r["marketplace"],
            r["current_price"], r["original_price"], r["discount_pct"], r["available"],
        ])


def _build_alerts_sheet(ws, alerts: list[dict]) -> None:
    headers = ["Дата-время", "Товар", "Маркетплейс", "Тип", "Старая цена", "Новая цена", "Δ, ₽", "Δ, %", "URL"]
    ws.append(headers)
    _style_header(ws, len(headers))
    for r in alerts:
        ws.append([
            r["created_at"], r["title"], r["marketplace"], r["kind"],
            r["old_price"], r["new_price"], r["delta_rub"], r["change_pct"], r["url"],
        ])


def _build_summary_sheet(ws, products: list[dict], alerts: list[dict], period_days: int) -> None:
    from openpyxl.styles import Font  # noqa: PLC0415

    def row(*vals):
        ws.append(list(vals))

    def section(label: str):
        ws.append([label])
        ws[ws.max_row][0].font = Font(bold=True)

    section("Сводка мониторинга")
    row("Период наблюдения, дней", period_days)
    row("Товаров на мониторинге", len(products))
    row("Алертов за период", len(alerts))
    row("из них падений", sum(1 for a in alerts if a["kind"] == "Падение"))
    row("из них ростов", sum(1 for a in alerts if a["kind"] == "Рост"))
    decreased = sum(1 for p in products if p["change_pct"] is not None and p["change_pct"] < 0)
    measured = sum(1 for p in products if p["change_pct"] is not None)
    row("Доля подешевевших, %", round(decreased / measured * 100, 1) if measured else 0)
    ws.append([])

    # Per-marketplace discounts.
    section("Скидки по маркетплейсам")
    ws.append(["Маркетплейс", "Товаров", "Средняя скидка, %", "Медианная скидка, %"])
    ws[ws.max_row][0].font = Font(bold=True)
    by_mp: dict[str, list[float]] = {}
    counts: dict[str, int] = {}
    for p in products:
        mp = p["marketplace"] or "—"
        counts[mp] = counts.get(mp, 0) + 1
        if p["discount_pct"] is not None:
            by_mp.setdefault(mp, []).append(p["discount_pct"])
    for mp in sorted(counts):
        discs = by_mp.get(mp, [])
        row(mp, counts[mp],
            round(statistics.mean(discs), 1) if discs else "—",
            round(statistics.median(discs), 1) if discs else "—")
    ws.append([])

    # Top movers by price change over the period.
    moved = [p for p in products if p["change_pct"] is not None]
    cheapened = sorted(moved, key=lambda p: p["change_pct"])[:5]
    risen = sorted(moved, key=lambda p: p["change_pct"], reverse=True)[:5]

    section("Топ-5 подешевевших за период")
    ws.append(["Товар", "Маркетплейс", "Изменение, %", "Текущая цена"])
    ws[ws.max_row][0].font = Font(bold=True)
    for p in cheapened:
        row(p["title"], p["marketplace"], p["change_pct"], p["current_price"])
    ws.append([])

    section("Топ-5 подорожавших за период")
    ws.append(["Товар", "Маркетплейс", "Изменение, %", "Текущая цена"])
    ws[ws.max_row][0].font = Font(bold=True)
    for p in risen:
        row(p["title"], p["marketplace"], p["change_pct"], p["current_price"])

    ws.column_dimensions["A"].width = 32
    for col in ("B", "C", "D"):
        ws.column_dimensions[col].width = 20


async def generate_report() -> str | None:
    """Build the four-sheet monitoring Excel report and store it.

    Returns the path on success, or None when generation failed.
    """
    period = report_period_days()
    data = await _collect_report_data(period)
    try:
        from openpyxl import Workbook  # noqa: PLC0415

        os.makedirs(_REPORTS_DIR, exist_ok=True)
        wb = Workbook()

        ws_products = wb.active
        ws_products.title = "Товары"
        stamp = datetime.utcnow().strftime("%Y-%m-%d %H:%M")
        ws_products.append([f"Отчёт мониторинга цен — {stamp} UTC · период {period} дн."])
        ws_products.append([])
        _build_products_sheet(ws_products, data["products"])

        _build_history_sheet(wb.create_sheet("История цен"), data["history"])
        _build_alerts_sheet(wb.create_sheet("Алерты"), data["alerts"])
        _build_summary_sheet(wb.create_sheet("Сводка"), data["products"], data["alerts"], period)

        wb.save(_LATEST)
        logger.info(
            f"Monitoring report generated: {len(data['products'])} product(s), "
            f"{len(data['history'])} snapshot(s), {len(data['alerts'])} alert(s) -> {_LATEST}"
        )
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
