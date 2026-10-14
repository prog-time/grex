"""Price monitors of the public API (``/v1/monitors``, ``/v1/alerts``).

An API monitor is a :class:`ParseTarget` with ``api_key_id`` set: the regular
monitoring scheduler (services/monitoring.py) re-parses it, records price
snapshots and logs alerts exactly as for admin targets. This module adds the
per-key ownership layer on top — every query is scoped to one API key, and
admin views filter API-owned rows out (see models/parse_target.py).
"""
from __future__ import annotations

from datetime import datetime

from loguru import logger

from utils.locks import key_lock

# Monitors one API key may own — every monitor spends the marketplace's rate
# limit, which the admin panel's own monitoring shares.
MAX_MONITORS_PER_KEY = 500


class MonitorError(Exception):
    """Rejected request; ``status`` is the HTTP status to answer with."""

    def __init__(self, status: int, message: str) -> None:
        super().__init__(message)
        self.status = status


def _session_factory():  # type: ignore[no-untyped-def]
    from models.job import async_session  # noqa: PLC0415

    if async_session is None:
        raise RuntimeError("DB not initialised")
    return async_session


def _check_interval(interval_seconds: int | None) -> None:
    from services.monitoring import cfg  # noqa: PLC0415

    minimum = int(cfg()["min_interval_seconds"])
    if interval_seconds is not None and interval_seconds < minimum:
        raise MonitorError(422, f"interval_seconds must be >= {minimum}")


async def _latest_prices(session, target_ids: list[str]) -> dict[str, dict]:  # type: ignore[no-untyped-def]
    """Most recent snapshot per target id."""
    from sqlalchemy import func, select  # noqa: PLC0415

    from models.price_snapshot import PriceSnapshot  # noqa: PLC0415

    if not target_ids:
        return {}
    latest = (
        select(PriceSnapshot.target_id, func.max(PriceSnapshot.parsed_at).label("at"))
        .where(PriceSnapshot.target_id.in_(target_ids))
        .group_by(PriceSnapshot.target_id)
        .subquery()
    )
    rows = (await session.execute(
        select(PriceSnapshot.target_id, PriceSnapshot.current_price,
               PriceSnapshot.original_price, PriceSnapshot.parsed_at)
        .join(latest, (PriceSnapshot.target_id == latest.c.target_id) & (PriceSnapshot.parsed_at == latest.c.at))
    )).all()
    return {r[0]: {"current_price": r[1], "original_price": r[2], "price_updated_at": r[3]} for r in rows}


def _to_dict(target, price: dict | None) -> dict:  # type: ignore[no-untyped-def]
    from services.monitoring import effective_interval  # noqa: PLC0415

    price = price or {}
    iso = lambda d: d.isoformat() if d else None  # noqa: E731
    return {
        "id": target.id,
        "url": target.url,
        "marketplace": target.marketplace,
        "name": target.name,
        "active": bool(target.monitor_enabled),
        "interval_seconds": effective_interval(target.interval_seconds),
        "current_price": price.get("current_price"),
        "original_price": price.get("original_price"),
        "price_updated_at": iso(price.get("price_updated_at")),
        "last_checked_at": iso(target.last_checked_at),
        "last_status": target.last_status,
        "next_run_at": iso(target.next_run_at) if target.monitor_enabled else None,
        "created_at": iso(target.created_at),
    }


async def _owned(session, api_key_id: str, monitor_id: str):  # type: ignore[no-untyped-def]
    from sqlalchemy import select  # noqa: PLC0415

    from models.parse_target import ParseTarget  # noqa: PLC0415

    target = (await session.execute(
        select(ParseTarget).where(ParseTarget.id == monitor_id, ParseTarget.api_key_id == api_key_id)
    )).scalar_one_or_none()
    if target is None:
        raise MonitorError(404, "Monitor not found")
    return target


async def create_monitor(api_key_id: str, url: str, interval_seconds: int | None, name: str | None) -> dict:
    from sqlalchemy import func, select  # noqa: PLC0415

    from crawlers.registry import CrawlerRegistry  # noqa: PLC0415
    from models.parse_target import ParseTarget  # noqa: PLC0415

    _check_interval(interval_seconds)
    marketplace = CrawlerRegistry.detect_marketplace(url)
    url = CrawlerRegistry.get(marketplace).normalize_url(url)

    # The duplicate/limit checks and the insert must not interleave between requests of one key.
    async with key_lock("api_monitors", api_key_id), _session_factory()() as session:
        existing = (await session.execute(
            select(ParseTarget.id).where(ParseTarget.api_key_id == api_key_id, ParseTarget.url == url)
        )).scalar_one_or_none()
        if existing is not None:
            raise MonitorError(409, f"This URL is already monitored: {existing}")
        count = (await session.execute(
            select(func.count()).select_from(ParseTarget).where(ParseTarget.api_key_id == api_key_id)
        )).scalar_one()
        if count >= MAX_MONITORS_PER_KEY:
            raise MonitorError(429, f"Monitor limit reached ({MAX_MONITORS_PER_KEY} per API key)")

        now = datetime.utcnow()
        target = ParseTarget(
            api_key_id=api_key_id,
            name=name or None,
            url=url,
            marketplace=marketplace,
            enabled=True,
            created_at=now,
            monitor_enabled=True,
            interval_seconds=interval_seconds,
            next_run_at=now,  # due right away so the first price shows up quickly
        )
        session.add(target)
        await session.commit()
        data = _to_dict(target, None)
    logger.info(f"API monitor {data['id']} created for key {api_key_id}: {url}")
    return data


async def list_monitors(api_key_id: str) -> list[dict]:
    from sqlalchemy import select  # noqa: PLC0415

    from models.parse_target import ParseTarget  # noqa: PLC0415

    async with _session_factory()() as session:
        targets = (await session.execute(
            select(ParseTarget).where(ParseTarget.api_key_id == api_key_id)
            .order_by(ParseTarget.created_at.desc())
        )).scalars().all()
        prices = await _latest_prices(session, [t.id for t in targets])
    return [_to_dict(t, prices.get(t.id)) for t in targets]


async def get_monitor(api_key_id: str, monitor_id: str) -> dict:
    async with _session_factory()() as session:
        target = await _owned(session, api_key_id, monitor_id)
        prices = await _latest_prices(session, [target.id])
    return _to_dict(target, prices.get(target.id))


async def update_monitor(api_key_id: str, monitor_id: str, changes: dict) -> dict:
    """Apply *changes* — any of ``active``, ``interval_seconds`` (None = default), ``name``."""
    if "interval_seconds" in changes:
        _check_interval(changes["interval_seconds"])

    async with _session_factory()() as session:
        target = await _owned(session, api_key_id, monitor_id)
        if "name" in changes:
            target.name = changes["name"] or None
        if "interval_seconds" in changes:
            target.interval_seconds = changes["interval_seconds"]
        if "active" in changes:
            target.monitor_enabled = bool(changes["active"])
            if target.monitor_enabled and target.next_run_at is None:
                target.next_run_at = datetime.utcnow()
        await session.commit()
        prices = await _latest_prices(session, [target.id])
        return _to_dict(target, prices.get(target.id))


async def delete_monitor(api_key_id: str, monitor_id: str) -> None:
    """Delete the monitor together with its price history and alerts."""
    async with _session_factory()() as session:
        target = await _owned(session, api_key_id, monitor_id)
        await _delete_targets(session, [target.id])
        await session.commit()
    logger.info(f"API monitor {monitor_id} deleted")


async def _delete_targets(session, target_ids: list[str]) -> None:  # type: ignore[no-untyped-def]
    from sqlalchemy import delete  # noqa: PLC0415

    from models.parse_target import ParseTarget  # noqa: PLC0415
    from models.price_snapshot import PriceAlert, PriceSnapshot  # noqa: PLC0415

    if not target_ids:
        return
    await session.execute(delete(PriceSnapshot).where(PriceSnapshot.target_id.in_(target_ids)))
    await session.execute(delete(PriceAlert).where(PriceAlert.target_id.in_(target_ids)))
    await session.execute(delete(ParseTarget).where(ParseTarget.id.in_(target_ids)))


async def price_history(api_key_id: str, monitor_id: str, limit: int) -> list[dict]:
    """Price points of the monitor, oldest → newest (the latest *limit*)."""
    from sqlalchemy import select  # noqa: PLC0415

    from models.price_snapshot import PriceSnapshot  # noqa: PLC0415

    async with _session_factory()() as session:
        await _owned(session, api_key_id, monitor_id)
        rows = (await session.execute(
            select(PriceSnapshot.parsed_at, PriceSnapshot.current_price, PriceSnapshot.original_price)
            .where(PriceSnapshot.target_id == monitor_id)
            .order_by(PriceSnapshot.parsed_at.desc())
            .limit(limit)
        )).all()
    return [
        {"at": r[0].isoformat() if r[0] else None, "current_price": r[1], "original_price": r[2]}
        for r in reversed(rows)
    ]


async def list_alerts(api_key_id: str, since: datetime | None, limit: int) -> list[dict]:
    """Price changes on the key's monitors, newest first."""
    from sqlalchemy import select  # noqa: PLC0415

    from models.parse_target import ParseTarget  # noqa: PLC0415
    from models.price_snapshot import PriceAlert  # noqa: PLC0415

    owned = select(ParseTarget.id).where(ParseTarget.api_key_id == api_key_id)
    query = select(PriceAlert).where(PriceAlert.target_id.in_(owned))
    if since is not None:
        query = query.where(PriceAlert.created_at > since)
    async with _session_factory()() as session:
        rows = (await session.execute(
            query.order_by(PriceAlert.created_at.desc()).limit(limit)
        )).scalars().all()
    return [
        {
            "id": a.id,
            "monitor_id": a.target_id,
            "url": a.url,
            "marketplace": a.marketplace,
            "title": a.title,
            "kind": a.kind,
            "old_price": a.old_price,
            "new_price": a.new_price,
            "change_pct": a.change_pct,
            "created_at": a.created_at.isoformat() if a.created_at else None,
        }
        for a in rows
    ]


async def pause_for_key(session, api_key_id: str) -> None:  # type: ignore[no-untyped-def]
    """Stop all monitors of a revoked key (history is kept)."""
    from sqlalchemy import update  # noqa: PLC0415

    from models.parse_target import ParseTarget  # noqa: PLC0415

    await session.execute(
        update(ParseTarget).where(ParseTarget.api_key_id == api_key_id).values(monitor_enabled=False)
    )


async def delete_for_key(session, api_key_id: str) -> None:  # type: ignore[no-untyped-def]
    """Remove all monitors of a deleted key, with their history and alerts."""
    from sqlalchemy import select  # noqa: PLC0415

    from models.parse_target import ParseTarget  # noqa: PLC0415

    ids = (await session.execute(
        select(ParseTarget.id).where(ParseTarget.api_key_id == api_key_id)
    )).scalars().all()
    await _delete_targets(session, list(ids))
