"""Admin resource — Parse Targets.

CRUD endpoints for pages configured for scraping:
URL, marketplace, and selected fields (extraction logic lives in crawlers).
"""
from __future__ import annotations

import csv
import io
import uuid
from datetime import datetime

from fastapi import Request
from fastapi.responses import JSONResponse
from loguru import logger
from starlette.datastructures import UploadFile as StarletteUploadFile


class TargetsRouter:

    @staticmethod
    async def list_targets(request: Request) -> JSONResponse:
        try:
            from models.job import async_session  # noqa: PLC0415
            from models.parse_target import ParseTarget  # noqa: PLC0415
            from sqlalchemy import select  # noqa: PLC0415

            if async_session is None:
                return JSONResponse({"error": "DB not initialised"}, status_code=503)

            async with async_session() as session:
                rows = (await session.execute(
                    select(ParseTarget).order_by(ParseTarget.created_at.desc())
                )).scalars().all()

            targets = [r.to_dict() for r in rows]
            logger.debug(f"Targets list: {len(targets)} records")
            return JSONResponse({"targets": targets})
        except Exception as exc:  # noqa: BLE001
            logger.error(f"Failed to list targets: {exc}")
            return JSONResponse({"error": str(exc)}, status_code=500)

    @staticmethod
    async def add_target(request: Request) -> JSONResponse:
        try:
            from models.job import async_session  # noqa: PLC0415
            from models.parse_target import ParseTarget  # noqa: PLC0415
            from crawlers.registry import CrawlerRegistry  # noqa: PLC0415
            from core.exceptions import UnsupportedMarketplaceError  # noqa: PLC0415

            if async_session is None:
                return JSONResponse({"error": "DB not initialised"}, status_code=503)

            body = await request.json()
            url: str = body.get("url", "").strip()
            name: str = body.get("name", "").strip()

            if not url:
                return JSONResponse({"error": "url is required"}, status_code=422)

            try:
                marketplace = CrawlerRegistry.detect_marketplace(url)
            except UnsupportedMarketplaceError:
                return JSONResponse(
                    {"error": f"Cannot detect marketplace for URL: {url}"},
                    status_code=422,
                )

            target = ParseTarget(
                id=str(uuid.uuid4()),
                name=name or None,
                url=url,
                marketplace=marketplace,
                enabled=True,
                created_at=datetime.utcnow(),
            )

            async with async_session() as session:
                session.add(target)
                await session.commit()

            logger.info(f"Parse target added: {url} (detected: {marketplace})")
            return JSONResponse(target.to_dict(), status_code=201)
        except Exception as exc:  # noqa: BLE001
            logger.error(f"Failed to add target: {exc}")
            return JSONResponse({"error": str(exc)}, status_code=500)

    @staticmethod
    async def parse_file(request: Request) -> JSONResponse:
        """Parse an uploaded CSV or XLSX file and return the list of URLs found."""
        try:
            form = await request.form()
            upload = form.get("file")
            if upload is None:
                return JSONResponse({"error": "Файл не передан"}, status_code=422)

            if not isinstance(upload, StarletteUploadFile):
                return JSONResponse({"error": "Ожидается файл"}, status_code=422)

            filename = (upload.filename or "").lower()
            content: bytes = await upload.read()

            urls: list[str] = []
            seen: set[str] = set()

            def collect(value: object) -> None:
                v = str(value or "").strip()
                if (v.startswith("http://") or v.startswith("https://")) and v not in seen:
                    seen.add(v)
                    urls.append(v)

            if filename.endswith(".txt"):
                text = content.decode("utf-8-sig", errors="replace")
                for line in text.splitlines():
                    collect(line.strip())

            elif filename.endswith(".csv"):
                text = content.decode("utf-8-sig", errors="replace")
                sample = text[:4096]
                try:
                    dialect = csv.Sniffer().sniff(sample, delimiters=",;\t|")
                except csv.Error:
                    dialect = csv.excel  # fallback: comma
                reader = csv.reader(io.StringIO(text), dialect=dialect)
                for row in reader:
                    for cell in row:
                        collect(cell)

            elif filename.endswith((".xlsx", ".xls")):
                import openpyxl  # noqa: PLC0415
                wb = openpyxl.load_workbook(io.BytesIO(content), read_only=True, data_only=True)
                for ws in wb.worksheets:
                    for row in ws.iter_rows(values_only=True):
                        for cell in (row or []):
                            if cell is not None:
                                collect(cell)
                wb.close()

            else:
                return JSONResponse(
                    {"error": "Поддерживаются только .txt, .csv и .xlsx файлы"},
                    status_code=422,
                )

            logger.debug(f"File parsed: {filename} → {len(urls)} URLs")
            return JSONResponse({"urls": urls, "total": len(urls)})

        except Exception as exc:  # noqa: BLE001
            logger.error(f"File parse error: {exc}")
            return JSONResponse({"error": f"Ошибка чтения файла: {exc}"}, status_code=500)

    @staticmethod
    async def import_bulk(request: Request) -> JSONResponse:
        """Import multiple parse targets from a JSON list of URLs."""
        try:
            from models.job import async_session  # noqa: PLC0415
            from models.parse_target import ParseTarget  # noqa: PLC0415
            from crawlers.registry import CrawlerRegistry  # noqa: PLC0415
            from core.exceptions import UnsupportedMarketplaceError  # noqa: PLC0415
            from sqlalchemy import select  # noqa: PLC0415

            if async_session is None:
                return JSONResponse({"error": "DB not initialised"}, status_code=503)

            body = await request.json()
            urls: list[str] = body.get("urls") or []
            if not urls:
                return JSONResponse({"error": "urls list is required"}, status_code=422)

            # Load existing URLs to detect duplicates
            async with async_session() as session:
                existing_urls: set[str] = set(
                    (await session.execute(select(ParseTarget.url))).scalars().all()
                )

            added = 0
            skipped = 0
            errors: list[dict] = []
            results: list[dict] = []

            async with async_session() as session:
                for raw_url in urls:
                    url = raw_url.strip()
                    if not url:
                        continue

                    if url in existing_urls:
                        skipped += 1
                        continue

                    try:
                        marketplace = CrawlerRegistry.detect_marketplace(url)
                    except UnsupportedMarketplaceError:
                        errors.append({"url": url, "reason": "Неизвестный маркетплейс"})
                        continue

                    target = ParseTarget(
                        id=str(uuid.uuid4()),
                        url=url,
                        marketplace=marketplace,
                        enabled=True,
                        created_at=datetime.utcnow(),
                    )
                    session.add(target)
                    existing_urls.add(url)
                    added += 1
                    results.append({"url": url, "marketplace": marketplace, "id": target.id})

                await session.commit()

            logger.info(f"Bulk import: added={added} skipped={skipped} errors={len(errors)}")
            return JSONResponse({
                "added": added,
                "skipped": skipped,
                "errors": errors,
                "results": results,
            })
        except Exception as exc:  # noqa: BLE001
            logger.error(f"Failed to import bulk targets: {exc}")
            return JSONResponse({"error": str(exc)}, status_code=500)

    @staticmethod
    async def delete_target(request: Request) -> JSONResponse:
        body = {}
        try:
            from models.job import async_session  # noqa: PLC0415
            from models.parse_target import ParseTarget  # noqa: PLC0415
            from sqlalchemy import delete  # noqa: PLC0415

            if async_session is None:
                return JSONResponse({"error": "DB not initialised"}, status_code=503)

            body = await request.json()
            target_id: str = body.get("id", "").strip()
            if not target_id:
                return JSONResponse({"error": "id is required"}, status_code=422)

            async with async_session() as session:
                result = await session.execute(
                    delete(ParseTarget).where(ParseTarget.id == target_id)
                )
                await session.commit()

            if result.rowcount == 0:  # type: ignore[attr-defined]
                return JSONResponse({"error": "Target not found"}, status_code=404)

            logger.info(f"Parse target deleted: {target_id}")
            return JSONResponse({"deleted": target_id})
        except Exception as exc:  # noqa: BLE001
            logger.error(f"Failed to delete target {body.get('id')}: {exc}")
            return JSONResponse({"error": str(exc)}, status_code=500)

    @staticmethod
    async def toggle_target(request: Request) -> JSONResponse:
        try:
            from models.job import async_session  # noqa: PLC0415
            from models.parse_target import ParseTarget  # noqa: PLC0415
            from sqlalchemy import select  # noqa: PLC0415

            if async_session is None:
                return JSONResponse({"error": "DB not initialised"}, status_code=503)

            body = await request.json()
            target_id: str = body.get("id", "").strip()
            if not target_id:
                return JSONResponse({"error": "id is required"}, status_code=422)

            async with async_session() as session:
                row = (await session.execute(
                    select(ParseTarget).where(ParseTarget.id == target_id)
                )).scalar_one_or_none()

                if row is None:
                    return JSONResponse({"error": "Target not found"}, status_code=404)

                row.enabled = not row.enabled  # type: ignore[assignment]
                await session.commit()
                new_state = row.enabled

            logger.info(f"Parse target {target_id} toggled to enabled={new_state}")
            return JSONResponse({"id": target_id, "enabled": new_state})
        except Exception as exc:  # noqa: BLE001
            logger.error(f"Failed to toggle target: {exc}")
            return JSONResponse({"error": str(exc)}, status_code=500)

    @staticmethod
    async def run_target(request: Request) -> JSONResponse:
        """Parse a single target by ID and return the product data."""
        try:
            from models.job import async_session  # noqa: PLC0415
            from models.parse_target import ParseTarget  # noqa: PLC0415
            from sqlalchemy import select  # noqa: PLC0415
            from services.parsing import ParsingService  # noqa: PLC0415
            from admin.resources.sources import get_source_fields  # noqa: PLC0415

            if async_session is None:
                return JSONResponse({"error": "DB not initialised"}, status_code=503)

            body = await request.json()
            target_id: str = body.get("id", "").strip()
            if not target_id:
                return JSONResponse({"error": "id is required"}, status_code=422)

            async with async_session() as session:
                row = (await session.execute(
                    select(ParseTarget).where(ParseTarget.id == target_id)
                )).scalar_one_or_none()

            if row is None:
                return JSONResponse({"error": "Target not found"}, status_code=404)

            logger.info(f"Running parse for target {target_id}: {row.url}")
            svc = ParsingService()
            try:
                product = await svc.parse_product(str(row.url))
            except Exception as exc:  # noqa: BLE001
                logger.warning(f"Parse failed for {row.url}: {exc}")
                return JSONResponse({"success": False, "url": row.url, "error": str(exc)})

            data = product.to_dict()

            # Apply field filter from source configuration
            selected = await get_source_fields(str(row.marketplace))
            if selected:
                data = {k: v for k, v in data.items() if k in selected or k == "source_url"}

            logger.info(f"Parse succeeded for target {target_id}")
            return JSONResponse({"success": True, "url": row.url, "marketplace": row.marketplace, "data": data})
        except Exception as exc:  # noqa: BLE001
            logger.error(f"run_target failed: {exc}")
            return JSONResponse({"error": str(exc)}, status_code=500)

    @staticmethod
    async def run_all_targets(request: Request) -> JSONResponse:
        """Parse all enabled targets sequentially and return a results summary."""
        try:
            from models.job import async_session  # noqa: PLC0415
            from models.parse_target import ParseTarget  # noqa: PLC0415
            from sqlalchemy import select  # noqa: PLC0415
            from services.parsing import ParsingService  # noqa: PLC0415
            from admin.resources.sources import get_source_fields  # noqa: PLC0415

            if async_session is None:
                return JSONResponse({"error": "DB not initialised"}, status_code=503)

            async with async_session() as session:
                rows = (await session.execute(
                    select(ParseTarget)
                    .where(ParseTarget.enabled.is_(True))
                    .order_by(ParseTarget.created_at.asc())
                )).scalars().all()

            if not rows:
                return JSONResponse({"results": [], "total": 0, "succeeded": 0, "failed": 0})

            svc = ParsingService()
            results = []
            for row in rows:
                entry: dict = {"id": row.id, "url": row.url, "marketplace": row.marketplace}
                try:
                    product = await svc.parse_product(str(row.url))
                    data = product.to_dict()
                    selected = await get_source_fields(str(row.marketplace))
                    if selected:
                        data = {k: v for k, v in data.items() if k in selected or k == "source_url"}
                    entry["success"] = True
                    entry["data"] = data
                    logger.info(f"run-all: parsed {row.url}")
                except Exception as exc:  # noqa: BLE001
                    entry["success"] = False
                    entry["error"] = str(exc)
                    logger.warning(f"run-all: failed {row.url}: {exc}")
                results.append(entry)

            succeeded = sum(1 for r in results if r["success"])
            return JSONResponse({
                "results": results,
                "total": len(results),
                "succeeded": succeeded,
                "failed": len(results) - succeeded,
            })
        except Exception as exc:  # noqa: BLE001
            logger.error(f"run_all_targets failed: {exc}")
            return JSONResponse({"error": str(exc)}, status_code=500)


def register_targets_routes(app) -> None:  # type: ignore[no-untyped-def]
    router = TargetsRouter()
    app.add_api_route(
        "/admin/targets/api",
        router.list_targets,
        methods=["GET"],
        tags=["admin"],
    )
    app.add_api_route(
        "/admin/targets/add",
        router.add_target,
        methods=["POST"],
        tags=["admin"],
    )
    app.add_api_route(
        "/admin/targets/parse-file",
        router.parse_file,
        methods=["POST"],
        tags=["admin"],
    )
    app.add_api_route(
        "/admin/targets/import-bulk",
        router.import_bulk,
        methods=["POST"],
        tags=["admin"],
    )
    app.add_api_route(
        "/admin/targets/delete",
        router.delete_target,
        methods=["POST"],
        tags=["admin"],
    )
    app.add_api_route(
        "/admin/targets/toggle",
        router.toggle_target,
        methods=["POST"],
        tags=["admin"],
    )
    app.add_api_route(
        "/admin/targets/run",
        router.run_target,
        methods=["POST"],
        tags=["admin"],
    )
    app.add_api_route(
        "/admin/targets/run-all",
        router.run_all_targets,
        methods=["POST"],
        tags=["admin"],
    )
