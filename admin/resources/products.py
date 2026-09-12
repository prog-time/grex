"""Admin resource — Parsed Products."""
from __future__ import annotations

import csv
import io
import json
import re
from pathlib import Path

from fastapi import Request
from fastapi.responses import FileResponse, JSONResponse, Response, StreamingResponse
from loguru import logger

# resources/{marketplace}/{sku}/ — where the image worker saves downloaded files.
_RESOURCES_BASE = Path(__file__).resolve().parent.parent.parent / "resources"


def _safe_segment(value: str) -> str:
    """Sanitise a path segment (matches the image downloader's naming)."""
    return re.sub(r"[^\w.\-]", "_", value or "")

_EXPORT_COLUMNS = [
    "title", "sku", "marketplace", "original_price", "current_price",
    "url", "images", "parsed_at",
]


class ProductsRouter:

    @staticmethod
    async def product_image(request: Request) -> Response:
        """Serve a locally-downloaded product image (``resources/{mp}/{sku}/{n}.*``).

        Used as an ``<img>`` fallback when a marketplace CDN blocks hot-linking
        from the browser (e.g. citilink's Qrator-protected CDN returns 403), so
        the already-downloaded local copy is shown instead.
        """
        marketplace = _safe_segment(request.query_params.get("marketplace", ""))
        sku = _safe_segment(request.query_params.get("sku", ""))
        try:
            n = max(1, int(request.query_params.get("n", "1")))
        except ValueError:
            n = 1
        if not marketplace or not sku:
            return Response(status_code=404)

        directory = _RESOURCES_BASE / marketplace / sku
        matches = sorted(directory.glob(f"{n}.*"))
        if matches and matches[0].is_file():
            return FileResponse(matches[0])
        return Response(status_code=404)

    @staticmethod
    async def list_products(request: Request) -> JSONResponse:
        try:
            from sqlalchemy import select  # noqa: PLC0415

            from models.job import async_session  # noqa: PLC0415
            from models.parsed_product import ParsedProduct  # noqa: PLC0415

            if async_session is None:
                return JSONResponse({"error": "DB not initialised"}, status_code=503)

            marketplace = request.query_params.get("marketplace", "")
            limit = min(int(request.query_params.get("limit", 200)), 500)

            async with async_session() as session:
                q = select(ParsedProduct).order_by(ParsedProduct.parsed_at.desc()).limit(limit)
                if marketplace:
                    q = q.where(ParsedProduct.marketplace == marketplace)
                rows = (await session.execute(q)).scalars().all()

                mp_rows = (await session.execute(
                    select(ParsedProduct.marketplace).distinct()
                )).scalars().all()

            products = [r.to_dict() for r in rows]
            marketplaces = sorted([m for m in mp_rows if m])
            return JSONResponse({"products": products, "marketplaces": marketplaces, "total": len(products)})
        except Exception as exc:  # noqa: BLE001
            logger.error(f"Failed to list products: {exc}")
            return JSONResponse({"error": str(exc)}, status_code=500)

    @staticmethod
    async def delete_product(request: Request) -> JSONResponse:
        try:
            from sqlalchemy import delete  # noqa: PLC0415

            from models.job import async_session  # noqa: PLC0415
            from models.parsed_product import ParsedProduct  # noqa: PLC0415

            if async_session is None:
                return JSONResponse({"error": "DB not initialised"}, status_code=503)

            body = await request.json()
            product_id: str = body.get("id", "").strip()
            if not product_id:
                return JSONResponse({"error": "id is required"}, status_code=422)

            async with async_session() as session:
                result = await session.execute(
                    delete(ParsedProduct).where(ParsedProduct.id == product_id)
                )
                await session.commit()

            if result.rowcount == 0:  # type: ignore[attr-defined]
                return JSONResponse({"error": "Product not found"}, status_code=404)
            return JSONResponse({"deleted": product_id})
        except Exception as exc:  # noqa: BLE001
            logger.error(f"Failed to delete product: {exc}")
            return JSONResponse({"error": str(exc)}, status_code=500)

    @staticmethod
    async def clear_products(request: Request) -> JSONResponse:
        try:
            from sqlalchemy import delete  # noqa: PLC0415

            from models.job import async_session  # noqa: PLC0415
            from models.parsed_product import ParsedProduct  # noqa: PLC0415

            if async_session is None:
                return JSONResponse({"error": "DB not initialised"}, status_code=503)

            async with async_session() as session:
                await session.execute(delete(ParsedProduct))
                await session.commit()
            return JSONResponse({"ok": True})
        except Exception as exc:  # noqa: BLE001
            logger.error(f"Failed to clear products: {exc}")
            return JSONResponse({"error": str(exc)}, status_code=500)

    @staticmethod
    async def delete_many(request: Request) -> JSONResponse:
        """Bulk-delete parsed products by id. Body: {"ids": [str, ...]}."""
        try:
            from sqlalchemy import delete  # noqa: PLC0415

            from models.job import async_session  # noqa: PLC0415
            from models.parsed_product import ParsedProduct  # noqa: PLC0415

            if async_session is None:
                return JSONResponse({"error": "DB not initialised"}, status_code=503)

            body = await request.json()
            ids = body.get("ids")
            if not isinstance(ids, list) or not ids:
                return JSONResponse({"error": "ids (non-empty list) is required"}, status_code=422)
            ids = [str(i) for i in ids]

            async with async_session() as session:
                result = await session.execute(delete(ParsedProduct).where(ParsedProduct.id.in_(ids)))
                await session.commit()
            return JSONResponse({"deleted": result.rowcount or 0})  # type: ignore[attr-defined]
        except Exception as exc:  # noqa: BLE001
            logger.error(f"Failed to bulk-delete products: {exc}")
            return JSONResponse({"error": str(exc)}, status_code=500)

    @staticmethod
    async def refetch_products(request: Request) -> JSONResponse:
        """Re-parse the selected products in the background. Body: {"ids": [str, ...]}.

        Returns immediately with how many were queued; the table reflects the
        refreshed prices/timestamps once the background re-parse completes.
        """
        try:
            import asyncio  # noqa: PLC0415

            from sqlalchemy import select  # noqa: PLC0415

            from models.job import async_session  # noqa: PLC0415
            from models.parsed_product import ParsedProduct  # noqa: PLC0415

            if async_session is None:
                return JSONResponse({"error": "DB not initialised"}, status_code=503)

            body = await request.json()
            ids = body.get("ids")
            if not isinstance(ids, list) or not ids:
                return JSONResponse({"error": "ids (non-empty list) is required"}, status_code=422)
            ids = [str(i) for i in ids]

            async with async_session() as session:
                rows = (await session.execute(
                    select(ParsedProduct.url, ParsedProduct.marketplace, ParsedProduct.target_id)
                    .where(ParsedProduct.id.in_(ids))
                )).all()
            items = [{"url": r[0], "marketplace": r[1] or "", "target_id": r[2]} for r in rows if r[0]]
            if not items:
                return JSONResponse({"error": "no matching products"}, status_code=404)

            asyncio.create_task(_refetch_background(items))
            return JSONResponse({"ok": True, "queued": len(items)})
        except Exception as exc:  # noqa: BLE001
            logger.error(f"Failed to start refetch: {exc}")
            return JSONResponse({"error": str(exc)}, status_code=500)

    @staticmethod
    async def export_products(request: Request) -> Response:
        """POST body: {format, ids?, marketplace?}"""
        try:
            body = await request.json()
        except Exception:  # noqa: BLE001
            body = {}

        fmt = str(body.get("format", "csv")).lower()
        marketplace = body.get("marketplace", "") or ""
        id_list: list[str] = [str(i) for i in (body.get("ids") or []) if i]

        try:
            from sqlalchemy import select  # noqa: PLC0415

            from models.job import async_session  # noqa: PLC0415
            from models.parsed_product import ParsedProduct  # noqa: PLC0415

            if async_session is None:
                return JSONResponse({"error": "DB not initialised"}, status_code=503)

            async with async_session() as session:
                q = select(ParsedProduct).order_by(ParsedProduct.parsed_at.desc())
                if id_list:
                    q = q.where(ParsedProduct.id.in_(id_list))
                elif marketplace:
                    q = q.where(ParsedProduct.marketplace == marketplace)
                rows = (await session.execute(q)).scalars().all()

            products = [r.to_dict() for r in rows]
        except Exception as exc:  # noqa: BLE001
            logger.error(f"Export failed: {exc}")
            return JSONResponse({"error": str(exc)}, status_code=500)

        if fmt == "json":
            content = json.dumps(products, ensure_ascii=False, indent=2)
            return Response(
                content=content,
                media_type="application/json",
                headers={"Content-Disposition": "attachment; filename=products.json"},
            )

        if fmt == "xlsx":
            try:
                import openpyxl  # noqa: PLC0415
                wb = openpyxl.Workbook()
                ws = wb.active
                ws.title = "Products"
                ws.append(_EXPORT_COLUMNS)
                for p in products:
                    ws.append([
                        p.get("title"),
                        p.get("sku"),
                        p.get("marketplace"),
                        p.get("original_price"),
                        p.get("current_price"),
                        p.get("url"),
                        ", ".join(p.get("images") or []),
                        p.get("parsed_at"),
                    ])
                buf = io.BytesIO()
                wb.save(buf)
                buf.seek(0)
                return StreamingResponse(
                    buf,
                    media_type="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
                    headers={"Content-Disposition": "attachment; filename=products.xlsx"},
                )
            except ImportError:
                return JSONResponse({"error": "openpyxl not installed"}, status_code=500)

        # Default: CSV
        buf_str = io.StringIO()
        writer = csv.writer(buf_str)
        writer.writerow(_EXPORT_COLUMNS)
        for p in products:
            writer.writerow([
                p.get("title"),
                p.get("sku"),
                p.get("marketplace"),
                p.get("original_price"),
                p.get("current_price"),
                p.get("url"),
                ", ".join(p.get("images") or []),
                p.get("parsed_at"),
            ])
        return Response(
            content="\ufeff" + buf_str.getvalue(),  # BOM for Excel
            media_type="text/csv; charset=utf-8",
            headers={"Content-Disposition": "attachment; filename=products.csv"},
        )


async def _refetch_background(items: list[dict]) -> None:
    """Re-parse each product URL and upsert the refreshed result (fire-and-forget)."""
    import json as _json  # noqa: PLC0415

    from admin.resources.targets import _upsert_parsed_product  # noqa: PLC0415
    from services.parsing import ParsingService  # noqa: PLC0415

    svc = ParsingService()
    ok = 0
    for it in items:
        try:
            product = await svc.parse_product(it["url"])
            await _upsert_parsed_product(
                session_id=None, target_id=it.get("target_id"), url=it["url"],
                marketplace=it.get("marketplace") or "", data=product.to_dict(), json_module=_json,
            )
            ok += 1
            logger.info(f"refetch OK: {it['url']}")
        except Exception as exc:  # noqa: BLE001
            logger.warning(f"refetch FAIL: {it['url']}: {exc}")
    logger.info(f"Refetch finished: {ok}/{len(items)} products updated")


def register_products_routes(app) -> None:  # type: ignore[no-untyped-def]
    from fastapi import Depends  # noqa: PLC0415

    from admin.resources.auth import require_admin  # noqa: PLC0415
    _auth = [Depends(require_admin)]
    router = ProductsRouter()
    app.add_api_route("/admin/products/api", router.list_products, methods=["GET"], tags=["admin"], dependencies=_auth)
    app.add_api_route("/admin/products/image", router.product_image, methods=["GET"], tags=["admin"], dependencies=_auth)
    app.add_api_route(
        "/admin/products/export", router.export_products, methods=["POST"], tags=["admin"], dependencies=_auth,
    )
    app.add_api_route(
        "/admin/products/delete", router.delete_product, methods=["POST"], tags=["admin"], dependencies=_auth,
    )
    app.add_api_route(
        "/admin/products/delete_many", router.delete_many, methods=["POST"], tags=["admin"], dependencies=_auth,
    )
    app.add_api_route(
        "/admin/products/refetch", router.refetch_products, methods=["POST"], tags=["admin"], dependencies=_auth,
    )
    app.add_api_route(
        "/admin/products/clear", router.clear_products, methods=["POST"], tags=["admin"], dependencies=_auth,
    )
