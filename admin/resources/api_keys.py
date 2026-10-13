"""Admin resource — API keys for the public REST API."""
from __future__ import annotations

from datetime import datetime

from fastapi import Request
from fastapi.responses import JSONResponse
from loguru import logger

_NAME_MAX = 100


class ApiKeysRouter:
    """FastAPI router for /admin/api-keys endpoints."""

    @staticmethod
    async def list_keys(request: Request) -> JSONResponse:
        from sqlalchemy import select  # noqa: PLC0415

        from models.api_key import ApiKey  # noqa: PLC0415
        from models.job import async_session  # noqa: PLC0415
        from services.api_keys import is_default_key_hash  # noqa: PLC0415

        if async_session is None:
            return JSONResponse({"error": "DB not initialised"}, status_code=503)

        async with async_session() as session:
            rows = (await session.execute(
                select(ApiKey).order_by(ApiKey.created_at.desc())
            )).scalars().all()

        keys = [{**r.to_dict(), "insecure": is_default_key_hash(r.key_hash)} for r in rows]
        return JSONResponse({"total": len(keys), "keys": keys})

    @staticmethod
    async def create_key(request: Request) -> JSONResponse:
        from services.api_keys import create_key  # noqa: PLC0415

        body = await request.json()
        name = (body.get("name") or "").strip()
        if not name:
            return JSONResponse({"error": "Укажите название ключа"}, status_code=422)
        if len(name) > _NAME_MAX:
            return JSONResponse({"error": f"Название длиннее {_NAME_MAX} символов"}, status_code=422)

        try:
            plain, data = await create_key(name)
        except RuntimeError as exc:
            return JSONResponse({"error": str(exc)}, status_code=503)
        # The plaintext key is returned only here — it is not stored anywhere.
        return JSONResponse({"ok": True, "key": plain, "api_key": data}, status_code=201)

    @staticmethod
    async def revoke_key(request: Request) -> JSONResponse:
        from sqlalchemy import select  # noqa: PLC0415

        from models.api_key import ApiKey  # noqa: PLC0415
        from models.job import async_session  # noqa: PLC0415

        body = await request.json()
        key_id = (body.get("id") or "").strip()
        if not key_id:
            return JSONResponse({"error": "id is required"}, status_code=422)
        if async_session is None:
            return JSONResponse({"error": "DB not initialised"}, status_code=503)

        async with async_session() as session:
            row = (await session.execute(
                select(ApiKey).where(ApiKey.id == key_id)
            )).scalar_one_or_none()
            if row is None:
                return JSONResponse({"error": "key not found"}, status_code=404)
            if row.revoked_at is None:
                from services.api_monitors import pause_for_key  # noqa: PLC0415

                row.revoked_at = datetime.utcnow()
                await pause_for_key(session, row.id)
                await session.commit()
                logger.info(f"API key revoked: {row.name} ({row.prefix}…)")
            data = row.to_dict()
        return JSONResponse({"ok": True, "api_key": data})

    @staticmethod
    async def delete_key(request: Request) -> JSONResponse:
        """Delete a key record. Only revoked keys can be deleted."""
        from sqlalchemy import select  # noqa: PLC0415

        from models.api_key import ApiKey  # noqa: PLC0415
        from models.job import async_session  # noqa: PLC0415

        body = await request.json()
        key_id = (body.get("id") or "").strip()
        if not key_id:
            return JSONResponse({"error": "id is required"}, status_code=422)
        if async_session is None:
            return JSONResponse({"error": "DB not initialised"}, status_code=503)

        async with async_session() as session:
            row = (await session.execute(
                select(ApiKey).where(ApiKey.id == key_id)
            )).scalar_one_or_none()
            if row is None:
                return JSONResponse({"error": "key not found"}, status_code=404)
            if row.revoked_at is None:
                return JSONResponse({"error": "Сначала отзовите ключ"}, status_code=409)
            from sqlalchemy import delete  # noqa: PLC0415

            from models.api_job import ApiJob, ApiJobItem  # noqa: PLC0415
            from services.api_monitors import delete_for_key  # noqa: PLC0415

            # Everything the key owned goes with it.
            await delete_for_key(session, row.id)
            job_ids = select(ApiJob.id).where(ApiJob.api_key_id == row.id)
            await session.execute(delete(ApiJobItem).where(ApiJobItem.job_id.in_(job_ids)))
            await session.execute(delete(ApiJob).where(ApiJob.api_key_id == row.id))
            await session.delete(row)
            await session.commit()
            logger.info(f"API key deleted: {row.name} ({row.prefix}…)")
        return JSONResponse({"ok": True})


def register_api_keys_routes(app) -> None:  # type: ignore[no-untyped-def]
    from fastapi import Depends  # noqa: PLC0415

    from admin.resources.auth import require_admin  # noqa: PLC0415
    _auth = [Depends(require_admin)]
    router = ApiKeysRouter()
    app.add_api_route("/admin/api-keys/api", router.list_keys, methods=["GET"], tags=["admin"], dependencies=_auth)
    app.add_api_route("/admin/api-keys/create", router.create_key, methods=["POST"], tags=["admin"], dependencies=_auth)
    app.add_api_route("/admin/api-keys/revoke", router.revoke_key, methods=["POST"], tags=["admin"], dependencies=_auth)
    app.add_api_route("/admin/api-keys/delete", router.delete_key, methods=["POST"], tags=["admin"], dependencies=_auth)
