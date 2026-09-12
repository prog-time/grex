"""Symmetric encryption for secrets stored at rest.

Secrets (e.g. the Telegram bot token) are encrypted with Fernet before being
written to ``data/settings.yml`` so they never appear there in plaintext. The
key is derived from ``admin.secret_key`` — there is no separate key to manage,
but rotating ``admin.secret_key`` invalidates previously-stored secrets (they
must be re-entered).
"""
from __future__ import annotations

import base64
import hashlib

from loguru import logger


def _fernet():
    from cryptography.fernet import Fernet  # noqa: PLC0415

    from config import settings  # noqa: PLC0415
    secret = (settings.get("admin", {}).get("secret_key") or "grex-default-secret").encode("utf-8")
    key = base64.urlsafe_b64encode(hashlib.sha256(secret).digest())
    return Fernet(key)


def encrypt_secret(plain: str) -> str:
    """Return a Fernet token for *plain*, or "" on empty input / failure."""
    if not plain:
        return ""
    try:
        return _fernet().encrypt(plain.encode("utf-8")).decode("ascii")
    except Exception as exc:  # noqa: BLE001
        logger.error(f"encrypt_secret failed: {exc}")
        return ""


def decrypt_secret(token: str) -> str:
    """Return the plaintext for a Fernet *token*, or "" when missing/invalid."""
    if not token:
        return ""
    try:
        return _fernet().decrypt(token.encode("ascii")).decode("utf-8")
    except Exception as exc:  # noqa: BLE001
        logger.warning(f"decrypt_secret failed: {exc}")
        return ""
