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


async def _upsert_parsed_product(
    *,
    session_id: str | None,
    target_id: str | None,
    url: str,
    marketplace: str,
    data: dict,
    json_module: object,
) -> None:
    """Insert or update a ParsedProduct row keyed by URL."""
    import json as _json  # noqa: PLC0415
    from datetime import datetime as _dt  # noqa: PLC0415
    from models.job import async_session  # noqa: PLC0415
    from models.parsed_product import ParsedProduct  # noqa: PLC0415
    from sqlalchemy import select  # noqa: PLC0415

    images_json = _json.dumps(data.get("images", []))
    orig = float(data["original_price"]) if data.get("original_price") else None
    cur = float(data["current_price"]) if data.get("current_price") else None

    async with async_session() as s:
        existing = (await s.execute(
            select(ParsedProduct).where(ParsedProduct.url == url)
        )).scalar_one_or_none()

        if existing is not None:
            existing.session_id = session_id
            existing.title = data.get("title")
            existing.sku = data.get("sku")
            existing.original_price = orig
            existing.current_price = cur
            existing.images = images_json
            existing.parsed_at = _dt.utcnow()
        else:
            s.add(ParsedProduct(
                id=str(uuid.uuid4()),
                session_id=session_id,
                target_id=target_id,
                url=url,
                marketplace=marketplace,
                title=data.get("title"),
                sku=data.get("sku"),
                original_price=orig,
                current_price=cur,
                images=images_json,
                parsed_at=_dt.utcnow(),
            ))
        await s.commit()


async def _run_all_background(session_id: str, targets: list[dict]) -> None:
    """Background task: parse all targets, save ParsedProduct rows, update ParseSession."""
    import json as _json  # noqa: PLC0415
    from datetime import datetime  # noqa: PLC0415
    from models.job import async_session  # noqa: PLC0415
    from models.parsed_product import ParseSession  # noqa: PLC0415
    from services.parsing import ParsingService  # noqa: PLC0415
    from admin.resources.sources import get_source_fields  # noqa: PLC0415
    from sqlalchemy import update  # noqa: PLC0415

    svc = ParsingService()
    succeeded = 0
    failed_count = 0

    async def _update_session(**values: object) -> None:
        async with async_session() as s:
            await s.execute(
                update(ParseSession).where(ParseSession.id == session_id).values(**values)
            )
            await s.commit()

    try:
        await _update_session(status="running")

        for tgt in targets:
            try:
                product = await svc.parse_product(tgt["url"])
                data = product.to_dict()
                selected = await get_source_fields(tgt["marketplace"])
                if selected:
                    data = {k: v for k, v in data.items() if k in selected or k == "source_url"}

                await _upsert_parsed_product(
                    session_id=session_id,
                    target_id=tgt["id"],
                    url=tgt["url"],
                    marketplace=tgt["marketplace"],
                    data=data,
                    json_module=_json,
                )
                succeeded += 1
                logger.info(f"bg-parse OK: {tgt['url']}")
            except Exception as exc:  # noqa: BLE001
                failed_count += 1
                logger.warning(f"bg-parse FAIL: {tgt['url']}: {exc}")

            await _update_session(succeeded=succeeded, failed_count=failed_count)

    finally:
        await _update_session(
            status="completed",
            finished_at=datetime.utcnow(),
            succeeded=succeeded,
            failed_count=failed_count,
        )
        logger.info(f"ParseSession {session_id} done: {succeeded} ok / {failed_count} fail")


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
        """Parse a single target by ID, save to ParsedProduct, return the product data."""
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
            selected = await get_source_fields(str(row.marketplace))
            if selected:
                data = {k: v for k, v in data.items() if k in selected or k == "source_url"}

            # Save or update parsed_products (unique by URL)
            import json as _json  # noqa: PLC0415
            await _upsert_parsed_product(
                session_id=None,
                target_id=row.id,
                url=str(row.url),
                marketplace=str(row.marketplace),
                data=data,
                json_module=_json,
            )

            logger.info(f"Parse succeeded for target {target_id}")
            return JSONResponse({"success": True, "url": row.url, "marketplace": row.marketplace, "data": data})
        except Exception as exc:  # noqa: BLE001
            logger.error(f"run_target failed: {exc}")
            return JSONResponse({"error": str(exc)}, status_code=500)

    @staticmethod
    async def run_all_targets(request: Request) -> JSONResponse:
        """Start background parse of all enabled targets. Returns session_id immediately."""
        import asyncio  # noqa: PLC0415
        try:
            from models.job import async_session  # noqa: PLC0415
            from models.parse_target import ParseTarget  # noqa: PLC0415
            from models.parsed_product import ParseSession  # noqa: PLC0415
            from sqlalchemy import select  # noqa: PLC0415

            if async_session is None:
                return JSONResponse({"error": "DB not initialised"}, status_code=503)

            async with async_session() as session:
                rows = (await session.execute(
                    select(ParseTarget)
                    .where(ParseTarget.enabled.is_(True))
                    .order_by(ParseTarget.created_at.asc())
                )).scalars().all()

            if not rows:
                return JSONResponse({"session_id": None, "total": 0, "message": "Нет активных targets"})

            targets = [{"id": r.id, "url": str(r.url), "marketplace": str(r.marketplace)} for r in rows]

            # Create ParseSession record
            session_id = str(uuid.uuid4())
            parse_session = ParseSession(
                id=session_id,
                status="pending",
                total=len(targets),
                succeeded=0,
                failed_count=0,
                started_at=datetime.utcnow(),
            )
            async with async_session() as session:
                session.add(parse_session)
                await session.commit()

            # Launch background task (survives page reload)
            asyncio.create_task(_run_all_background(session_id, targets))

            logger.info(f"ParseSession {session_id} started: {len(targets)} targets")
            return JSONResponse({"session_id": session_id, "total": len(targets), "status": "running"})
        except Exception as exc:  # noqa: BLE001
            logger.error(f"run_all_targets failed: {exc}")
            return JSONResponse({"error": str(exc)}, status_code=500)

    @staticmethod
    async def get_active_session(request: Request) -> JSONResponse:
        """Return the most recent pending/running ParseSession, or null."""
        try:
            from models.job import async_session  # noqa: PLC0415
            from models.parsed_product import ParseSession  # noqa: PLC0415
            from sqlalchemy import select  # noqa: PLC0415

            if async_session is None:
                return JSONResponse({"session": None})

            async with async_session() as session:
                row = (await session.execute(
                    select(ParseSession)
                    .where(ParseSession.status.in_(["pending", "running"]))
                    .order_by(ParseSession.started_at.desc())
                    .limit(1)
                )).scalar_one_or_none()

            return JSONResponse({"session": row.to_dict() if row else None})
        except Exception as exc:  # noqa: BLE001
            logger.error(f"get_active_session failed: {exc}")
            return JSONResponse({"session": None})

    @staticmethod
    async def get_session_status(session_id: str, request: Request) -> JSONResponse:
        """Return current status of a ParseSession."""
        try:
            from models.job import async_session  # noqa: PLC0415
            from models.parsed_product import ParseSession  # noqa: PLC0415
            from sqlalchemy import select  # noqa: PLC0415

            if async_session is None:
                return JSONResponse({"error": "DB not initialised"}, status_code=503)

            async with async_session() as session:
                row = (await session.execute(
                    select(ParseSession).where(ParseSession.id == session_id)
                )).scalar_one_or_none()

            if row is None:
                return JSONResponse({"error": "Session not found"}, status_code=404)

            return JSONResponse(row.to_dict())
        except Exception as exc:  # noqa: BLE001
            logger.error(f"get_session_status failed: {exc}")
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
    app.add_api_route(
        "/admin/targets/active-session",
        router.get_active_session,
        methods=["GET"],
        tags=["admin"],
    )
    app.add_api_route(
        "/admin/targets/session/{session_id}",
        router.get_session_status,
        methods=["GET"],
        tags=["admin"],
    )
