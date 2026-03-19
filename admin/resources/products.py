"""Admin resource — Parsed Products."""
from __future__ import annotations

from fastapi import Request
from fastapi.responses import JSONResponse
from loguru import logger


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


def register_products_routes(app) -> None:  # type: ignore[no-untyped-def]
    router = ProductsRouter()
    app.add_api_route("/admin/products/api", router.list_products, methods=["GET"], tags=["admin"])
    app.add_api_route("/admin/products/delete", router.delete_product, methods=["POST"], tags=["admin"])
    app.add_api_route("/admin/products/clear", router.clear_products, methods=["POST"], tags=["admin"])
