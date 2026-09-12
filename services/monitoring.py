"""Product monitoring — scheduled re-parsing, price history, change detection.

The scheduler (services/monitor_scheduler.py) calls :func:`run_due_once`
periodically. For every monitored target whose ``next_run_at`` has elapsed it:

  1. re-parses the URL through :class:`ParsingService` (so per-source rate
     limits, proxy health and the browser cap all apply),
  2. upserts the latest product state (reusing the targets upsert),
  3. appends a :class:`PriceSnapshot` to the price history,
  4. compares the new price with the previous snapshot and logs a
     :class:`PriceAlert` when the change crosses the configured percentage
     threshold (drop or rise),
  5. reschedules the target's ``next_run_at`` (even on failure).

Notification *delivery* (Telegram/webhook/email) is intentionally out of scope
for this phase — alerts are persisted and surfaced in the admin panel.
"""
from __future__ import annotations

import asyncio
import random
from datetime import datetime, timedelta

from loguru import logger

from config import settings

_DEFAULTS = {
    "enabled": True,
    "default_interval_seconds": 3600,   # 1h when a target has no explicit interval
    "min_interval_seconds": 300,        # floor, so nobody schedules a 5s hammer
    "tick_seconds": 30,                 # how often the scheduler wakes up
    "max_concurrent": 3,                # parses run at once per tick
    "batch_limit": 50,                  # max targets handled per tick
    "drop_percent": 5.0,                # |Δ| ≥ this (price down) → alert
    "rise_percent": 5.0,                # |Δ| ≥ this (price up) → alert
    "jitter_seconds": 30,               # spread reschedules to avoid thundering herd
    "history_retention_days": 90,       # snapshots older than this are purged
}


def cfg() -> dict:
    raw = settings.get("monitoring", {}) or {}
    return {key: raw.get(key, default) for key, default in _DEFAULTS.items()}


# ---------------------------------------------------------------------------
# Pure helpers (no I/O — unit-tested directly)
# ---------------------------------------------------------------------------

def pct_change(old: float | None, new: float | None) -> float | None:
    """Signed percentage change from *old* to *new* (negative = price drop).

    Returns None when it can't be computed (missing values or zero baseline).
    """
    if old is None or new is None or old == 0:
        return None
    return (new - old) / old * 100.0


def classify_change(
    old: float | None,
    new: float | None,
    drop_percent: float,
    rise_percent: float,
) -> str | None:
    """Return "drop", "rise", or None based on the configured thresholds."""
    change = pct_change(old, new)
    if change is None:
        return None
    if change <= -abs(drop_percent):
        return "drop"
    if change >= abs(rise_percent):
        return "rise"
    return None


def effective_interval(interval_seconds: int | None) -> int:
    """Resolve a target's interval: explicit value or global default, floored."""
    c = cfg()
    value = interval_seconds if interval_seconds else int(c["default_interval_seconds"])
    return max(int(c["min_interval_seconds"]), int(value))


def compute_next_run(now: datetime, interval_seconds: int, jitter_seconds: int = 0) -> datetime:
    """When the target should next run: now + interval (+ a little jitter)."""
    jitter = random.uniform(0, jitter_seconds) if jitter_seconds > 0 else 0.0
    return now + timedelta(seconds=interval_seconds + jitter)


# ---------------------------------------------------------------------------
# DB operations
# ---------------------------------------------------------------------------

async def _previous_price(session, url: str) -> float | None:
    from sqlalchemy import select  # noqa: PLC0415

    from models.price_snapshot import PriceSnapshot  # noqa: PLC0415
    row = (await session.execute(
        select(PriceSnapshot.current_price)
        .where(PriceSnapshot.url == url)
        .order_by(PriceSnapshot.parsed_at.desc())
        .limit(1)
    )).first()
    return row[0] if row else None


async def _record_snapshot(session, *, target_id, url, marketplace, current_price, original_price) -> None:
    from models.price_snapshot import PriceSnapshot  # noqa: PLC0415
    session.add(PriceSnapshot(
        target_id=target_id,
        url=url,
        marketplace=marketplace,
        current_price=current_price,
        original_price=original_price,
        parsed_at=datetime.utcnow(),
    ))


async def _maybe_log_alert(session, *, target_id, url, marketplace, title, old_price, new_price) -> str | None:
    c = cfg()
    kind = classify_change(old_price, new_price, c["drop_percent"], c["rise_percent"])
    if kind is None:
        return None
    from models.price_snapshot import PriceAlert  # noqa: PLC0415
    change = pct_change(old_price, new_price) or 0.0
    session.add(PriceAlert(
        target_id=target_id,
        url=url,
        marketplace=marketplace,
        title=title,
        kind=kind,
        old_price=old_price,
        new_price=new_price,
        change_pct=change,
        created_at=datetime.utcnow(),
    ))
    logger.info(f"Price {kind} {change:+.1f}% for {url} ({old_price} → {new_price})")
    return kind


async def _due_targets(session, limit: int) -> list[dict]:
    from sqlalchemy import or_, select  # noqa: PLC0415

    from models.parse_target import ParseTarget  # noqa: PLC0415
    now = datetime.utcnow()
    rows = (await session.execute(
        select(ParseTarget)
        .where(ParseTarget.monitor_enabled.is_(True))
        .where(or_(ParseTarget.next_run_at.is_(None), ParseTarget.next_run_at <= now))
        .limit(limit)
    )).scalars().all()
    # Detach to plain dicts so we don't hold ORM objects across the parse.
    return [
        {"id": t.id, "url": t.url, "marketplace": t.marketplace, "interval_seconds": t.interval_seconds}
        for t in rows
    ]


async def _reschedule(target_id: str, *, status: str) -> None:
    from sqlalchemy import update  # noqa: PLC0415

    from models.job import async_session  # noqa: PLC0415
    from models.parse_target import ParseTarget  # noqa: PLC0415
    if async_session is None:
        return
    now = datetime.utcnow()
    async with async_session() as session:
        # Read the (possibly just-edited) interval for an accurate next run.
        from sqlalchemy import select  # noqa: PLC0415
        interval = (await session.execute(
            select(ParseTarget.interval_seconds).where(ParseTarget.id == target_id)
        )).scalar_one_or_none()
        next_run = compute_next_run(now, effective_interval(interval), int(cfg()["jitter_seconds"]))
        await session.execute(
            update(ParseTarget).where(ParseTarget.id == target_id).values(
                last_checked_at=now, last_status=status, next_run_at=next_run,
            )
        )
        await session.commit()


async def _check_one(target: dict) -> str:
    """Parse one target, snapshot it, detect a change. Returns the status."""
    from models.job import async_session  # noqa: PLC0415
    from services.parsing import ParsingService  # noqa: PLC0415

    url = target["url"]
    try:
        # Capture the previous price BEFORE the new parse overwrites state.
        async with async_session() as session:
            prev = await _previous_price(session, url)

        product = await ParsingService().parse_product(url)
        data = product.to_dict()
        new_price = float(data["current_price"]) if data.get("current_price") else None
        orig_price = float(data["original_price"]) if data.get("original_price") else None

        # Reuse the targets upsert so the "latest product" view stays in sync.
        import json as _json  # noqa: PLC0415

        from admin.resources.targets import _upsert_parsed_product  # noqa: PLC0415
        await _upsert_parsed_product(
            session_id=None, target_id=target["id"], url=url,
            marketplace=target["marketplace"], data=data, json_module=_json,
        )

        async with async_session() as session:
            await _record_snapshot(
                session, target_id=target["id"], url=url, marketplace=target["marketplace"],
                current_price=new_price, original_price=orig_price,
            )
            await _maybe_log_alert(
                session, target_id=target["id"], url=url, marketplace=target["marketplace"],
                title=data.get("title"), old_price=prev, new_price=new_price,
            )
            await session.commit()
        return "ok"
    except Exception as exc:  # noqa: BLE001
        logger.warning(f"monitor check failed for {url}: {exc}")
        return "failed"


async def run_due_once() -> dict:
    """Process all due monitors. Returns a small summary for logging/metrics."""
    c = cfg()
    if not c["enabled"]:
        return {"checked": 0, "skipped": "disabled"}

    from models.job import async_session  # noqa: PLC0415
    if async_session is None:
        return {"checked": 0, "skipped": "no_db"}

    async with async_session() as session:
        due = await _due_targets(session, int(c["batch_limit"]))
    if not due:
        return {"checked": 0}

    semaphore = asyncio.Semaphore(max(1, int(c["max_concurrent"])))

    async def _process(target: dict) -> str:
        async with semaphore:
            status = await _check_one(target)
        await _reschedule(target["id"], status=status)
        return status

    results = await asyncio.gather(*(_process(t) for t in due), return_exceptions=True)
    ok = sum(1 for r in results if r == "ok")
    failed = len(results) - ok
    logger.info(f"Monitor tick: checked {len(due)} ({ok} ok, {failed} failed)")
    return {"checked": len(due), "ok": ok, "failed": failed}


async def purge_old_snapshots() -> int:
    """Delete snapshots older than the configured retention window."""
    from sqlalchemy import delete  # noqa: PLC0415

    from models.job import async_session  # noqa: PLC0415
    from models.price_snapshot import PriceSnapshot  # noqa: PLC0415
    if async_session is None:
        return 0
    cutoff = datetime.utcnow() - timedelta(days=int(cfg()["history_retention_days"]))
    async with async_session() as session:
        result = await session.execute(
            delete(PriceSnapshot).where(PriceSnapshot.parsed_at < cutoff)
        )
        await session.commit()
        return result.rowcount or 0
