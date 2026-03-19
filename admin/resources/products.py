"""Admin resource — Parsed Products."""
from __future__ import annotations

import csv
import io
import json

from fastapi import Request
from fastapi.responses import JSONResponse, Response, StreamingResponse
from loguru import logger

_EXPORT_COLUMNS = [
    "title", "sku", "marketplace", "original_price", "current_price",
    "url", "images", "parsed_at",
]


class ProductsRouter:

    @staticmethod
    async def list_products(request: Request) -> JSONResponse:
        try:
            from models.job import async_session  # noqa: PLC0415
            from models.parsed_product import ParsedProduct  # noqa: PLC0415
            from sqlalchemy import select  # noqa: PLC0415

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
            from models.job import async_session  # noqa: PLC0415
            from models.parsed_product import ParsedProduct  # noqa: PLC0415
            from sqlalchemy import delete  # noqa: PLC0415

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
            from models.job import async_session  # noqa: PLC0415
            from models.parsed_product import ParsedProduct  # noqa: PLC0415
            from sqlalchemy import delete  # noqa: PLC0415

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
            from models.job import async_session  # noqa: PLC0415
            from models.parsed_product import ParsedProduct  # noqa: PLC0415
            from sqlalchemy import select  # noqa: PLC0415

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


def register_products_routes(app) -> None:  # type: ignore[no-untyped-def]
    router = ProductsRouter()
    app.add_api_route("/admin/products/api", router.list_products, methods=["GET"], tags=["admin"])
    app.add_api_route("/admin/products/export", router.export_products, methods=["POST"], tags=["admin"])
    app.add_api_route("/admin/products/delete", router.delete_product, methods=["POST"], tags=["admin"])
    app.add_api_route("/admin/products/clear", router.clear_products, methods=["POST"], tags=["admin"])
