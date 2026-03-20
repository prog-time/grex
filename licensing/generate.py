#!/usr/bin/env python3
"""
Управление лицензиями — DEVELOPER USE ONLY.

!! DO NOT ship this file to clients !!

Используется для активации клиентских ключей в licenses.json.

Использование:

    # Активировать нового клиента (клиент прислал ключ из register.py)
    python licensing/generate.py activate \
        --key "abc123..." \
        --customer "ООО Ромашка" \
        --updates-until "2027-03-21"

    # Заблокировать клиента
    python licensing/generate.py block --key "abc123..."

    # Список всех лицензий
    python licensing/generate.py list

Файл licenses.json нужно закоммитить в репозиторий после изменений:
    git add licenses.json && git commit -m "activate: ООО Ромашка"
"""
from __future__ import annotations

import argparse
import json
from pathlib import Path

_LICENSES_PATH = Path("licenses.json")


def _load() -> dict:
    if _LICENSES_PATH.exists():
        return json.loads(_LICENSES_PATH.read_text(encoding="utf-8"))
    return {}


def _save(data: dict) -> None:
    _LICENSES_PATH.write_text(
        json.dumps(data, indent=2, ensure_ascii=False),
        encoding="utf-8",
    )
    print(f"  ✓ Сохранено в {_LICENSES_PATH}")


def cmd_activate(key: str, customer: str, updates_until: str) -> None:
    data = _load()
    data[key] = {
        "status": "active",
        "customer": customer,
        "updates_until": updates_until,
    }
    _save(data)
    print(f"  ✓ Активирован: {customer} (updates_until: {updates_until})")
    print("\n  Не забудьте закоммитить licenses.json в репозиторий!")


def cmd_block(key: str) -> None:
    data = _load()
    if key not in data:
        print(f"  ✗ Ключ не найден: {key}")
        return
    data[key]["status"] = "blocked"
    _save(data)
    print(f"  ✓ Заблокирован: {data[key].get('customer', key)}")
    print("\n  Не забудьте закоммитить licenses.json в репозиторий!")


def cmd_list() -> None:
    data = _load()
    if not data:
        print("  Нет активных лицензий.")
        return
    print(f"\n  {'Ключ':>12}  {'Статус':<10}  {'Клиент':<25}  {'Обновления до'}")
    print(f"  {'-'*12}  {'-'*10}  {'-'*25}  {'-'*13}")
    for key, entry in data.items():
        print(
            f"  ...{key[-9:]}  "
            f"{entry.get('status', '?'):<10}  "
            f"{entry.get('customer', '?'):<25}  "
            f"{entry.get('updates_until', '?')}"
        )
    print()


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="Manage client licenses")
    sub = parser.add_subparsers(dest="command", required=True)

    p_activate = sub.add_parser("activate", help="Activate a new client key")
    p_activate.add_argument("--key", required=True, help="Key from client's register.py")
    p_activate.add_argument("--customer", required=True, help="Customer name")
    p_activate.add_argument("--updates-until", required=True, help="Updates subscription end YYYY-MM-DD")

    p_block = sub.add_parser("block", help="Block a client key")
    p_block.add_argument("--key", required=True, help="Key to block")

    sub.add_parser("list", help="List all licenses")

    args = parser.parse_args()

    if args.command == "activate":
        cmd_activate(args.key, args.customer, args.updates_until)
    elif args.command == "block":
        cmd_block(args.key)
    elif args.command == "list":
        cmd_list()
