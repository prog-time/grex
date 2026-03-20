"""License checker — this file is obfuscated in production builds (PyArmor).
Do NOT ship the unobfuscated version to clients.
"""
from __future__ import annotations

import json
import hmac
import hashlib
import socket
import datetime
import urllib.request
from pathlib import Path

# Secret key — must match generate.py. Change before first release.
_SECRET = "mp-parser-secret-CHANGE-ME-2024"

# Hosted licenses index (optional online check).
# Host a raw JSON file: {"<key>": {"status": "active"|"blocked"}}
_ONLINE_CHECK_URL = ""  # e.g. "https://raw.githubusercontent.com/YOUR_ORG/YOUR_REPO/main/licenses.json"

_LICENSE_PATH = Path("license.json")


def _compute_sig(data: dict) -> str:
    payload = "|".join([
        data["key"],
        data["expires_at"],
        data["updates_until"],
        data["server_id"],
    ])
    return hmac.new(_SECRET.encode(), payload.encode(), hashlib.sha256).hexdigest()


def check_license() -> dict:
    """Run all license checks. Calls SystemExit(1) on any failure."""

    if not _LICENSE_PATH.exists():
        _abort("Файл license.json не найден. Обратитесь к поставщику.")

    try:
        lic = json.loads(_LICENSE_PATH.read_text(encoding="utf-8"))
    except Exception:
        _abort("Файл license.json повреждён или содержит некорректный JSON.")

    for field in ("key", "expires_at", "updates_until", "server_id", "sig"):
        if field not in lic:
            _abort(f"Файл license.json неполный: отсутствует поле '{field}'.")

    # 1. Signature integrity (prevents manual editing)
    if not hmac.compare_digest(_compute_sig(lic), lic["sig"]):
        _abort("Лицензия повреждена или изменена вручную. Обратитесь к поставщику.")

    # 2. Expiry date
    try:
        expires = datetime.date.fromisoformat(lic["expires_at"])
    except ValueError:
        _abort("Некорректный формат даты expires_at в license.json (ожидается YYYY-MM-DD).")

    if datetime.date.today() > expires:
        _abort(
            f"Срок действия лицензии истёк {lic['expires_at']}. "
            "Продлите лицензию или обратитесь к поставщику."
        )

    # 3. Server binding
    current = socket.gethostname()
    if current != lic["server_id"]:
        _abort(
            f"Лицензия выдана для сервера '{lic['server_id']}', "
            f"текущий хост: '{current}'. "
            "Обратитесь к поставщику для переноса лицензии."
        )

    # 4. Online block-list (non-fatal if offline)
    if _ONLINE_CHECK_URL:
        _check_online(lic["key"])

    return lic


def check_updates_allowed() -> bool:
    """Returns True if the updates subscription is still active."""
    try:
        lic = json.loads(_LICENSE_PATH.read_text(encoding="utf-8"))
        until = datetime.date.fromisoformat(lic["updates_until"])
        return datetime.date.today() <= until
    except Exception:
        return False


def _check_online(key: str) -> None:
    try:
        with urllib.request.urlopen(_ONLINE_CHECK_URL, timeout=4) as resp:
            data = json.loads(resp.read().decode())
        entry = data.get(key)
        if entry is None:
            return  # unknown key — allow (key not yet in the list)
        if entry.get("status") == "blocked":
            _abort("Лицензия заблокирована. Обратитесь к поставщику.")
    except SystemExit:
        raise
    except Exception:
        pass  # offline / unreachable — fall back to local check only


def _abort(message: str) -> None:
    print("\n  ╔══ ОШИБКА ЛИЦЕНЗИИ ══════════════════════════════════╗")
    print(f"  ║  {message}")
    print("  ╚═════════════════════════════════════════════════════╝\n")
    raise SystemExit(1)
