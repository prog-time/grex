"""API key management for the public REST API (``X-API-Key`` header).

Keys are generated server-side, shown to the admin once and stored only as a
SHA-256 hash. Keys are long random tokens, so a fast unsalted hash is enough —
brute-forcing 256 bits of entropy is not feasible.
"""
from __future__ import annotations

import hashlib
import secrets
from datetime import datetime, timedelta

from loguru import logger

KEY_PREFIX = "grx_"
# The key shipped in settings.yml.example — public, so never safe to use.
DEFAULT_LEGACY_KEY = "4353456dfvbdfg34"
LEGACY_KEY_NAME = "Ключ из settings.yml"
# last_used_at is refreshed at most this often to avoid a DB write per request.
_LAST_USED_RESOLUTION = timedelta(minutes=1)


def generate_key() -> str:
    return KEY_PREFIX + secrets.token_urlsafe(32)


def hash_key(plain: str) -> str:
    return hashlib.sha256(plain.encode("utf-8")).hexdigest()


def _display_prefix(plain: str) -> str:
    return plain[: len(KEY_PREFIX) + 4] if plain.startswith(KEY_PREFIX) else plain[:4]


async def _insert(session, name: str, plain: str):  # type: ignore[no-untyped-def]
    from models.api_key import ApiKey  # noqa: PLC0415

    row = ApiKey(
        name=name,
        key_hash=hash_key(plain),
        prefix=_display_prefix(plain),
        created_at=datetime.utcnow(),
    )
    session.add(row)
    await session.commit()
    return row


async def create_key(name: str) -> tuple[str, dict]:
    """Create a key named *name*; return ``(plaintext, row dict)``."""
    from models.job import async_session  # noqa: PLC0415

    if async_session is None:
        raise RuntimeError("DB not initialised")
    plain = generate_key()
    async with async_session() as session:
        row = await _insert(session, name, plain)
    logger.info(f"API key created: {name} ({row.prefix}…)")
    return plain, row.to_dict()


class KeyStorageUnavailable(RuntimeError):
    """The DB holding API keys is not initialised — keys can't be checked."""


async def verify_key(plain: str) -> str | None:
    """Return the id of the active key *plain*, or None if it is unknown or revoked.

    Raises :class:`KeyStorageUnavailable` when the DB is down — callers must
    fail closed rather than fall back to another key source.
    """
    from sqlalchemy import select  # noqa: PLC0415

    from models.api_key import ApiKey  # noqa: PLC0415
    from models.job import async_session  # noqa: PLC0415

    if async_session is None:
        raise KeyStorageUnavailable("DB not initialised")
    async with async_session() as session:
        row = (await session.execute(
            select(ApiKey).where(ApiKey.key_hash == hash_key(plain))
        )).scalar_one_or_none()
        if row is None or row.revoked_at is not None:
            return None
        now = datetime.utcnow()
        if row.last_used_at is None or now - row.last_used_at >= _LAST_USED_RESOLUTION:
            row.last_used_at = now
            await session.commit()
        return row.id


async def import_legacy_key(plain: str | None) -> None:
    """Seed the table with ``security.api_key`` from settings.yml, once.

    Keeps existing integrations working after the switch to DB-stored keys.
    A key whose hash is already present (active or revoked) is never
    re-imported, so revoking it in the admin panel sticks.
    """
    from sqlalchemy import select  # noqa: PLC0415

    from models.api_key import ApiKey  # noqa: PLC0415
    from models.job import async_session  # noqa: PLC0415

    if not plain or async_session is None:
        return
    async with async_session() as session:
        exists = (await session.execute(
            select(ApiKey.id).where(ApiKey.key_hash == hash_key(plain))
        )).first()
        if exists is None:
            await _insert(session, LEGACY_KEY_NAME, plain)
            logger.info("Imported security.api_key from settings.yml into API keys")


async def warn_if_default_key_active() -> None:
    from sqlalchemy import select  # noqa: PLC0415

    from models.api_key import ApiKey  # noqa: PLC0415
    from models.job import async_session  # noqa: PLC0415

    if async_session is None:
        return
    async with async_session() as session:
        active = (await session.execute(
            select(ApiKey.id).where(
                ApiKey.key_hash == hash_key(DEFAULT_LEGACY_KEY), ApiKey.revoked_at.is_(None)
            )
        )).first()
    if active is not None:
        logger.warning(
            "SECURITY: the default API key from settings.yml.example is active"
            " — revoke it in the admin panel (Настройки → API-ключи) before going to production!"
        )


def is_default_key_hash(key_hash: str) -> bool:
    return key_hash == hash_key(DEFAULT_LEGACY_KEY)
