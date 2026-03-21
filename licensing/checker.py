"""License checker — this file is obfuscated in production builds (PyArmor).
Do NOT ship the unobfuscated version to clients.
"""
from __future__ import annotations

import json
import hmac
import hashlib
import uuid
import datetime
import urllib.request
from pathlib import Path

# Must match register.py — never change after first release.
_SECRET = "KBSox0KKob3hiCwAkdWfBzdYFRS8gIeFvWrO8dn5ipvttuW0jSdaxT3BlmmmFDHZ"

# Hosted licenses index — required (app always has internet).
# Format: {"<key>": {"status": "active"|"blocked"|"pending", "updates_until": "YYYY-MM-DD"}}
_ONLINE_CHECK_URL = "https://raw.githubusercontent.com/prog-time/licenses/main/grex/licenses.json"

_LICENSE_PATH = Path("license.json")


def _get_mac() -> str:
    mac_int = uuid.getnode()
    return ":".join(f"{(mac_int >> i) & 0xff:02x}" for i in range(40, -1, -8))


def _compute_key(mac: str) -> str:
    return hmac.new(_SECRET.encode(), mac.encode(), hashlib.sha256).hexdigest()


def _read_license_file() -> dict:
    if not _LICENSE_PATH.exists():
        _abort("Файл license.json не найден. Запустите: python register.py")
    try:
        return json.loads(_LICENSE_PATH.read_text(encoding="utf-8"))
    except Exception:
        _abort("Файл license.json повреждён или содержит некорректный JSON.")


def _fetch_license_entry(key: str) -> dict:
    try:
        with urllib.request.urlopen(_ONLINE_CHECK_URL, timeout=5) as resp:
            data = json.loads(resp.read().decode())
        return data.get(key)
    except Exception:
        _abort("Не удалось проверить лицензию. Проверьте интернет-соединение.")


def check_license() -> dict:
    """Run all license checks. Calls SystemExit(1) on any failure."""

    # 1. Read local license.json
    lic = _read_license_file()

    if "key" not in lic:
        _abort("Файл license.json неполный. Запустите: python register.py")

    # 2. Verify key matches this machine's MAC (prevents copying license to another machine)
    mac = _get_mac()
    expected_key = _compute_key(mac)
    if not hmac.compare_digest(expected_key, lic["key"]):
        _abort("Лицензия выдана для другого устройства. Обратитесь к поставщику.")

    # 3. Online activation check (mandatory — app requires internet)
    entry = _fetch_license_entry(lic["key"])

    if entry is None:
        _abort("Лицензия не активирована. Запустите register.py и отправьте ключ разработчику.")

    status = entry.get("status")

    if status == "blocked":
        _abort("Лицензия заблокирована. Обратитесь к поставщику.")

    if status != "active":
        _abort(f"Лицензия не активна (статус: {status}). Обратитесь к поставщику.")

    return {**lic, **entry}


def check_updates_allowed() -> bool:
    """Returns True if the updates subscription is still active."""
    try:
        lic = _read_license_file()
        entry = _fetch_license_entry(lic["key"])
        if not entry:
            return False
        until = datetime.date.fromisoformat(entry["updates_until"])
        return datetime.date.today() <= until
    except Exception:
        return False


def _abort(message: str) -> None:
    print(f"\n  ОШИБКА ЛИЦЕНЗИИ: {message}\n")
    raise SystemExit(1)
