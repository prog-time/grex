#!/usr/bin/env python3
"""
License generator — DEVELOPER USE ONLY.

!! DO NOT ship this file to clients !!
!! Add to .gitignore or exclude from release archives !!

Usage:
    python licensing/generate.py \
        --customer "ООО Ромашка" \
        --server-id "parser-server-01" \
        --expires-at "2027-03-20" \
        --updates-until "2026-03-20"

    python licensing/generate.py \
        --customer "ИП Иванов" \
        --server-id "ubuntu-vps" \
        --expires-at "2030-01-01" \
        --updates-until "2026-12-31" \
        --output /tmp/license_ivanov.json
"""
from __future__ import annotations

import argparse
import json
import hmac
import hashlib
import uuid
from pathlib import Path

# Must match _SECRET in checker.py — change both at the same time.
_SECRET = "KBSox0KKob3hiCwAkdWfBzdYFRS8gIeFvWrO8dn5ipvttuW0jSdaxT3BlmmmFDHZ"


def _compute_sig(data: dict) -> str:
    payload = "|".join([
        data["key"],
        data["expires_at"],
        data["updates_until"],
        data["server_id"],
    ])
    return hmac.new(_SECRET.encode(), payload.encode(), hashlib.sha256).hexdigest()


def generate_license(
    customer: str,
    server_id: str,
    expires_at: str,
    updates_until: str,
    output_path: str | None = None,
) -> dict:
    data: dict = {
        "key": str(uuid.uuid4()),
        "customer": customer,
        "server_id": server_id,
        "expires_at": expires_at,
        "updates_until": updates_until,
    }
    data["sig"] = _compute_sig(data)

    out = Path(output_path or f"license_{customer.replace(' ', '_')}.json")
    out.write_text(json.dumps(data, indent=2, ensure_ascii=False), encoding="utf-8")

    print(f"\n  License saved → {out}\n")
    print(json.dumps(data, indent=2, ensure_ascii=False))
    return data


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="Generate a client license")
    parser.add_argument("--customer", required=True, help="Customer name")
    parser.add_argument("--server-id", required=True, help="Target server hostname")
    parser.add_argument("--expires-at", required=True, help="License expiry YYYY-MM-DD")
    parser.add_argument("--updates-until", required=True, help="Updates subscription end YYYY-MM-DD")
    parser.add_argument("--output", help="Output path (default: license_<customer>.json)")
    args = parser.parse_args()

    generate_license(
        customer=args.customer,
        server_id=args.server_id,
        expires_at=args.expires_at,
        updates_until=args.updates_until,
        output_path=args.output,
    )
