"""Asynchronous parse jobs for the public API (``/v1/jobs``).

A job is created and answered immediately; the URLs are parsed in a background
task through :meth:`ParsingService.parse_products_batch`, and every finished
URL is written to ``api_job_items`` right away so clients can poll progress.
Background tasks live in this process: jobs still pending or running when the
server stops are marked failed on the next startup (:func:`reset_stale_jobs`).
"""
from __future__ import annotations

import asyncio
import json
from datetime import datetime

from loguru import logger

# Unfinished jobs one API key may have at a time — keeps a single client from
# queueing unbounded work.
MAX_ACTIVE_JOBS_PER_KEY = 5
_ACTIVE_STATUSES = ("pending", "running")

# Strong references so running jobs aren't garbage-collected mid-flight.
_running: set[asyncio.Task] = set()


class TooManyActiveJobs(Exception):
    pass


def _session_factory():  # type: ignore[no-untyped-def]
    from models.job import async_session  # noqa: PLC0415

    if async_session is None:
        raise RuntimeError("DB not initialised")
    return async_session


async def create_job(api_key_id: str, urls: list[str], concurrency: int | None, parsing_service) -> dict:  # type: ignore[no-untyped-def]
    """Persist a new job for *api_key_id* and start it in the background."""
    from sqlalchemy import func, select  # noqa: PLC0415

    from models.api_job import ApiJob  # noqa: PLC0415

    session_factory = _session_factory()
    async with session_factory() as session:
        active = (await session.execute(
            select(func.count()).select_from(ApiJob).where(
                ApiJob.api_key_id == api_key_id, ApiJob.status.in_(_ACTIVE_STATUSES)
            )
        )).scalar_one()
        if active >= MAX_ACTIVE_JOBS_PER_KEY:
            raise TooManyActiveJobs(
                f"Too many unfinished jobs ({active}); limit is {MAX_ACTIVE_JOBS_PER_KEY} per API key"
            )
        job = ApiJob(
            api_key_id=api_key_id,
            status="pending",
            total=len(urls),
            created_at=datetime.utcnow(),
        )
        session.add(job)
        await session.commit()
        data = job.to_dict()

    task = asyncio.create_task(_run_job(job.id, urls, concurrency, parsing_service), name=f"api_job:{job.id}")
    _running.add(task)
    task.add_done_callback(_running.discard)
    logger.info(f"API job {job.id} created: {len(urls)} URLs")
    return data


async def _run_job(job_id: str, urls: list[str], concurrency: int | None, parsing_service) -> None:  # type: ignore[no-untyped-def]
    from sqlalchemy import update  # noqa: PLC0415

    from models.api_job import ApiJob, ApiJobItem  # noqa: PLC0415

    session_factory = _session_factory()

    async def set_job(**values: object) -> None:
        async with session_factory() as session:
            await session.execute(update(ApiJob).where(ApiJob.id == job_id).values(**values))
            await session.commit()

    async def on_result(index: int, result) -> None:  # type: ignore[no-untyped-def]
        async with session_factory() as session:
            session.add(ApiJobItem(
                job_id=job_id,
                position=index,
                url=result.url,
                success=1 if result.success else 0,
                marketplace=result.marketplace,
                data=json.dumps(result.data, ensure_ascii=False) if result.data is not None else None,
                error=result.error,
                processing_time=result.processing_time,
            ))
            counter = ApiJob.succeeded if result.success else ApiJob.failed_count
            await session.execute(
                update(ApiJob).where(ApiJob.id == job_id)
                .values({ApiJob.done: ApiJob.done + 1, counter: counter + 1})
            )
            await session.commit()

    try:
        await set_job(status="running", started_at=datetime.utcnow())
        await parsing_service.parse_products_batch(urls, concurrency=concurrency, on_result=on_result)
        await set_job(status="completed", finished_at=datetime.utcnow())
        logger.info(f"API job {job_id} completed")
    except Exception as exc:  # noqa: BLE001
        logger.error(f"API job {job_id} failed: {exc}")
        try:
            await set_job(status="failed", error=str(exc), finished_at=datetime.utcnow())
        except Exception as db_exc:  # noqa: BLE001
            logger.error(f"API job {job_id}: failed to record failure: {db_exc}")


async def get_job(api_key_id: str, job_id: str) -> dict | None:
    """Return the job with its finished results, or None if *api_key_id* doesn't own it."""
    from sqlalchemy import select  # noqa: PLC0415

    from models.api_job import ApiJob, ApiJobItem  # noqa: PLC0415

    async with _session_factory()() as session:
        job = (await session.execute(
            select(ApiJob).where(ApiJob.id == job_id, ApiJob.api_key_id == api_key_id)
        )).scalar_one_or_none()
        if job is None:
            return None
        items = (await session.execute(
            select(ApiJobItem).where(ApiJobItem.job_id == job_id).order_by(ApiJobItem.position)
        )).scalars().all()
    return {**job.to_dict(), "results": [i.to_dict() for i in items]}


async def list_jobs(api_key_id: str, limit: int) -> list[dict]:
    """Most recent jobs of *api_key_id*, without results."""
    from sqlalchemy import select  # noqa: PLC0415

    from models.api_job import ApiJob  # noqa: PLC0415

    async with _session_factory()() as session:
        rows = (await session.execute(
            select(ApiJob).where(ApiJob.api_key_id == api_key_id)
            .order_by(ApiJob.created_at.desc()).limit(limit)
        )).scalars().all()
    return [r.to_dict() for r in rows]


async def reset_stale_jobs() -> None:
    """Mark jobs left unfinished by a previous process as failed."""
    from sqlalchemy import update  # noqa: PLC0415

    from models.api_job import ApiJob  # noqa: PLC0415

    async with _session_factory()() as session:
        result = await session.execute(
            update(ApiJob).where(ApiJob.status.in_(_ACTIVE_STATUSES)).values(
                status="failed", error="Interrupted by server restart", finished_at=datetime.utcnow()
            )
        )
        await session.commit()
    if result.rowcount:
        logger.info(f"{result.rowcount} unfinished API job(s) marked failed after restart")
