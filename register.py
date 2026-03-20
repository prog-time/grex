#!/usr/bin/env python3
"""
Скрипт регистрации — запускается клиентом один раз.

Генерирует license.json с ключом, привязанным к MAC-адресу устройства.
Клиент присылает ключ разработчику для активации.

Использование:
    python register.py
"""
import hmac
import hashlib
import json
import uuid
from pathlib import Path

_SECRET = "KBSox0KKob3hiCwAkdWfBzdYFRS8gIeFvWrO8dn5ipvttuW0jSdaxT3BlmmmFDHZ"
_LICENSE_PATH = Path("license.json")


def _get_mac() -> str:
    mac_int = uuid.getnode()
    return ":".join(f"{(mac_int >> i) & 0xff:02x}" for i in range(40, -1, -8))


def _compute_key(mac: str) -> str:
    return hmac.new(_SECRET.encode(), mac.encode(), hashlib.sha256).hexdigest()


def main() -> None:
    if _LICENSE_PATH.exists():
        existing = json.loads(_LICENSE_PATH.read_text(encoding="utf-8"))
        key = existing.get("key", "")
        print("\n  Файл license.json уже существует.")
        print(f"\n  Ваш ключ активации:\n\n      {key}\n")
        print("  Если лицензия ещё не активирована — отправьте ключ разработчику.")
        return

    mac = _get_mac()
    key = _compute_key(mac)

    _LICENSE_PATH.write_text(
        json.dumps({"key": key}, indent=2, ensure_ascii=False),
        encoding="utf-8",
    )

    print("")
    print("  Регистрация Grex Parser")
    print("")
    print(f"  Ваш ключ:  {key}")
    print("")
    print("  Отправьте ключ разработчику для активации.")
    print("  После активации запустите: ./start.sh")
    print("")


if __name__ == "__main__":
    main()
