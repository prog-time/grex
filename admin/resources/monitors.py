"""Admin resource — Monitoring.

Endpoints to manage scheduled monitoring of parse targets, view price history,
and read detected price-change alerts. Re-parsing itself is driven by the
background scheduler (services.monitor_scheduler); these endpoints only manage
configuration and expose the collected data.
"""
from __future__ import annotations

from datetime import datetime

from fastapi import Request
from fastapi.responses import JSONResponse
from loguru import logger


class MonitorsRouter:
    """FastAPI router for /admin/monitors endpoints."""

    @staticmethod
    async def list_monitors(request: Request) -> JSONResponse:
        from sqlalchemy import select  # noqa: PLC0415

        from models.job import async_session  # noqa: PLC0415
        from models.parse_target import ParseTarget  # noqa: PLC0415
        from models.parsed_product import ParsedProduct  # noqa: PLC0415
        from services.monitoring import cfg, effective_interval  # noqa: PLC0415

        if async_session is None:
            return JSONResponse({"total": 0, "monitors": [], "default_interval_seconds": 3600})

        async with async_session() as session:
            targets = (await session.execute(select(ParseTarget))).scalars().all()
            prices = (await session.execute(
                select(ParsedProduct.url, ParsedProduct.current_price,
                       ParsedProduct.original_price, ParsedProduct.title)
            )).all()

        price_by_url = {row[0]: {"current_price": row[1], "original_price": row[2], "title": row[3]}
                        for row in prices}

        monitors = []
        for t in targets:
            p = price_by_url.get(t.url, {})
            monitors.append({
                "id": t.id,
                "name": t.name or "",
                "url": t.url,
                "marketplace": t.marketplace,
                "title": p.get("title"),
                "current_price": p.get("current_price"),
                "original_price": p.get("original_price"),
                "monitor_enabled": bool(t.monitor_enabled),
                "interval_seconds": t.interval_seconds,
                "effective_interval_seconds": effective_interval(t.interval_seconds),
                "next_run_at": t.next_run_at.isoformat() if t.next_run_at else None,
                "last_checked_at": t.last_checked_at.isoformat() if t.last_checked_at else None,
                "last_status": t.last_status,
            })

        return JSONResponse({
            "total": len(monitors),
            "monitors": monitors,
            "default_interval_seconds": int(cfg()["default_interval_seconds"]),
            "min_interval_seconds": int(cfg()["min_interval_seconds"]),
        })

    @staticmethod
    async def toggle_monitor(request: Request) -> JSONResponse:
        """Enable/disable monitoring and/or set the interval for a target.

        Body: {"id": str, "monitor_enabled": bool?, "interval_seconds": int|null?}.
        """
        from sqlalchemy import select  # noqa: PLC0415

        from models.job import async_session  # noqa: PLC0415
        from models.parse_target import ParseTarget  # noqa: PLC0415
        from services.monitoring import cfg  # noqa: PLC0415

        body = await request.json()
        target_id = body.get("id")
        if not target_id:
            return JSONResponse({"error": "id is required"}, status_code=422)

        interval = body.get("interval_seconds", "__absent__")
        if interval not in ("__absent__", None):
            try:
                interval = int(interval)
            except (TypeError, ValueError):
                return JSONResponse({"error": "interval_seconds must be an integer or null"}, status_code=422)
            if interval < int(cfg()["min_interval_seconds"]):
                return JSONResponse(
                    {"error": f"interval_seconds must be >= {int(cfg()['min_interval_seconds'])}"},
                    status_code=422,
                )

        if async_session is None:
            return JSONResponse({"error": "DB not initialised"}, status_code=503)

        async with async_session() as session:
            target = (await session.execute(
                select(ParseTarget).where(ParseTarget.id == target_id)
            )).scalar_one_or_none()
            if target is None:
                return JSONResponse({"error": "target not found"}, status_code=404)

            if "monitor_enabled" in body:
                target.monitor_enabled = bool(body["monitor_enabled"])
                # Enabling makes it due immediately so the user sees data fast.
                if target.monitor_enabled and target.next_run_at is None:
                    target.next_run_at = datetime.utcnow()
            if interval != "__absent__":
                target.interval_seconds = interval
            await session.commit()
            result = target.to_dict()

        return JSONResponse({"ok": True, "monitor": result})

    @staticmethod
    async def bulk(request: Request) -> JSONResponse:
        """Apply activation and/or interval changes to several targets at once.

        Body: {"ids": [str], "monitor_enabled"?: bool, "interval_seconds"?: int|null}.
        Either or both of ``monitor_enabled`` / ``interval_seconds`` may be present;
        only the provided fields are changed. ``interval_seconds: null`` resets a
        target to the global default interval.
        """
        from sqlalchemy import select  # noqa: PLC0415

        from models.job import async_session  # noqa: PLC0415
        from models.parse_target import ParseTarget  # noqa: PLC0415
        from services.monitoring import cfg  # noqa: PLC0415

        body = await request.json()
        ids = body.get("ids")
        if not isinstance(ids, list) or not ids:
            return JSONResponse({"error": "ids (non-empty list) is required"}, status_code=422)
        ids = [str(i) for i in ids]

        set_enabled = "monitor_enabled" in body
        enabled_val = bool(body.get("monitor_enabled")) if set_enabled else None

        set_interval = "interval_seconds" in body
        interval_val = None
        if set_interval:
            raw = body.get("interval_seconds")
            if raw is not None:
                try:
                    interval_val = int(raw)
                except (TypeError, ValueError):
                    return JSONResponse({"error": "interval_seconds must be an integer or null"}, status_code=422)
                min_interval = int(cfg()["min_interval_seconds"])
                if interval_val < min_interval:
                    return JSONResponse({"error": f"interval_seconds must be >= {min_interval}"}, status_code=422)

        if not set_enabled and not set_interval:
            return JSONResponse(
                {"error": "nothing to update — provide monitor_enabled and/or interval_seconds"},
                status_code=422,
            )

        if async_session is None:
            return JSONResponse({"error": "DB not initialised"}, status_code=503)

        now = datetime.utcnow()
        async with async_session() as session:
            targets = (await session.execute(
                select(ParseTarget).where(ParseTarget.id.in_(ids))
            )).scalars().all()
            for t in targets:
                if set_enabled:
                    t.monitor_enabled = enabled_val
                    if enabled_val and t.next_run_at is None:
                        t.next_run_at = now
                if set_interval:
                    t.interval_seconds = interval_val
            await session.commit()
            updated = len(targets)

        return JSONResponse({"ok": True, "updated": updated})

    @staticmethod
    async def history(request: Request) -> JSONResponse:
        """Return the price snapshot series for a target's URL (oldest → newest)."""
        from sqlalchemy import select  # noqa: PLC0415

        from models.job import async_session  # noqa: PLC0415
        from models.parse_target import ParseTarget  # noqa: PLC0415
        from models.price_snapshot import PriceSnapshot  # noqa: PLC0415

        target_id = request.query_params.get("target_id")
        if not target_id:
            return JSONResponse({"error": "target_id is required"}, status_code=422)
        try:
            limit = min(2000, max(1, int(request.query_params.get("limit", "500"))))
        except ValueError:
            limit = 500

        if async_session is None:
            return JSONResponse({"target_id": target_id, "points": []})

        async with async_session() as session:
            target = (await session.execute(
                select(ParseTarget.url).where(ParseTarget.id == target_id)
            )).scalar_one_or_none()
            if target is None:
                return JSONResponse({"error": "target not found"}, status_code=404)

            rows = (await session.execute(
                select(PriceSnapshot.parsed_at, PriceSnapshot.current_price, PriceSnapshot.original_price)
                .where(PriceSnapshot.url == target)
                .order_by(PriceSnapshot.parsed_at.desc())
                .limit(limit)
            )).all()

        points = [
            {"t": r[0].isoformat() if r[0] else None, "current_price": r[1], "original_price": r[2]}
            for r in reversed(rows)
        ]
        return JSONResponse({"target_id": target_id, "url": target, "points": points})

    @staticmethod
    async def alerts(request: Request) -> JSONResponse:
        """Return recent detected price-change alerts (newest first)."""
        from sqlalchemy import select  # noqa: PLC0415

        from models.job import async_session  # noqa: PLC0415
        from models.price_snapshot import PriceAlert  # noqa: PLC0415

        try:
            limit = min(500, max(1, int(request.query_params.get("limit", "100"))))
        except ValueError:
            limit = 100

        if async_session is None:
            return JSONResponse({"total": 0, "alerts": []})

        async with async_session() as session:
            rows = (await session.execute(
                select(PriceAlert).order_by(PriceAlert.created_at.desc()).limit(limit)
            )).scalars().all()
        return JSONResponse({"total": len(rows), "alerts": [a.to_dict() for a in rows]})

    @staticmethod
    async def run_now(request: Request) -> JSONResponse:
        """Manually trigger a monitoring tick for all currently-due targets."""
        try:
            from services.monitoring import run_due_once  # noqa: PLC0415
            summary = await run_due_once()
            return JSONResponse({"ok": True, **summary})
        except Exception as exc:  # noqa: BLE001
            logger.error(f"Manual monitor run failed: {exc}")
            return JSONResponse({"ok": False, "error": str(exc)}, status_code=500)


def register_monitors_routes(app) -> None:  # type: ignore[no-untyped-def]
    """Attach monitoring endpoints to *app*."""
    from fastapi import Depends  # noqa: PLC0415

    from admin.resources.auth import require_admin  # noqa: PLC0415
    _auth = [Depends(require_admin)]
    router = MonitorsRouter()

    app.add_api_route("/admin/monitors/api", router.list_monitors, methods=["GET"], tags=["admin"], dependencies=_auth)
    app.add_api_route("/admin/monitors/toggle", router.toggle_monitor, methods=["POST"], tags=["admin"], dependencies=_auth)
    app.add_api_route("/admin/monitors/bulk", router.bulk, methods=["POST"], tags=["admin"], dependencies=_auth)
    app.add_api_route("/admin/monitors/history", router.history, methods=["GET"], tags=["admin"], dependencies=_auth)
    app.add_api_route("/admin/monitors/alerts", router.alerts, methods=["GET"], tags=["admin"], dependencies=_auth)
    app.add_api_route("/admin/monitors/run", router.run_now, methods=["POST"], tags=["admin"], dependencies=_auth)
