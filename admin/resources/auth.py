"""Admin auth dependency — require active admin session."""
from __future__ import annotations

from fastapi import HTTPException, Request


async def require_admin(request: Request) -> None:
    """FastAPI dependency: raise 401 if no active admin session."""
    username = request.session.get("username")
    if not username:
        raise HTTPException(status_code=401, detail="Unauthorized")

    from utils.logger import set_actor  # noqa: PLC0415

    actor = f"admin:{username}"
    request.state.actor = actor
    set_actor(actor)
