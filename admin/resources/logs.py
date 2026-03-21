"""Admin resource — Logs SSE (TASK-12).

Registers a ``/admin/logs/stream`` SSE endpoint that streams live log lines
from the global :data:`~utils.logger.log_queue`.
"""
from __future__ import annotations

import asyncio
from typing import AsyncIterator

from fastapi import Request
from fastapi.responses import JSONResponse
from loguru import logger
from sse_starlette.sse import EventSourceResponse


async def _log_event_generator(
    request: Request,
    level_filter: str | None = None,
) -> AsyncIterator[dict]:
    """Yield SSE events from :data:`~utils.logger.log_queue`."""
    from utils.logger import log_queue  # noqa: PLC0415

    level_upper = level_filter.upper() if level_filter else None

    while True:
        if await request.is_disconnected():
            break

        try:
            line: str = await asyncio.wait_for(log_queue.get(), timeout=1.0)
        except asyncio.TimeoutError:
            # Send a keep-alive comment so the browser doesn't time out.
            yield {"comment": "keep-alive"}
            continue

        # Apply optional level filter.
        if level_upper and level_upper not in line.upper():
            continue

        yield {"data": line}


async def logs_stream(
    request: Request,
    level: str | None = None,
) -> EventSourceResponse | JSONResponse:
    """SSE endpoint — streams live log lines.

    Requires an active admin session (set by starlette-admin login).
    Query parameter ``level`` filters by log level substring
    (e.g. ``?level=ERROR``).
    """
    level_info = f" (filter: {level})" if level else ""
    logger.debug(f"SSE log stream opened{level_info}")
    return EventSourceResponse(
        _log_event_generator(request, level_filter=level),
        headers={"Cache-Control": "no-cache"},
    )


def register_logs_routes(app) -> None:  # type: ignore[no-untyped-def]
    """Attach the SSE log stream endpoint to *app*."""
    from fastapi import Depends  # noqa: PLC0415
    from admin.resources.auth import require_admin  # noqa: PLC0415
    app.add_api_route(
        "/admin/logs/stream",
        logs_stream,
        methods=["GET"],
        tags=["admin"],
        summary="Server-Sent Events stream of application logs",
        response_model=None,
        dependencies=[Depends(require_admin)],
    )
