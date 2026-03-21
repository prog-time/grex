"""Admin auth dependency — require active admin session."""
from __future__ import annotations

from fastapi import HTTPException, Request


async def require_admin(request: Request) -> None:
    """FastAPI dependency: raise 401 if no active admin session."""
    if not request.session.get("username"):
        raise HTTPException(status_code=401, detail="Unauthorized")
