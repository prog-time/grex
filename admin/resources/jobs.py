"""Jobs resource — custom view with export support."""
from __future__ import annotations

import csv
import io
import json

from fastapi import Request
from fastapi.responses import JSONResponse, Response, StreamingResponse
from loguru import logger

_EXPORT_COLUMNS = [
    "marketplace", "status", "url", "processing_time",
    "started_at", "finished_at", "error",
]


class JobsRouter:

    @staticmethod
    async def list_jobs(
        request: Request,
        marketplace: str | None = None,
        status: str | None = None,
        page: int = 1,
        page_size: int = 50,
    ) -> JSONResponse:
        try:
            from models.job import async_session, Job  # noqa: PLC0415
            from sqlalchemy import select, func  # noqa: PLC0415

            if async_session is None:
                return JSONResponse({"error": "DB not initialised"}, status_code=503)

            async with async_session() as session:
                q = select(Job)
                if marketplace:
                    q = q.where(Job.marketplace == marketplace)
                if status:
                    q = q.where(Job.status == status)

                total_q = select(func.count()).select_from(q.subquery())
                total = (await session.execute(total_q)).scalar_one()

                q = q.order_by(Job.started_at.desc()).offset((page - 1) * page_size).limit(page_size)
                rows = (await session.execute(q)).scalars().all()

            logger.debug(f"Jobs list: total={total}, page={page}, returned={len(rows)}")
            return JSONResponse({
                "total": total,
                "page": page,
                "page_size": page_size,
                "jobs": [r.to_dict() for r in rows],
            })
        except Exception as exc:  # noqa: BLE001
            logger.error(f"Failed to list jobs: {exc}")
            return JSONResponse({"error": str(exc)}, status_code=500)

    @staticmethod
    async def delete_job(request: Request) -> JSONResponse:
        try:
            from models.job import async_session, Job  # noqa: PLC0415
            from sqlalchemy import delete  # noqa: PLC0415

            if async_session is None:
                return JSONResponse({"error": "DB not initialised"}, status_code=503)

            body = await request.json()
            job_id: str = body.get("id", "").strip()
            if not job_id:
                return JSONResponse({"error": "id is required"}, status_code=422)

            async with async_session() as session:
                result = await session.execute(delete(Job).where(Job.id == job_id))
                await session.commit()

            if result.rowcount == 0:  # type: ignore[attr-defined]
                return JSONResponse({"error": "Job not found"}, status_code=404)
            return JSONResponse({"deleted": job_id})
        except Exception as exc:  # noqa: BLE001
            logger.error(f"Failed to delete job: {exc}")
            return JSONResponse({"error": str(exc)}, status_code=500)

    @staticmethod
    async def export_jobs(request: Request) -> Response:
        try:
            body = await request.json()
        except Exception:  # noqa: BLE001
            body = {}

        fmt = str(body.get("format", "csv")).lower()
        marketplace = body.get("marketplace", "") or ""
        status_filter = body.get("status", "") or ""
        id_list: list[str] = [str(i) for i in (body.get("ids") or []) if i]

        try:
            from models.job import async_session, Job  # noqa: PLC0415
            from sqlalchemy import select  # noqa: PLC0415

            if async_session is None:
                return JSONResponse({"error": "DB not initialised"}, status_code=503)

            async with async_session() as session:
                q = select(Job).order_by(Job.started_at.desc())
                if id_list:
                    q = q.where(Job.id.in_(id_list))
                else:
                    if marketplace:
                        q = q.where(Job.marketplace == marketplace)
                    if status_filter:
                        q = q.where(Job.status == status_filter)
                rows = (await session.execute(q)).scalars().all()

            jobs = [r.to_dict() for r in rows]
        except Exception as exc:  # noqa: BLE001
            logger.error(f"Export jobs failed: {exc}")
            return JSONResponse({"error": str(exc)}, status_code=500)

        if fmt == "json":
            content = json.dumps(jobs, ensure_ascii=False, indent=2)
            return Response(
                content=content,
                media_type="application/json",
                headers={"Content-Disposition": "attachment; filename=jobs.json"},
            )

        if fmt == "xlsx":
            try:
                import openpyxl  # noqa: PLC0415
                wb = openpyxl.Workbook()
                ws = wb.active
                ws.title = "Jobs"
                ws.append(_EXPORT_COLUMNS)
                for j in jobs:
                    ws.append([j.get(c) for c in _EXPORT_COLUMNS])
                buf = io.BytesIO()
                wb.save(buf)
                buf.seek(0)
                return StreamingResponse(
                    buf,
                    media_type="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
                    headers={"Content-Disposition": "attachment; filename=jobs.xlsx"},
                )
            except ImportError:
                return JSONResponse({"error": "openpyxl not installed"}, status_code=500)

        buf_str = io.StringIO()
        writer = csv.writer(buf_str)
        writer.writerow(_EXPORT_COLUMNS)
        for j in jobs:
            writer.writerow([j.get(c) for c in _EXPORT_COLUMNS])
        return Response(
            content="\ufeff" + buf_str.getvalue(),
            media_type="text/csv; charset=utf-8",
            headers={"Content-Disposition": "attachment; filename=jobs.csv"},
        )

    @staticmethod
    async def clear_all(request: Request) -> JSONResponse:
        try:
            from models.job import async_session, Job  # noqa: PLC0415
            from sqlalchemy import delete  # noqa: PLC0415

            if async_session is None:
                return JSONResponse({"error": "DB not initialised"}, status_code=503)

            async with async_session() as session:
                result = await session.execute(delete(Job))
                await session.commit()

            logger.info(f"Jobs cleared: {result.rowcount} rows deleted")  # type: ignore[attr-defined]
            return JSONResponse({"deleted": result.rowcount})  # type: ignore[attr-defined]
        except Exception as exc:  # noqa: BLE001
            logger.error(f"Failed to clear jobs: {exc}")
            return JSONResponse({"error": str(exc)}, status_code=500)


def register_jobs_routes(app) -> None:  # type: ignore[no-untyped-def]
    router = JobsRouter()
    app.add_api_route("/admin/jobs/api", router.list_jobs, methods=["GET"], tags=["admin"])
    app.add_api_route("/admin/jobs/export", router.export_jobs, methods=["POST"], tags=["admin"])
    app.add_api_route("/admin/jobs/delete", router.delete_job, methods=["POST"], tags=["admin"])
    app.add_api_route("/admin/jobs/clear", router.clear_all, methods=["POST"], tags=["admin"])
