#!/usr/bin/env python3
"""Smoke test for a running Grex Parser server — used by CI after `python api.py`.

Catches what unit tests miss: broken imports inside route handlers (admin
modules import lazily), template errors and routes that failed to register.
Every check is a plain GET, so it never parses a marketplace or changes data.

  * public API: /v1/health, /v1/marketplaces, /openapi.json
  * admin panel: logs in, opens every section page and every JSON list endpoint

Usage:
    python scripts/smoke_test.py                              # http://localhost:8004, admin/admin
    python scripts/smoke_test.py --base-url http://host:8004 --username u --password p

Exits non-zero and lists every failed check.
"""
from __future__ import annotations

import argparse
import sys

import httpx

PUBLIC_ENDPOINTS = ["/v1/health", "/v1/marketplaces", "/openapi.json"]

ADMIN_PAGES = [
    "/admin/",
    "/admin/targets",
    "/admin/links",
    "/admin/products",
    "/admin/monitors",
    "/admin/jobs",
    "/admin/thanks",
    "/admin/monitoring-parameters",
    "/admin/proxies",
    "/admin/users",
    "/admin/api-keys",
]

ADMIN_APIS = [
    "/admin/targets/api",
    "/admin/products/api",
    "/admin/monitors/api",
    "/admin/monitors/alerts",
    "/admin/jobs/api",
    "/admin/users/api",
    "/admin/api-keys/api",
    "/admin/proxies/api",
    "/admin/proxies/settings",
    "/admin/alert-sources/api",
    "/admin/alert-sources/settings",
]


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--base-url", default="http://localhost:8004")
    parser.add_argument("--username", default="admin")
    parser.add_argument("--password", default="admin")
    args = parser.parse_args()

    failures: list[str] = []

    def check(client: httpx.Client, path: str, expect_json: bool = False) -> None:
        try:
            r = client.get(path)
        except httpx.HTTPError as exc:
            failures.append(f"{path}: {exc}")
            print(f"FAIL {path}: {exc}")
            return
        ok = r.status_code == 200
        if ok and expect_json:
            try:
                r.json()
            except ValueError:
                ok = False
        print(f"{'ok  ' if ok else 'FAIL'} {r.status_code} {path}")
        if not ok:
            failures.append(f"{path}: HTTP {r.status_code} {r.text[:200]!r}")

    with httpx.Client(base_url=args.base_url, timeout=30) as client:
        for path in PUBLIC_ENDPOINTS:
            check(client, path, expect_json=True)

        login = client.post(
            "/admin/login",
            data={"username": args.username, "password": args.password},
            follow_redirects=False,
        )
        if login.status_code not in (302, 303):
            print(f"FAIL admin login: HTTP {login.status_code}")
            failures.append(f"admin login: HTTP {login.status_code}")
        else:
            print(f"ok   {login.status_code} admin login")
            for path in ADMIN_PAGES:
                check(client, path)
            for path in ADMIN_APIS:
                check(client, path, expect_json=True)

    if failures:
        print(f"\n{len(failures)} check(s) failed:", *failures, sep="\n  ")
        return 1
    print("\nAll smoke checks passed.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
