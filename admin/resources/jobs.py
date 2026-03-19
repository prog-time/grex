"""Jobs resource — ModelView defined in admin/__init__.py as JobView.

API endpoints for jobs list and clear are kept here for JSON access.
"""
from __future__ import annotations

from fastapi import Request
from fastapi.responses import JSONResponse
from loguru import logger


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
    app.add_api_route("/admin/jobs/clear", router.clear_all, methods=["POST"], tags=["admin"])
