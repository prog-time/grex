#!/usr/bin/env python3
"""Live check of the crawlers: parse one known product per marketplace.

Marketplaces change their sites and APIs without notice (in October 2026
Wildberries started answering 403 on its JSON APIs). This script parses one
reference product per marketplace through the same path as the API and checks
that the title is not empty and the price is above zero.

Usage:
    python scripts/live_check.py                       # all marketplaces
    python scripts/live_check.py --only wildberries ozon
    python scripts/live_check.py --report report.md    # also write a Markdown report

Exit code 1 if any checked marketplace failed. Marketplaces without a reference
product are reported as skipped and do not fail the run. Run from the repo
root; the crawlers open Chromium with a window, so on a server use xvfb-run.
Used nightly by .github/workflows/live-check.yml.
"""
from __future__ import annotations

import argparse
import asyncio
import os
import sys
import time

sys.argv[0] = os.path.abspath("main.py")  # config.py resolves data/ next to argv[0]
sys.path.insert(0, os.getcwd())

# One product per marketplace. A product that goes out of stock or disappears
# turns the check red without the crawler being broken — replace it here.
PRODUCTS: dict[str, str | None] = {
    "wildberries": "https://www.wildberries.ru/catalog/273059631/detail.aspx",
    "ozon": "https://ozon.ru/product/shumin-maska-dlya-sna-chernaya-1563221610",
    "yandex_market": "https://market.yandex.ru/card/espander-dlya-paltsev-ruk-kistevoy-95-kg-s-krepleniyem-na-zapyastye-tsvet---chernyy/103757317666",  # noqa: E501
    "megamarket": "https://megamarket.ru/catalog/details/puhovik-detskiy-poivre-blanc-w21-1201-jrgl-2122-mega-pink-128-600011555038",  # noqa: E501
    "mm": "https://mm.ru/product/zaschitnoe-steklo-dlya-126042?skuId=12929772",
    "cdek": "https://cdek.shopping/p/11076395/",
    # No reference product yet — add a real product URL to enable the check.
    "citilink": None,
    "vseinstrumenti": None,
    "bigam": None,
}

TIMEOUT_SECONDS = 240


async def check(marketplace: str, url: str | None) -> dict:
    if not url:
        return {"marketplace": marketplace, "status": "skipped", "detail": "no reference product", "seconds": 0.0}
    from services.parsing import ParsingService

    started = time.monotonic()
    try:
        card = await asyncio.wait_for(ParsingService().parse_product(url), TIMEOUT_SECONDS)
    except Exception as exc:  # noqa: BLE001 — every failure is a result here
        detail = getattr(exc, "detail", None) or str(exc) or type(exc).__name__
        return {"marketplace": marketplace, "status": "failed", "detail": str(detail)[:300],
                "seconds": time.monotonic() - started}
    problems = []
    if not (card.title or "").strip():
        problems.append("empty title")
    if not card.current_price or card.current_price <= 0:
        problems.append(f"price {card.current_price}")
    return {
        "marketplace": marketplace,
        "status": "failed" if problems else "ok",
        "detail": ", ".join(problems) or f"{card.title[:60]} — {card.current_price}",
        "seconds": time.monotonic() - started,
    }


def markdown(results: list[dict]) -> str:
    icon = {"ok": "✅", "failed": "❌", "skipped": "⏭️"}
    rows = [f"| {r['marketplace']} | {icon[r['status']]} {r['status']} | {r['seconds']:.0f}s | {r['detail']} |"
            for r in results]
    return "\n".join(["| Marketplace | Result | Time | Detail |", "|---|---|---|---|", *rows])


async def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--only", nargs="*", help="marketplaces to check (default: all)")
    parser.add_argument("--report", help="write a Markdown report to this file")
    args = parser.parse_args()

    unknown = set(args.only or []) - set(PRODUCTS)
    if unknown:
        parser.error(f"unknown marketplace(s): {', '.join(sorted(unknown))}")
    selected = [m for m in PRODUCTS if not args.only or m in args.only]

    results = []
    for marketplace in selected:  # one at a time: the crawlers share proxies and browser slots
        result = await check(marketplace, PRODUCTS[marketplace])
        print(f"{result['status']:8} {marketplace:15} {result['seconds']:6.0f}s  {result['detail']}", flush=True)
        results.append(result)

    report = markdown(results)
    if args.report:
        with open(args.report, "w", encoding="utf-8") as f:
            f.write(report + "\n")
    failed = [r["marketplace"] for r in results if r["status"] == "failed"]
    print(f"\n{len(results) - len(failed)} ok/skipped, {len(failed)} failed" + (f": {', '.join(failed)}" if failed else ""))
    return 1 if failed else 0


if __name__ == "__main__":  # the crawlers start browsers in subprocesses (spawn)
    sys.exit(asyncio.run(main()))
